"""CPU document pass: identity -> table protection/roles -> one shared flow."""
from __future__ import annotations

import unicodedata

from ocr_artifacts.digest import sha256_json_unicode
from ocr_document.region_tree import LayoutItem, infer_region_tree, METHOD
from ocr_document.resolve_document_furniture import resolve_document_furniture
from ocr_document.ids import page_items
from ocr_document.source_identity import assign_source_ids
from ocr_document.reading_order_graph import Edge, solve_order
from ocr_contracts.text_block import TextBlock
from ocr_contracts.evidence_block import EvidenceBlock
from ocr_contracts.region import Region, BoundingBox, ContentType
from ocr_contracts.geometry import block_is_covered, is_oriented_line_candidate, placed_line_bbox, placed_line_tokens
from ocr_document.source_identity import valid_bbox
from ocr_text.numeric_guard import preserves_numeric_evidence

ALGORITHM_VERSION = "region-tree-graph-v1.5"
RECONCILIATION_METHOD = "exact-source-occurrence-v2"
GLYPH_RECONCILIATION_METHOD = "exact-source-occurrence-glyph-v3"
TOKEN_RECONCILIATION_METHOD = "exact-source-occurrence-token-v4"


def _source_tokens(source):
    tokens = _exact_tokens(source["text"])
    # A list marker is presentation, never a numeric sign. Retain it in the
    # frozen source evidence and record its exclusion in the disposition.
    marker = (source.get("block_type") in ("list", "list_item") and len(tokens) > 1
              and tokens[0] in ("-", "–", "•", "●", "▪") and tokens[1][:1].isalpha())
    if marker:
        marker = preserves_numeric_evidence(source["text"], " ".join(tokens[1:]))
    return tokens[1:] if marker else tokens

def _exact_tokens(text):
    """Normalize transcription encoding/spacing, preserving case and numbers."""
    return unicodedata.normalize("NFC", text).split()


def _table_rows(table):
    rows = {}
    for index, cell in sorted(enumerate(table.cells), key=lambda item: (item[1].row_idx, item[1].col_idx)):
        rows.setdefault(cell.row_idx, []).extend(
            (token, index, offset, cell) for offset, token in enumerate(_exact_tokens(cell.text)))
    return rows


def _cell_at_source(source_box, cell_box):
    """A same-row cell must overlap the source's horizontal text extent."""
    if not valid_bbox(source_box) or not valid_bbox(cell_box):
        return False
    x_overlap = min(source_box[2], cell_box[2]) - max(source_box[0], cell_box[0])
    y_overlap = min(source_box[3], cell_box[3]) - max(source_box[1], cell_box[1])
    return (x_overlap > 0 and y_overlap > 0
            and y_overlap / min(source_box[3] - source_box[1], cell_box[3] - cell_box[1]) >= .5)


PLACED_BOX_TOLERANCE_PT = 0.004


def _cell_contains_placed_line(source_box, cell_box, *, eps: float = PLACED_BOX_TOLERANCE_PT):
    """Compare hidden-line bounds against an owning cell with float32 slack.

    Docling and PyMuPDF can report the same hidden-line edges with float32 or
    inverse-rotation differences. Compare with a symmetric tolerance instead of
    rounding each edge independently across half-cent boundaries.
    Original boxes stay immutable; ambiguity still abstains.
    """
    if not valid_bbox(source_box) or not valid_bbox(cell_box):
        return False
    return (cell_box[0] - eps <= source_box[0] < source_box[2] <= cell_box[2] + eps
            and cell_box[1] - eps <= source_box[1] < source_box[3] <= cell_box[3] + eps)


def _table_occurrences(source, table, rows, used, *, placement_bbox=None, placement_tokens=None):
    """Find unconsumed exact occurrences, never joining separate table rows.

    Missing cell geometry permits only a unique textual occurrence (the caller
    rejects ambiguity). When geometry is available it must agree with the
    source, including for each cell of an OCR line spanning several columns.
    """
    tokens = _source_tokens(source)
    if not tokens or not block_is_covered(source["bbox"], [table]):
        return []
    has_cell_geometry = any(cell.bbox for cell in table.cells)
    vertical = is_oriented_line_candidate(source.get("recognition_provenance", {}))
    located_box = source["bbox"] if placement_bbox is None else placement_bbox
    matches = []
    for row in rows.values():
        for start in range(len(row) - len(tokens) + 1):
            occurrence = row[start:start + len(tokens)]
            if [item[0] for item in occurrence] != tokens:
                continue
            keys = {(item[1], item[2]) for item in occurrence}
            if keys & used:
                continue
            if placement_tokens is not None:
                if len({item[1] for item in occurrence}) < 2:
                    continue  # Keep the established whole-line path for one cell.
                located = placement_tokens[len(placement_tokens) - len(tokens):]
                if len(located) != len(tokens) or any(
                        token["text"] != word or not _cell_contains_placed_line(token["bbox"], item[3].bbox)
                        for token, word, item in zip(located, tokens, occurrence)):
                    continue
                matches.append((keys, [{"cell_index": item[1], "row_idx": item[3].row_idx,
                                        "col_idx": item[3].col_idx, "token_index": item[2]} for item in occurrence]))
                continue
            if vertical:
                if len({item[1] for item in occurrence}) != 1:
                    continue
                cell = occurrence[0][3]
                contained = (_cell_contains_placed_line(located_box, cell.bbox) if placement_bbox is not None
                             else block_is_covered(located_box, [cell], threshold=1.0))
                if not contained:
                    continue
            if has_cell_geometry and any(not _cell_at_source(located_box, item[3].bbox)
                                         for item in occurrence):
                continue
            matches.append((keys, [{"cell_index": item[1], "row_idx": item[3].row_idx,
                                    "col_idx": item[3].col_idx, "token_index": item[2]}
                                   for item in occurrence]))
    return matches


def _reconcile_evidence(page):
    """Require text as well as geometry before considering table replacement.

    Unmapped sources return to the visible body with a review reason. The old
    geometric table filter must not erase business text without an audit.
    """
    evidence = page.metadata.get("text_evidence", {}).get("items", [])
    if not evidence:
        page.metadata["source_dispositions"] = {}
        return
    if page.tables:
        page.metadata["source_reconciliation_method"] = RECONCILIATION_METHOD
    else:
        page.metadata.pop("source_reconciliation_method", None)
    # Revisit only blocks restored by an earlier reconciliation. Other visible
    # occurrences still belong to the body and are never globally deduplicated.
    frozen_ids = {source["source_id"] for source in evidence}
    restored_codes = {"TABLE_SOURCE_UNMAPPED", "TABLE_FURNITURE_CONFLICT"}
    revisited = {b.source_id: b for b in page.blocks
                 if b.source_id in frozen_ids and restored_codes.intersection(b.review_reason_codes)}
    page.blocks = [b for b in page.blocks if b.source_id not in revisited]
    restored_evidence_ids = {f"restored:{sid}" for sid in revisited}
    page.evidence_blocks = [b for b in page.evidence_blocks
                            if b.block_id not in restored_evidence_ids]
    page.quality.total_blocks = max(0, page.quality.total_blocks - len(revisited))
    page.quality.review_required_count = max(0, page.quality.review_required_count - len(revisited))
    surviving = {b.source_id for b in page.blocks if b.source_id}
    dispositions = {}
    table_rows = [_table_rows(table) for table in page.tables]
    consumed = [set() for _ in page.tables]
    source_ids = {source["source_id"] for source in evidence}
    oriented_placements = {
        item.get("source_id") for item in page.metadata.get("ocr_structure_bridge", {}).get("orientation_placements", [])
        if item.get("status") == "PLACED_ORIENTATION_HYPOTHESIS"
    }
    placement_records = {}
    for record in page.metadata.get("ocr_structure_bridge", {}).get("orientation_placements", []):
        placement_records.setdefault(record.get("source_id"), []).append(record)
    for table in page.tables:
        # Recompute this pass's claims instead of retaining stale ownership
        # after table contents or source dispositions change.
        table.provenance["derived_from_source_ids"] = [
            sid for sid in table.provenance.get("derived_from_source_ids", []) if sid not in source_ids]
        table.provenance["source_occurrence_mapping"] = {
            sid: mapping for sid, mapping in table.provenance.get("source_occurrence_mapping", {}).items()
            if sid not in source_ids}
    for source in evidence:
        sid = source["source_id"]
        if sid in surviving:
            dispositions[sid] = {"status": "VISIBLE_OR_FURNITURE"}
            continue
        current = revisited[source["source_id"]].to_dict() if source["source_id"] in revisited else source
        vertical = is_oriented_line_candidate(current.get("recognition_provenance", {}))
        records = placement_records.get(sid, [])
        geometry = placed_line_bbox(current, records[0]) if len(records) == 1 else None
        tokens = placed_line_tokens(current, records[0]) if len(records) == 1 else None
        owners = [(index, keys, mapping, False) for index, table in enumerate(page.tables)
                  if not vertical or sid in oriented_placements
                  for keys, mapping in _table_occurrences(current, table, table_rows[index], consumed[index],
                                                         placement_bbox=geometry)]
        if tokens is not None:
            owners.extend((index, keys, mapping, True) for index, table in enumerate(page.tables)
                          for keys, mapping in _table_occurrences(current, table, table_rows[index], consumed[index],
                                                                 placement_tokens=tokens))
        # V8-080: a heuristic table that duplicates a model table now matches
        # the same source lines once its cells carry real boxes. The model
        # table keeps ownership; any other ambiguity still abstains.
        preferred = False
        if len(owners) > 1:
            model_owners = [owner for owner in owners if page.tables[owner[0]].engine != "heuristic"]
            if len(model_owners) == 1:
                owners, preferred = model_owners, True
        formula_owners = [formula for formula in page.formulas
                          if not vertical and sid in formula.provenance.get("derived_from_source_ids", [])
                          and block_is_covered(source["bbox"], [formula])]
        if len(formula_owners) == 1 and not owners and not current.get("protected_from_furniture"):
            dispositions[sid] = {"status": "REPLACED_BY_FORMULA", "formula_id": formula_owners[0].formula_id,
                                 "reason": "DOCLING_FORMULA_ABSORPTION"}
        elif len(owners) == 1 and not formula_owners and not current.get("protected_from_furniture"):
            table_index, keys, mapping, token_geometry = owners[0]
            owner = page.tables[table_index]
            consumed[table_index].update(keys)
            owner.provenance["source_occurrence_mapping"][sid] = mapping
            parents = owner.provenance.setdefault("derived_from_source_ids", [])
            if sid not in parents:
                parents.append(sid)
            dispositions[sid] = {"status": "REPLACED_BY_TABLE", "table_id": owner.table_id,
                                 "reason": "GEOMETRY_AND_EXACT_TEXT_OCCURRENCE",
                                 "excluded_presentation_tokens": _exact_tokens(current["text"])[:
                                     len(_exact_tokens(current["text"])) - len(_source_tokens(current))]}
            if preferred:
                dispositions[sid]["owner_choice"] = "MODEL_TABLE_OVER_HEURISTIC_OVERLAP"
            if geometry is not None:
                dispositions[sid].update(reason="PLACED_GLYPHS_AND_EXACT_TEXT_OCCURRENCE",
                                         placement_geometry=dict(records[0]), occurrence_mapping=mapping)
                if token_geometry:
                    dispositions[sid].update(reason="PLACED_TOKENS_AND_EXACT_TEXT_OCCURRENCE",
                                             placement_granularity="TOKEN")
                    page.metadata["source_reconciliation_method"] = TOKEN_RECONCILIATION_METHOD
                elif page.metadata["source_reconciliation_method"] != TOKEN_RECONCILIATION_METHOD:
                    page.metadata["source_reconciliation_method"] = GLYPH_RECONCILIATION_METHOD
        else:
            restored = TextBlock.model_validate(current)
            restored.review_reason_codes = sorted(set(restored.review_reason_codes + ["TABLE_FURNITURE_CONFLICT" if restored.protected_from_furniture else "TABLE_SOURCE_UNMAPPED"]))
            page.blocks.append(restored)
            region_id = restored.region_id
            if region_id not in {r.region_id for r in page.regions}:
                region_id = f"p{page.page_num}_restored_{sid.rsplit(':', 1)[-1]}"
                box = restored.bbox if valid_bbox(restored.bbox) else [0, 0, page.width, page.height]
                page.regions.append(Region(region_id=region_id,
                    bbox=BoundingBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
                    content_type=ContentType.TEXT, status="NEEDS_REVIEW", orientation_status="UNCERTAIN",
                    failure_reason="Source text not faithfully represented by table cells"))
            restored.region_id = region_id
            page.evidence_blocks.append(EvidenceBlock(
                page=page.page_num, block_id=f"restored:{sid}", region_id=region_id,
                bbox=restored.bbox, bbox_original=restored.bbox, bbox_normalized=restored.bbox,
                text=restored.text, source="native" if restored.source == "native" else "ocr",
                confidence=restored.confidence, confidence_source=restored.confidence_source,
                quality_score=restored.quality_score, orientation=0, status="UNRESOLVED",
                failure_reason="TABLE_SOURCE_UNMAPPED", block_type=restored.block_type))
            page.quality.total_blocks += 1
            page.quality.review_required_count += 1
            surviving.add(sid)
            dispositions[sid] = {"status": "KEPT_FOR_REVIEW", "reason": restored.review_reason_codes[-1]}
    page.metadata["source_dispositions"] = dispositions


def resolve_document_layout(doc, settings=None):
    from ocr_runtime.config import document_layout_settings
    settings = document_layout_settings({"document_layout": {"method": METHOD, **(settings or {})}})
    if settings["method"] != METHOD:
        raise ValueError("The document pass requires region_tree_graph_v1; legacy uses original page evidence")
    for page in doc.pages:
        _reconcile_evidence(page)
        logical = page.metadata.setdefault("logical_page_id", f"p{page.page_num:04d}")
        page.metadata.setdefault("physical_page_id", f"p{page.page_num:04d}")
        items = page_items(doc.file_hash, page, ignore_flow=True)
        records = []
        for item in items:
            if item.kind == "b":
                text = item.block.text if item.block.text_raw is None else item.block.text_raw
                producer = item.block.producer_ref or ""
            elif item.kind == "t":
                text = " | ".join(c.text for c in sorted(item.table.cells, key=lambda c: (c.row_idx, c.col_idx)))
                producer = item.table.provenance.get("producer_ref", "")
            elif item.kind == "f":
                text, producer = item.formula.latex, item.formula.provenance.get("producer_ref", "")
            else:
                text, producer = "", item.region.asset or ""
            # Preserve full geometry for canonical ties; exported boxes are rounded.
            box = (item.block.bbox if item.block is not None else item.table.bbox if item.table is not None
                   else item.formula.bbox if item.formula is not None else
                   [item.region.bbox.x0, item.region.bbox.y0, item.region.bbox.x1, item.region.bbox.y1])
            records.append({"ref": item.id, "kind": item.kind, "text_raw": text,
                            "bbox": box, "producer_ref": producer})
        identity = assign_source_ids(records, doc.file_hash, logical,
                                     page.metadata.get("evidence_generation", 1))
        if page.metadata.get("text_evidence"):
            # Phase-B structures are derived; they never renumber frozen text.
            pool = page.metadata["text_evidence"]
            identity["stable"] = identity["stable"] and pool["stable"]
            identity["collisions"].extend(pool["collisions"])
            for item, record in zip(items, records):
                data = identity["items"][item.id]
                if item.block is not None and item.block.source_id:
                    data.update(source_id=item.block.source_id, source_id_method=item.block.source_id_method,
                                raw_text_sha8=item.block.raw_text_sha8, evidence_generation=item.block.evidence_generation)
                else:
                    suffix = sha256_json_unicode({k: v for k, v in record.items() if k != "ref"})[:24]
                    rank_suffix = data["source_id"].rsplit(":", 1)[1]
                    data.update(source_id=f"{doc.file_hash[:16]}:{logical}:derived-{item.kind}-{suffix}-{rank_suffix}",
                                source_id_method="derived-content-geometry-v1",
                                derived_from_source_ids=(item.table.provenance if item.table is not None else
                                    item.formula.provenance if item.formula is not None else {}).get("derived_from_source_ids", []))
        page.metadata["source_identity"] = identity
        for item in items:
            if item.block is not None:
                data = identity["items"][item.id]
                for field in ("source_id", "source_id_method", "raw_text_sha8", "evidence_generation"):
                    setattr(item.block, field, data[field])
                item.block.review_reason_codes = sorted(set(item.block.review_reason_codes + data["review_reason_codes"]))
    resolve_document_furniture(doc, settings)
    for page in doc.pages:
        items = page_items(doc.file_hash, page, ignore_flow=True)
        identities = page.metadata["source_identity"]["items"]
        by_source = {identities[item.id]["source_id"]: item for item in items}
        body, headers, footers, numbers = [], [], [], []
        for item in items:
            entry = LayoutItem(identities[item.id]["source_id"], item.bbox,
                               item.block.text if item.block is not None else "",
                               item.block.block_type if item.block is not None else item.kind, item.kind)
            role = entry.role
            destination = headers if role in ("header", "running_header") else footers if role in ("footer", "running_footer") else numbers if role == "page_number" else body
            destination.append(entry)
        layout = infer_region_tree(body, min_gap=settings.get("min_gap", 3.0))
        def peripheral(group):
            return sorted(group, key=lambda b: (b.bbox[1] if b.bbox else float("inf"), b.id))
        source_flow = [b.id for b in peripheral(headers)] + layout.ordered_ids + [b.id for b in peripheral(footers + numbers)]
        edges = [Edge(a, b, hard=False, score=.5) for a, b in zip(source_flow, source_flow[1:])]
        source_flow, conflicts = solve_order(source_flow, edges)
        layout.review.extend(conflicts)
        flow = [by_source[s].id for s in source_flow]
        assignments = {}
        for index, source in enumerate(source_flow):
            item = by_source[source]
            assignment = {**layout.assignments.get(source, {"parent_region_id": "peripheral", "column_id": None,
                                                           "article_group_id": None}), "reading_order_index": index}
            if "callout_id" in assignment:
                assignment["callout_id"] = f"p{page.page_num:04d}:{assignment['callout_id']}"
            assignments[item.id] = assignment
            if item.block is not None:
                item.block.callout_id = assignment.get("callout_id")
                for field, value in assignment.items():
                    setattr(item.block, field, value)
        page.metadata.update(flow_doc_hash=doc.file_hash, ordered_item_ids=flow, ordered_source_ids=source_flow,
                             item_assignments=assignments, layout_tree=layout.tree,
                             order_edges=[{"before": e.before, "after": e.after, "relation": e.relation,
                                           "source": METHOD, "hard": e.hard} for e in edges],
                             layout_family=layout.layout_family, article_groups=layout.article_groups,
                             layout_review=layout.review, reading_order={"method": METHOD})
        if layout.review or not page.metadata["source_identity"]["stable"]:
            page.metadata["layout_status"] = "NEEDS_REVIEW"
        else:
            page.metadata["layout_status"] = "RESOLVED"
        # Furniture reviews were already accounted for by the shared pass.
        review_count = len(layout.review) + sum(b.role_status != "NEEDS_REVIEW" and bool(b.review_reason_codes) for b in page.blocks)
        previous = page.metadata.get("document_layout_review_count", 0)
        page.quality.review_required_count = max(0, page.quality.review_required_count - previous) + review_count
        page.metadata["document_layout_review_count"] = review_count
    doc.manifest["document_layout"] = {"method": METHOD, "quality_acceptance": "NOT_EVALUATED",
                                       "algorithm_version": ALGORITHM_VERSION, "settings": settings,
                                       "source_reconciliation_method": RECONCILIATION_METHOD,
                                       "source_stage": "extracted_page_evidence",
                                       "context_hash": doc.manifest["furniture"]["context_hash"]}
    return doc

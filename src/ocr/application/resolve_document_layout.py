"""CPU document pass: identity -> table protection/roles -> one shared flow."""
from __future__ import annotations

import hashlib
import json
import unicodedata

from src.ocr.infrastructure.layout.region_tree import LayoutItem, infer_region_tree, METHOD
from src.ocr.application.resolve_document_furniture import resolve_document_furniture
from src.ocr.infrastructure.structure.ids import page_items
from src.ocr.infrastructure.structure.source_identity import assign_source_ids
from src.ocr.infrastructure.postprocessing.reading_order_graph import Edge, solve_order
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.models.evidence_block import EvidenceBlock
from src.ocr.domain.models.region import Region, BoundingBox, ContentType
from src.ocr.infrastructure.layout.docling_structure import block_is_covered
from src.ocr.infrastructure.structure.source_identity import valid_bbox


def _reconcile_evidence(page):
    """Require text as well as geometry before considering table replacement.

    Unmapped sources return to the visible body with a review reason. The old
    geometric table filter must not erase business text without an audit.
    """
    evidence = page.metadata.get("text_evidence", {}).get("items", [])
    surviving = {b.source_id for b in page.blocks if b.source_id}
    dispositions = {}
    normalize = lambda text: " ".join(unicodedata.normalize("NFC", text).casefold().split())
    for source in evidence:
        sid = source["source_id"]
        if sid in surviving:
            dispositions[sid] = {"status": "VISIBLE_OR_FURNITURE"}
            continue
        text = normalize(source["text"])
        owners = [table for table in page.tables if block_is_covered(source["bbox"], [table])
                  and text and f" {text} " in f" {normalize(' '.join(c.text for c in sorted(table.cells, key=lambda c: (c.row_idx, c.col_idx))))} "]
        formula_owners = [formula for formula in page.formulas
                          if sid in formula.provenance.get("derived_from_source_ids", [])
                          and block_is_covered(source["bbox"], [formula])]
        if len(formula_owners) == 1 and not owners and not source.get("protected_from_furniture"):
            dispositions[sid] = {"status": "REPLACED_BY_FORMULA", "formula_id": formula_owners[0].formula_id,
                                 "reason": "DOCLING_FORMULA_ABSORPTION"}
        elif len(owners) == 1 and not formula_owners and not source.get("protected_from_furniture"):
            parents = owners[0].provenance.setdefault("derived_from_source_ids", [])
            if sid not in parents:
                parents.append(sid)
            dispositions[sid] = {"status": "REPLACED_BY_TABLE", "table_id": owners[0].table_id,
                                 "reason": "GEOMETRY_AND_EXACT_TEXT"}
        else:
            restored = TextBlock.model_validate(source)
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
    from src.ocr.runtime.config import document_layout_settings
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
                    payload = json.dumps({k: v for k, v in record.items() if k != "ref"}, sort_keys=True, ensure_ascii=False)
                    suffix = hashlib.sha256(payload.encode()).hexdigest()[:24]
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
            assignments[item.id] = assignment
            if item.block is not None:
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
                                       "algorithm_version": "region-tree-graph-v1.1", "settings": settings,
                                       "source_stage": "extracted_page_evidence",
                                       "context_hash": doc.manifest["furniture"]["context_hash"]}
    return doc

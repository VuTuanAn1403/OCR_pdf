"""Document-level output contracts (proposal V6 C3.7, decision D3).

Adds four files next to the existing outputs, which stay byte-compatible:
- extraction.json  every element with a stable id, raw text, bbox, source,
                   device and confidence (null when the model gives none);
- structure.json   the section tree (C2.4) and the table of contents;
- document.json    sections and chunks that only reference extraction ids;
- document.md      document-level Markdown with real heading levels.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.ocr.domain.models.document import ExtractedDocument
from src.ocr.domain.models.region import ContentType
from src.ocr.infrastructure.export.json_exporter import JSONExporter
from src.ocr.infrastructure.export.manifest_exporter import PIPELINE_VERSION
from src.ocr.infrastructure.export.markdown_exporter import MarkdownExporter
from src.ocr.infrastructure.structure.figures import METHOD as FIGURE_LINK_METHOD, link_figures
from src.ocr.infrastructure.structure.ids import Item, document_items
from src.ocr.infrastructure.table.financial_checks import (
    METHOD as FINANCIAL_CHECKS_METHOD, balance_identity_checks, continuation_candidate, multi_value_cells,
    table_total_checks,
)
from src.ocr.infrastructure.structure.section_tree import (
    ACCEPT_SCORE, MAX_LEVEL, METHOD, DOCUMENT_ROLE_METHOD, DocStructure, Paragraph, Section, build_structure,
)
from src.ocr.runtime.atomic_io import atomic_write_text
from src.ocr.infrastructure.structure.source_identity import valid_bbox

logger = logging.getLogger(__name__)

SCHEMA_EXTRACTION = "extraction-v1"
SCHEMA_STRUCTURE = "structure-v1"
SCHEMA_DOCUMENT = "doc-v1"


def _schema(doc, name):
    modern = doc.manifest.get("document_layout", {}).get("method") == "region_tree_graph_v1"
    return {"extraction-v1": "extraction-v2-layout", "structure-v1": "structure-v2-layout",
            "doc-v1": "doc-v2-layout"}[name] if modern else name
REVIEW_QUALITY = 0.75  # same threshold as EvidenceBlock status in ProcessPageUseCase
_VI_LETTERS = set("ăâđêôơưáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")
LANGUAGE_METHOD = "vi-en-lexical-heuristic-v2"
_VI_PHRASES = ("bao cao", "doanh thu", "loi nhuan", "tai chinh", "hoi dong", "co dong",
               "cong ty", "quan tri", "von dieu le", "dai hoi", "nam truoc", "tong tai san")
_EN_WORDS = set("the and of for with from to in is are was were has have revenue grew strongly annual report financial statements board directors shareholders profit assets liabilities cash flow company year results".split())
_EN_PHRASES = ("annual report", "financial statements", "board of directors", "cash flow")


def detect_language(text: str) -> str:
    return language_detection(text)["language"]


def language_detection(text: str) -> dict:
    """Conservative, inspectable baseline; ASCII alone is not English evidence."""
    normalized = unicodedata.normalize("NFC", text).lower()
    letters = [c for c in normalized if c.isalpha()]
    result = {"language": "unknown", "method": LANGUAGE_METHOD, "reason": "insufficient_lexical_evidence"}
    if len(letters) < 4:
        return result
    vietnamese = sum(c in _VI_LETTERS for c in letters)
    if vietnamese >= 0.03 * len(letters):
        return {**result, "language": "vi", "reason": "vietnamese_character_ratio"}
    tokens = re.findall(r"[a-z]+", normalized)
    words = " " + " ".join(tokens) + " "
    vi_hits = [phrase for phrase in _VI_PHRASES if f" {phrase} " in words]
    en_hits = sorted(set(tokens) & _EN_WORDS)
    en_phrases = [phrase for phrase in _EN_PHRASES if f" {phrase} " in words]
    result.update(vi_phrases=vi_hits, en_words=en_hits, en_phrases=en_phrases)
    if not all(ord(c) < 128 for c in letters):
        return result
    en_evidence = bool(en_phrases) or (len(en_hits) >= 2 and len(en_hits) >= .25 * len(tokens))
    if vi_hits and not en_evidence:
        return {**result, "language": "vi", "reason": "unaccented_vietnamese_phrase"}
    if en_evidence and not vi_hits:
        return {**result, "language": "en", "reason": "english_lexical_evidence"}
    return {**result, "reason": "mixed_lexical_evidence" if vi_hits and en_evidence else result["reason"]}


def _block_needs_review(block) -> bool:
    """TextBlock or section_tree.Line: unscored or low-quality text needs review."""
    return bool(getattr(block, "structure_needs_review", False)) or block.quality_score is None or block.quality_score < REVIEW_QUALITY


def _confidence_source(value: Optional[float], source: Optional[str] = None) -> str:
    return "unavailable" if value is None else (source or "engine")


def _cell_json(item: Item, cell, device: str) -> Dict[str, Any]:
    # A cell is addressed by its owning extraction table plus row/column.
    # When the recognizer omits a cell box, retain the coarser table box and
    # declare the fallback rather than leaving an untraceable empty source.
    valid_cell = valid_bbox(cell.bbox)
    valid_table = valid_bbox(item.bbox)
    bbox = cell.bbox if valid_cell else (item.bbox if valid_table else [])
    return {"row": cell.row_idx, "col": cell.col_idx, "row_span": cell.row_span, "col_span": cell.col_span,
            "text_raw": cell.text, "text_corrected": cell.text, "correction_source": None,
            "bbox": [round(v, 2) for v in bbox] if bbox else [],
            "bbox_source": "cell" if valid_cell else "table_fallback",
            "localization": "cell" if valid_cell else "coarse",
            "needs_review": not valid_cell or cell.confidence is None,
            "review_reason": "cell_bbox_unavailable" if not valid_cell else None,
            "spans": _spans([(item.page, item.id, bbox)]) if bbox else [],
            "source": item.table.engine, "device": device, "confidence": cell.confidence,
            "confidence_source": _confidence_source(cell.confidence, cell.confidence_source)}


def _coordinate_space(page) -> Tuple[str, List[Dict[str, Any]]]:
    """Blocks of scans OCR'd after an orientation fallback are stored in the
    rotated page space; say so instead of claiming original-page points."""
    meta = page.metadata or {}
    angle = {"rotate_90_cw": 90, "rotate_90_ccw": 270, "rotate_180": 180}.get(meta.get("orientation_fallback"))
    if angle and (meta.get("is_1up_landscape") or meta.get("is_180_rotated")):
        return "pdf_points_top_left_rotated", [{"op": "rotate", "degrees": angle}]
    return "pdf_points_top_left", []


def _item_json(item: Item, device: str) -> Dict[str, Any]:
    if item.kind == "b":
        b = item.block
        return {"id": item.id, "kind": "b", "bbox": item.bbox, "block_type": b.block_type,
                "text_raw": b.text if getattr(b, "text_raw", None) is None else b.text_raw,
                "text_corrected": b.text, "correction_source": getattr(b, "correction_source", None),
                "source": b.source, "engine": b.engine, "device": device,
                "confidence": b.confidence, "confidence_source": _confidence_source(b.confidence, b.confidence_source),
                "quality_score": b.quality_score, "font_size": b.font_size, "is_bold": b.is_bold,
                "region_id": b.region_id, "needs_review": _block_needs_review(b)}
    if item.kind == "t":
        t = item.table
        text = " | ".join(c.text.strip() for c in sorted(t.cells, key=lambda c: (c.row_idx, c.col_idx)) if c.text.strip())
        return {"id": item.id, "kind": "t", "bbox": item.bbox, "table_id": t.table_id, "engine": t.engine,
                "text_raw": text, "text_corrected": text, "correction_source": None, "source": t.engine,
                "device": device, "confidence": t.confidence,
                "confidence_source": _confidence_source(t.confidence, t.confidence_source),
                "num_rows": t.num_rows, "num_cols": t.num_cols, "needs_review": bool(t.needs_review),
                "html_path": t.provenance.get("html_artifact"),
                "cells": [_cell_json(item, c, device) for c in t.cells]}
    if item.kind == "f":
        f = item.formula
        return {"id": item.id, "kind": "f", "bbox": item.bbox, "formula_id": f.formula_id, "latex": f.latex,
                "text_raw": f.latex, "text_corrected": f.latex, "correction_source": None, "source": f.engine,
                "engine": f.engine, "device": device, "confidence": f.confidence,
                "confidence_source": _confidence_source(f.confidence),
                "status": f.status, "latex_path": f.provenance.get("latex_artifact"), "needs_review": True}
    r = item.region
    return {"id": item.id, "kind": "i", "bbox": item.bbox, "region_id": r.region_id,
            "text_raw": "", "text_corrected": "", "correction_source": None,
            "source": "image", "device": device, "confidence": None, "confidence_source": "unavailable",
            "content_type": r.content_type.value, "status": r.status, "asset": r.asset}


def build_extraction_json(doc: ExtractedDocument, items_by_page: Sequence[Sequence[Item]], device: str) -> Dict[str, Any]:
    pages = []
    for page, items in zip(doc.pages, items_by_page):
        space, transforms = _coordinate_space(page)
        pages.append({"page": page.page_num, "width": page.width, "height": page.height,
                      "page_type": page.page_type.value, "coordinate_space": space, "transforms": transforms,
                      **{key: page.metadata.get(key) for key in (
                          "logical_page_id", "physical_page_id", "ordered_item_ids", "layout_family",
                          "layout_tree", "article_groups", "order_edges", "layout_review", "text_evidence", "source_dispositions")},
                      "items": [{**_item_json(item, device),
                                 **page.metadata.get("source_identity", {}).get("items", {}).get(item.id, {}),
                                 **page.metadata.get("item_assignments", {}).get(item.id, {}),
                                 **(item.block.model_dump(include={"role_initial", "role_final", "role_status", "role_reasons",
                                     "role_source", "visibility", "suppression_reason", "support_page_ids", "template_id",
                                     "protected_from_furniture", "protection_reason", "table_candidate_id"})
                                    if item.block is not None else {})} for item in items]})
    return {"schema_version": _schema(doc, SCHEMA_EXTRACTION), "doc_id": doc.document_id, "doc_sha16": doc.file_hash,
            "pipeline_version": PIPELINE_VERSION, "profile": doc.profile, "total_pages": doc.total_pages,
            "pages": pages}


# --------------------------------------------------------------- sections --

def _section_id(doc: ExtractedDocument, index: int) -> str:
    return f"{doc.file_hash}:s{index:04d}"


def _flow_page(entry: Tuple[str, object]) -> int:
    kind, value = entry
    if kind == "heading":
        return value.heading.page
    if kind == "paragraph":
        return value.lines[-1].page
    return value.page


def _section_extents(structure: DocStructure) -> Dict[int, int]:
    """Last page of each section: up to the next heading of the same or a
    higher level."""
    last_page: Dict[int, int] = {}
    open_sections: List[Section] = []
    for entry in structure.flow:
        page = _flow_page(entry)
        if entry[0] == "heading":
            section = entry[1]
            open_sections = [s for s in open_sections if s.heading.level < section.heading.level]
            open_sections.append(section)
        for section in open_sections:
            last_page[section.index] = max(last_page.get(section.index, page), page)
    return last_page


def build_structure_json(doc: ExtractedDocument, structure: DocStructure, consumed: Optional[Set[str]] = None) -> Dict[str, Any]:
    extents = _section_extents(structure)
    toc = structure.toc
    consumed_set = set(consumed) if consumed else set()
    reasons = {k: v for k, v in dict(structure.dropped).items() if k not in consumed_set}
    for item_id in _duplicate_formula_blocks(structure):
        if item_id not in consumed_set:
            reasons[item_id] = "duplicate_formula"
    dropped = sorted(reasons.items())
    return {
        "schema_version": _schema(doc, SCHEMA_STRUCTURE), "doc_sha16": doc.file_hash,
        "method": DOCUMENT_ROLE_METHOD if _schema(doc, SCHEMA_STRUCTURE) != SCHEMA_STRUCTURE else METHOD,
        "params": {"accept_score": ACCEPT_SCORE, "max_level": MAX_LEVEL,
                   "limits": ["no font size on OCR lines: scan headings come from numbering only",
                              "tables are not merged across pages", "no learned text correction"]},
        "body_font_size": structure.body_font_size,
        "toc": None if toc is None else {
            "pages": toc.pages, "page_offset": toc.page_offset,
            "entries": [{"title": title, "printed_page": printed} for title, printed in toc.entries]},
        "sections": [{
            "section_id": _section_id(doc, s.index), "level": s.heading.level, "title": _title(s),
            "title_en": s.heading.title_en,
            "parent_id": _section_id(doc, s.parent) if s.parent else None,
            "page_start": s.heading.page, "page_end": extents.get(s.index, s.heading.page),
            "spans": _heading_spans(s),
            "heading_item_ids": s.heading.item_ids, "toc_confirmed": s.heading.toc_confirmed,
            "needs_review": any(_block_needs_review(line) for line in s.heading.lines),
            "score": round(s.heading.score, 2), "signals": s.heading.signals,
        } for s in structure.sections],
        "dropped": [{"item_id": item_id, "reason": reason} for item_id, reason in dropped],
    }


def _title(section: Section) -> str:
    heading = section.heading
    lines = [line.text for line in heading.lines]
    if heading.title_en and lines and lines[-1] == heading.title_en:
        lines = lines[:-1]
    return " ".join(text.strip() for text in lines)


def _heading_spans(section: Section) -> List[Dict[str, Any]]:
    return _spans([(line.page, line.item_id, line.bbox) for line in section.heading.lines])


# ----------------------------------------------------------------- chunks --

def _union(bboxes: Sequence[Sequence[float]]) -> List[float]:
    boxes = [b for b in bboxes if len(b) == 4]
    if not boxes:
        return []
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _spans(entries: Sequence[Tuple[int, str, List[float]]]) -> List[Dict[str, Any]]:
    """Group (page, id, bbox) into one span per page, in order."""
    spans: List[Dict[str, Any]] = []
    for page, item_id, bbox in entries:
        if not spans or spans[-1]["page"] != page:
            spans.append({"page": page, "bbox": [], "block_ids": [], "_boxes": []})
        spans[-1]["block_ids"].append(item_id)
        spans[-1]["_boxes"].append(bbox)
    for span in spans:
        span["bbox"] = [round(v, 2) for v in _union(span.pop("_boxes"))]
    return spans


def _confidence(values: Sequence[Optional[float]]) -> Tuple[Optional[float], str]:
    if not values or any(v is None for v in values):
        # One unscored constituent makes the aggregate unscored as well.
        return None, "unavailable"
    return round(min(values), 4), "min_of_blocks"


def _source(sources: Sequence[str]) -> str:
    unique = sorted(set(sources))
    return unique[0] if len(unique) == 1 else "mixed"


def _duplicate_formula_blocks(structure: DocStructure) -> set:
    """Formula-typed text blocks that repeat a Docling formula (the pipeline
    adds both); the Formula item is kept, the block is skipped."""
    formulas = [v for k, v in structure.flow if k == "item" and v.kind == "f"]
    return {v.id for k, v in structure.flow if k == "item" and v.kind == "b"
            and any(f.page == v.page and _overlaps(v.bbox, f.bbox) for f in formulas)}


def build_document_json(doc: ExtractedDocument, structure: DocStructure, device: str) -> Dict[str, Any]:
    duplicates = _duplicate_formula_blocks(structure)
    sections: Dict[str, Dict[str, Any]] = {}
    extents = _section_extents(structure)
    preamble = _section_id(doc, 0)
    current = preamble
    chunks: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    formulas: List[Dict[str, Any]] = []
    images: List[Dict[str, Any]] = []

    def section_entry(section_id: str) -> Dict[str, Any]:
        if section_id == preamble and preamble not in sections:
            sections[preamble] = {"section_id": preamble, "level": 0, "title": "", "title_en": None,
                                  "parent_id": None, "page_start": None, "page_end": None,
                                  "heading_item_ids": [], "spans": [], "chunk_ids": []}
        return sections[section_id]

    def add_chunk(chunk: Dict[str, Any]) -> None:
        entry = section_entry(current)
        entry["chunk_ids"].append(chunk["chunk_id"])
        if current == preamble:
            pages = [s["page"] for s in chunk["spans"]]
            entry["page_start"] = min([p for p in (entry["page_start"], *pages) if p is not None])
            entry["page_end"] = max([p for p in (entry["page_end"], *pages) if p is not None])
        chunks.append(chunk)

    for kind, value in structure.flow:
        if kind == "heading":
            current = _section_id(doc, value.index)
            sections[current] = {
                "section_id": current, "level": value.heading.level, "title": _title(value),
                "title_en": value.heading.title_en,
                "parent_id": _section_id(doc, value.parent) if value.parent else None,
                "page_start": value.heading.page, "page_end": extents.get(value.index, value.heading.page),
                "heading_item_ids": value.heading.item_ids, "spans": _heading_spans(value), "chunk_ids": [],
                "needs_review": any(_block_needs_review(line) for line in value.heading.lines)}
            continue
        if kind == "paragraph":
            add_chunk(_paragraph_chunk(value, current, device))
            continue
        item: Item = value
        if item.id in duplicates:
            continue
        if item.kind == "t":
            chunk = _table_chunk(item, current, device)
            add_chunk(chunk)
            tables.append({"table_id": item.id, "chunk_id": chunk["chunk_id"], "section_id": current,
                           "pages": [item.page], "num_rows": item.table.num_rows, "num_cols": item.table.num_cols,
                           "has_merged_cells": chunk["has_merged_cells"], "engine": item.table.engine,
                           "html_path": item.table.provenance.get("html_artifact"), "cells": chunk.pop("cells")})
        elif item.kind in ("f", "b"):  # Docling formula, or a text block typed as formula
            text = item.formula.latex if item.kind == "f" else item.block.text
            raw_text = text if item.kind == "f" or getattr(item.block, "text_raw", None) is None else item.block.text_raw
            correction = None if item.kind == "f" else getattr(item.block, "correction_source", None)
            confidence = item.formula.confidence if item.kind == "f" else item.block.confidence
            add_chunk({"chunk_id": item.id, "section_id": current, "type": "formula", "lang": "unknown",
                       "text_raw": raw_text, "text_corrected": text, "correction_source": correction,
                       "spans": _spans([(item.page, item.id, item.bbox)]), "source": item.formula.engine if item.kind == "f"
                       else item.block.source, "device": device, "confidence": confidence,
                       "confidence_source": _confidence_source(confidence, None if item.kind == "f"
                                                                else item.block.confidence_source),
                       "needs_review": True})
            if item.kind == "f":
                formulas.append({"formula_id": item.id, "chunk_id": item.id, "section_id": current})
        elif item.kind == "i":
            add_chunk({"chunk_id": item.id, "section_id": current, "type": "image", "lang": "unknown",
                       "text_raw": "", "text_corrected": "", "correction_source": None,
                       "spans": _spans([(item.page, item.id, item.bbox)]), "source": "image", "device": device,
                       "confidence": None, "confidence_source": "unavailable", "needs_review": False,
                       "asset": item.region.asset})
            images.append({"image_id": item.id, "chunk_id": item.id, "section_id": current,
                           "asset": item.region.asset})

    try:
        figure_links = link_figures(chunks, images)
    except Exception as exc:  # annotation only; never costs the export
        logger.exception("Figure caption linking failed for %s", doc.document_id)
        for entry in (*chunks, *images):
            for key in ("role", "figure_id", "caption", "figure_text_chunk_ids"):
                entry.pop(key, None)
        figure_links = {"method": FIGURE_LINK_METHOD, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
    try:
        consistency = _table_consistency(doc, structure, tables, chunks)
    except Exception as exc:  # flag-only checks must never cost the export
        logger.exception("Financial consistency checks failed for %s", doc.document_id)
        for entry in tables:
            entry["needs_review"] = True  # unchecked, so not cleared for use
        consistency = {"method": FINANCIAL_CHECKS_METHOD, "status": "FAILED",
                       "error": f"{type(exc).__name__}: {exc}"}
    ordered = sorted(sections.values(), key=lambda s: (s["section_id"] != preamble, s["section_id"]))
    return {"schema_version": _schema(doc, SCHEMA_DOCUMENT), "doc_id": doc.document_id, "doc_sha16": doc.file_hash,
            "source": {"sha256_16": doc.file_hash, "file_name": Path(doc.file_path).name},
            "extraction": "extraction.json", "sections": ordered, "chunks": chunks,
            "tables": tables, "formulas": formulas, "images": images,
            "consistency_checks": consistency, "figure_links": figure_links}


def _table_consistency(doc: ExtractedDocument, structure: DocStructure, tables: List[Dict[str, Any]],
                       chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """C3.5(b)/(c): flag-only arithmetic checks and multi-page continuation
    candidates. Numbers are never changed and tables are never merged; a
    violation or candidate only marks the table for review."""
    entries = {t["table_id"]: t for t in tables}
    chunk_by_id = {c["chunk_id"]: c for c in chunks}
    # Every table entry states its review flag, mirroring its chunk.
    for entry in tables:
        entry["needs_review"] = bool(chunk_by_id[entry["chunk_id"]]["needs_review"])
    flow_tables = [(i, v) for i, (k, v) in enumerate(structure.flow)
                   if k == "item" and v.kind == "t" and v.id in entries]

    def review(table_id: str) -> None:
        entries[table_id]["needs_review"] = True
        chunk_by_id[entries[table_id]["chunk_id"]]["needs_review"] = True

    statuses: Dict[str, int] = {"CONSISTENT": 0, "VIOLATION": 0, "INCONCLUSIVE": 0}
    for _, item in flow_tables:
        checks = table_total_checks(item.table, item.id) + multi_value_cells(item.table, item.id)
        entries[item.id]["arithmetic_checks"] = checks
        for check in checks:
            statuses[check["status"]] += 1
        if any(c["status"] == "VIOLATION" for c in checks):
            review(item.id)
    balance = balance_identity_checks([(item.id, item.table) for _, item in flow_tables])
    for check in balance:
        statuses[check["status"]] += 1
        if check["status"] == "VIOLATION":
            review(check["assets"]["table_id"])
            review(check["equity"]["table_id"])

    heights = {page.page_num: page.height for page in doc.pages}
    continuations = []
    for (i, previous), (j, following) in zip(flow_tables, flow_tables[1:]):
        if following.page != previous.page + 1:
            continue
        between = structure.flow[i + 1:j]
        candidate = continuation_candidate(
            previous.table, previous.bbox, heights.get(previous.page, 0.0),
            following.table, following.bbox, heights.get(following.page, 0.0),
            [v.text for k, v in between if k == "paragraph"], any(k == "heading" for k, _ in between))
        if candidate:
            candidate.update(previous_table_id=previous.id, table_id=following.id)
            entries[following.id]["continuation"] = candidate
            review(following.id)
            continuations.append(candidate)
    return {"method": FINANCIAL_CHECKS_METHOD, "status": "COMPLETED",
            "summary": {**statuses, "tables": len(flow_tables),
                        "tables_with_checks": sum(1 for _, item in flow_tables if entries[item.id]["arithmetic_checks"]),
                        "continuation_candidates": len(continuations)},
            "balance_identity": balance, "continuation_candidates": continuations,
            "note": "Flag-only: no value is corrected and no table is merged (proposal C3.5b/c)"}


def _paragraph_chunk(paragraph: Paragraph, section_id: str, device: str) -> Dict[str, Any]:
    confidence, confidence_source = _confidence([line.confidence for line in paragraph.lines])
    raw_text = " ".join((line.text if line.text_raw is None else line.text_raw).strip() for line in paragraph.lines)
    joined_corrected = " ".join(line.text.strip() for line in paragraph.lines)
    sources = {line.correction_source for line in paragraph.lines if line.correction_source}
    dehyphenated = joined_corrected != paragraph.text
    if dehyphenated:
        sources.add("line_wrap_dehyphenation")
    return {"chunk_id": paragraph.lines[0].item_id, "section_id": section_id, "type": paragraph.kind,
            "lang": detect_language(paragraph.text), "language_detection": language_detection(paragraph.text),
            "text_raw": raw_text, "text_corrected": paragraph.text,
            "correction_source": "+".join(sorted(sources)) if sources else None,
            "spans": _spans([(line.page, line.item_id, line.bbox) for line in paragraph.lines]),
            "source": _source([line.source for line in paragraph.lines]), "device": device,
            "confidence": confidence, "confidence_source": confidence_source,
            "needs_review": dehyphenated or any(_block_needs_review(line) for line in paragraph.lines)}


def _table_chunk(item: Item, section_id: str, device: str) -> Dict[str, Any]:
    table = item.table
    merged = any(c.row_span > 1 or c.col_span > 1 for c in table.cells)
    text = " | ".join(c.text.strip() for c in sorted(table.cells, key=lambda c: (c.row_idx, c.col_idx)) if c.text.strip())
    cells = [_cell_json(item, c, device) for c in table.cells]
    return {"chunk_id": item.id, "section_id": section_id, "type": "table", "lang": detect_language(text),
            "language_detection": language_detection(text),
            "text_raw": text, "text_corrected": text, "correction_source": None,
            "spans": _spans([(item.page, item.id, item.bbox)]), "source": table.engine, "device": device,
            "confidence": table.confidence,
            "confidence_source": _confidence_source(table.confidence, table.confidence_source),
            "needs_review": bool(table.needs_review) or table.confidence is None or not text
                or any(c["bbox_source"] == "table_fallback" for c in cells),
            "has_merged_cells": merged, "cells": cells}


# ---------------------------------------------------------------- markdown --

def render_document_md(doc: ExtractedDocument, structure: DocStructure, assets_mode: str) -> str:
    out: List[str] = []
    duplicates = _duplicate_formula_blocks(structure)
    diagram_pages = ({p.page_num: p for p in doc.pages if p.diagrams}
                     if doc is not None and doc.manifest.get("document_layout", {}).get("method") == "region_tree_graph_v1" else {})
    emitted = set()
    for kind, value in structure.flow:
        page_number = _flow_page((kind, value))
        if page_number in diagram_pages and page_number not in emitted:
            out.extend(MarkdownExporter.render_diagrams(diagram_pages[page_number]))
            emitted.add(page_number)
        if kind == "item" and value.id in duplicates:
            continue
        if kind == "heading":
            out.append(f"{'#' * value.heading.level} {_escape(_title(value))}")
            if value.heading.title_en:
                out.append(f"*{_escape(value.heading.title_en)}*")
        elif kind == "paragraph":
            text = value.text
            if value.lines and all(line.block_type in ("header", "footer") for line in value.lines):
                label = "Header" if value.lines[0].block_type == "header" else "Footer"
                out.append(f"> **{label}**: {_escape(text)}")
                continue
            if value.kind == "list_item":
                text = "- " + text.lstrip()[1:].lstrip()
                out.append(text)
            else:
                out.append(_escape(text))
        else:
            item: Item = value
            if item.kind == "t":
                pipe = item.table.to_pipe_table().strip()
                if pipe:
                    out.append(pipe)
                html = item.table.provenance.get("html_artifact")
                if html and any(c.row_span > 1 or c.col_span > 1 for c in item.table.cells):
                    out.append(f"*Bảng có ô gộp, cấu trúc đầy đủ: [{html}]({html})*")
            elif item.kind == "b":
                out.append(f"$$\n{item.block.text.strip()}\n$$")
            elif item.kind == "f":
                out.append(item.formula.to_markdown())
            elif item.kind == "i":
                region = item.region
                if assets_mode == "none":
                    out.append(MarkdownExporter.image_omitted_marker(region.region_id))
                else:
                    tag = "Embedded table image" if region.content_type == ContentType.IMAGE_TABLE else "Embedded image"
                    out.append(f"![{tag}](images/{MarkdownExporter.image_asset_name(item.page, region, assets_mode)})")
    for number in sorted(set(diagram_pages) - emitted):
        out.extend(MarkdownExporter.render_diagrams(diagram_pages[number]))
    return "\n\n".join(out).strip() + "\n"


def _escape(text: str) -> str:
    """Keep body text from being parsed as a heading, table or list."""
    return "\\" + text if text and (text[:1] in "#|>" or text.startswith(("- ", "+ ", "* "))) else text


def _overlaps(a: Sequence[float], b: Sequence[float], threshold: float = 0.5) -> bool:
    if len(a) != 4 or len(b) != 4:
        return False
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    return inter / area >= threshold


# ------------------------------------------------------------------- write --

def write_document_contracts(doc: ExtractedDocument, doc_dir: Path, assets_mode: str) -> None:
    """Write the four C3.7 files. extraction.json is always written. A failure
    in the structure heuristics is recorded in structure.json and the stale
    document files are removed, so V6 validation fails and names the cause
    instead of shipping an outdated document.json."""
    device = doc.manifest.get("device", "cpu")
    items_by_page = document_items(doc)
    JSONExporter.save_json(build_extraction_json(doc, items_by_page, device), doc_dir / "extraction.json")
    try:
        layout_method = doc.manifest.get("document_layout", {}).get("method", "legacy")
        structure = build_structure(items_by_page, layout_method=layout_method,
                                    furniture_method=doc.manifest.get("furniture", {}).get("method"))
        document_json = build_document_json(doc, structure, device)
        consumed = {ref for section in document_json.get("sections", []) for span in section.get("spans", []) for ref in span.get("block_ids", [])}
        consumed.update(ref for chunk in document_json.get("chunks", []) for span in chunk.get("spans", []) for ref in span.get("block_ids", []))
        structure_json = build_structure_json(doc, structure, consumed=consumed)
        markdown = render_document_md(doc, structure, assets_mode)
    except Exception as exc:  # recorded for validation; see docstring
        logger.exception("Structure step failed for %s", doc.document_id)
        JSONExporter.save_json({"schema_version": _schema(doc, SCHEMA_STRUCTURE), "doc_sha16": doc.file_hash,
                                "error": f"{type(exc).__name__}: {exc}"}, doc_dir / "structure.json")
        for stale in ("document.json", "document.md"):
            (doc_dir / stale).unlink(missing_ok=True)
        return
    JSONExporter.save_json(structure_json, doc_dir / "structure.json")
    JSONExporter.save_json(document_json, doc_dir / "document.json")
    atomic_write_text(doc_dir / "document.md", markdown)

"""Docling structure enrichment of one page, moved verbatim from
ProcessPageUseCase._enrich_structures (P07).

Model tables replace only the heuristic tables they overlap; empty cells are
filled from phase A text; covered text blocks are dropped only when a table or
formula holds them. Every model structure stays NEEDS_REVIEW with no
calibrated confidence. A Docling failure keeps the existing extraction
(FAILED_FALLBACK) unless `on_error: raise`.
"""
import logging
import re

from ocr_contracts.geometry import block_is_covered, is_non_horizontal_line_candidate
from ocr_contracts.page_type import PageType
from ocr_contracts.region import BoundingBox, ContentType, Region
from ocr_contracts.text_block import TextBlock
from ocr_structure.docling_client import DoclingStructureError
from ocr_structure.table_selection import retain_heuristic_candidates

logger = logging.getLogger(__name__)


def enrich_structures(extractor, doc_page, page_num, page_type, blocks, tables, regions, meta, *,
                      bridge_unplaced_indices=None):
    fingerprint = extractor.cache_fingerprint() if extractor.enabled else None
    meta["docling_structure"] = {"status": "DISABLED", "cache_fingerprint": fingerprint}
    if not extractor.enabled:
        return blocks, tables, []
    options = extractor.options
    has_formula_candidate = any(
        b.block_type == "formula"
        or re.search(r"[∫∑√≠≤≥±×÷∂∆∇∈∉∩∪⊂⊃∀∃∞]|\\(?:frac|sum|int|prod|sqrt)\b|(?:[a-zA-Z]\s*=\s*[-+]?[0-9/.]+|[a-zA-Z]\^[0-9]+|\b(?:sin|cos|tan|log|lim)\b)", b.text)
        for b in blocks
    )
    candidate = (
        options.get("page_selection", "auto") == "all"
        or page_type != PageType.NATIVE_TEXT or bool(tables)
        or has_formula_candidate
    )
    if not candidate:
        meta["docling_structure"]["status"] = "SKIPPED_NO_STRUCTURE_CANDIDATE"
        return blocks, tables, []
    try:
        result = extractor.extract_page(doc_page, page_num)
    except DoclingStructureError as exc:
        meta["docling_structure"].update(status="FAILED_FALLBACK", error=str(exc))
        logger.warning("Page %d: Docling failed; preserving existing extraction: %s", page_num, exc)
        # Record a review item even if the source classifier missed the
        # table/formula. Never label the heuristic fallback as TableFormer.
        regions.append(Region(
            region_id=f"p{page_num}_docling_failed", bbox=BoundingBox(
                x0=0, y0=0, x1=meta["width"], y1=meta["height"]),
            content_type=ContentType.UNKNOWN, needs_fallback=True,
            status="NEEDS_REVIEW", failure_reason=f"Docling structure extraction failed: {exc}",
            orientation_status="UNCERTAIN",
        ))
        if options.get("on_error", "fallback") == "raise":
            raise
        return blocks, tables, []
    meta["docling_structure"] = {**result["metadata"], "cache_fingerprint": fingerprint}
    model_tables = result["tables"] if options.get("tables", True) else []
    formulas = result["formulas"] if options.get("formulas", True) else []
    vertical = {index for index, block in enumerate(blocks) if is_non_horizontal_line_candidate(block.recognition_provenance)}
    unplaced = vertical if bridge_unplaced_indices is None else bridge_unplaced_indices
    # Populate empty cell text from high-accuracy native or VietOCR blocks
    for table in model_tables:
        for cell in table.cells:
            if not cell.text.strip() and cell.bbox and len(cell.bbox) == 4:
                cx0, cy0, cx1, cy1 = cell.bbox
                matched = [
                    b.text.strip() for index, b in enumerate(blocks)
                    if index not in vertical and len(b.bbox) == 4 and cx0 <= (b.bbox[0] + b.bbox[2]) / 2.0 <= cx1
                    and cy0 <= (b.bbox[1] + b.bbox[3]) / 2.0 <= cy1
                    and b.text.strip()
                ]
                if matched:
                    cell.text = " ".join(matched)
    # Replace only overlapping heuristic tables. Unmatched tables and text
    # remain, including when Docling finds no structures.
    geometric = [table for table in tables
                 if table.engine == "deterministic_geometry"
                 and table.provenance.get("method") == "financial-geometry-v1"
                 and table.provenance.get("source_coverage") == "COMPLETE"
                 and table.provenance.get("numeric_guard") == "PRESERVED"]
    # Preserve only a source-checked grid for the same region. A large model table
    # spanning unrelated material must not be discarded on a one-way overlap test.
    preserved = [table for table in geometric if any(
        block_is_covered(model.bbox, [table], 0.98)
        for model in model_tables)]
    model_tables = [model for model in model_tables if not any(
        block_is_covered(model.bbox, [table], 0.98)
        for table in preserved)]
    if preserved:
        meta["docling_structure"]["preserved_geometric_tables"] = [table.table_id for table in preserved]
    tables = retain_heuristic_candidates(tables, model_tables, meta['docling_structure']) + model_tables
    # Freeze formula ownership at the point where the original text is
    # replaced. LaTeX need not equal the OCR transcription byte-for-byte.
    for index, block in enumerate(blocks):
        if index in vertical or re.fullmatch(r"\(\d+(?:\.\d+)*\)", block.text.strip()):
            continue  # equation labels are independent visible evidence
        owners = [formula for formula in formulas if block_is_covered(block.bbox, [formula])]
        if len(owners) == 1 and block.source_id:
            parents = owners[0].provenance.setdefault("derived_from_source_ids", [])
            if block.source_id not in parents:
                parents.append(block.source_id)
            owners[0].provenance["source_mapping_method"] = "docling-formula-absorption-v1"
    blocks = [block for index, block in enumerate(blocks) if index in unplaced or (
        not block_is_covered(block.bbox, model_tables)
        and (index in vertical or re.fullmatch(r"\(\d+(?:\.\d+)*\)", block.text.strip())
             or not block_is_covered(block.bbox, formulas)))]
    for structure in model_tables + formulas:
        is_formula = hasattr(structure, "latex")
        region_id = f"p{page_num}_docling_{'formula' if is_formula else 'table'}_{len(regions)}"
        regions.append(Region(
            region_id=region_id, bbox=BoundingBox(
                x0=structure.bbox[0], y0=structure.bbox[1], x1=structure.bbox[2], y1=structure.bbox[3]),
            content_type=ContentType.FORMULA if is_formula else ContentType.TABLE,
            source="ocr", confidence=None, quality_score=None, status="NEEDS_REVIEW",
            needs_fallback=True, orientation_status="CONFIDENT", orientation_confidence=1.0,
            failure_reason="Model output has no calibrated confidence or independent verification",
        ))
        if is_formula:
            blocks.append(TextBlock(text=structure.latex, source="docling", page=page_num,
                bbox=structure.bbox, block_type="formula", engine=structure.engine,
                confidence=None, quality_score=None, confidence_source="unavailable",
                region_id=region_id))
    return blocks, tables, formulas


def drop_blocks_inside_tables(blocks, tables, layout_engine):
    """Text represented by a table is not repeated as paragraphs (P09b, verbatim)."""
    if tables:
        heuristic_tables = [t for t in tables if t.engine != "docling_tableformer"]
        model_tables = [t for t in tables if t.engine == "docling_tableformer"]
        blocks = [b for b in blocks if is_non_horizontal_line_candidate(b.recognition_provenance) or not (
            (heuristic_tables and layout_engine.is_block_inside_any_table(b.bbox, heuristic_tables))
            or block_is_covered(b.bbox, model_tables))]
    return blocks

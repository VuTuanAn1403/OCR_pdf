"""Phase B (structure): Docling on the GPU text bridge of each phase A page.
Moved verbatim from ocr_workers.phases (P11b); it never loads recognition or
layout code (stage closure "structure").
"""
from __future__ import annotations

import time

from ocr_artifacts.atomic_io import atomic_write_json
from ocr_workers.stage_io import read, receipt_path, stage_dir, write_receipt


def phase_b_status_accepted(info, on_error):
    """Docling fallback policy of V6.3 (D3).

    With `on_error: fallback`, a page whose Docling call failed keeps its
    phase A extraction (FAILED_FALLBACK) and a page whose conversion logged
    errors keeps the model result; the validator marks both NEEDS_REVIEW.
    Failures outside Docling raise before this check and still fail phase B.
    """
    fallback = on_error == "fallback"
    allowed = ("success", "SKIPPED_NO_STRUCTURE_CANDIDATE") + (("FAILED_FALLBACK",) if fallback else ())
    return info.get("status") in allowed and (fallback or not info.get("errors"))


def run_structure(request):
    from ocr_runtime.pipeline_policy import validate_phase_request
    validate_phase_request(request)
    from ocr_pdf_io.document_view import processing_pdf
    # Phase B maps Docling output onto phase A text; it never loads recognition
    # code (ProcessPageUseCase/VietOCR) into this coordinating process.
    from ocr_contracts.page import ExtractedPage
    from ocr_structure.docling_client import DoclingStructureExtractor
    from ocr_structure.enrichment import enrich_structures
    from ocr_structure.region_views import RegionStructureExtractor
    from ocr_structure.small_image_tables import reconstruct_small_image_tables
    from ocr_structure.text_table_alignment import align_text_tables
    from ocr_contracts.geometry import block_is_covered
    text_root, root = stage_dir(request, "text"), stage_dir(request, "structure")
    checkpoint = read(receipt_path(request, "text"))
    extractor = DoclingStructureExtractor(request["config"]["docling"])
    files, placement = [], []
    started = time.perf_counter()
    try:
        with processing_pdf(request["pdf"], request["config"].get("pdf_render_policy", "original_v1"),
                            expected_sha256=request.get("identity", {}).get("pdf")) as (pdf, render_view):
            if checkpoint.get("pdf_render_view", render_view) != render_view:
                raise ValueError("Phase B processing view differs from phase A")
            for name in sorted(checkpoint["files"]):
                page = ExtractedPage.model_validate(read(text_root / name))
                small_tables=reconstruct_small_image_tables(page)
                if small_tables:
                    removed=[t for t in page.tables if t.engine=='heuristic' and block_is_covered(t.bbox,small_tables,.8)]
                    page.metadata['superseded_small_image_heuristics']=[t.model_dump(mode='json') for t in removed]
                    page.tables=[t for t in page.tables if t not in removed]+small_tables
                before = time.perf_counter()
                from ocr_structure.ocr_bridge import ocr_bridge, unplaced_visible_block_indices
                orientation_placements = []
                source_coverage = {}
                with ocr_bridge(pdf[page.page_num-1], page, request["config"]["render_dpi"],
                                font_path=request["config"].get("bridge_font"),
                                orientation_placements=orientation_placements, source_coverage=source_coverage) as bridge:
                    unplaced = unplaced_visible_block_indices(orientation_placements, len(page.blocks))
                    for index in unplaced:
                        block = page.blocks[index]
                        if not block.review_reason_codes and not block.reviewed:
                            page.quality.review_required_count += 1
                        if "bridge_orientation_unresolved" not in block.review_reason_codes:
                            block.review_reason_codes.append("bridge_orientation_unresolved")
                    page_extractor = RegionStructureExtractor(extractor, page.metadata.get("regional_ocr", {}).get("views", []))
                    page.blocks, page.tables, page.formulas = enrich_structures(
                        page_extractor, bridge[0], page.page_num, page.page_type, page.blocks,
                        page.tables, page.regions, page.metadata, bridge_unplaced_indices=unplaced)
                    page.tables=align_text_tables(page)
                page.metadata["ocr_structure_bridge"] = {"policy":"raster-plus-gpu-text-v1",
                    "native_text_layer_copied":False,"generation":request["generation"], "source_pool": source_coverage}
                if orientation_placements:
                    page.metadata["ocr_structure_bridge"]["orientation_placements"] = orientation_placements
                for structure in [*page.tables,*page.formulas]:
                    structure.provenance["text_source_policy"] = "gpu_ocr_only_v1"
                    structure.provenance["bridge"] = "raster-plus-gpu-text-v1"
                for table in page.tables:
                    # Restore OCR whitespace after font encoding, never digits.
                    for cell in table.cells:
                        cell.text = cell.text.replace("\u00a0", " ")
                    table.provenance["bridge_whitespace"] = "nbsp-to-ocr-space"
                info = page.metadata["docling_structure"]
                if not phase_b_status_accepted(info, request["config"]["docling"].get("on_error")):
                    raise RuntimeError(f"Incomplete Docling phase B: {info}")
                placement.append(info.get("provenance", {}).get("placement", {}))
                page.quality.stage_seconds["docling_structure"] = time.perf_counter()-before
                page.quality.time_taken_seconds += time.perf_counter()-before
                page.quality.total_blocks = len(page.blocks)
                page.metadata["runtime_phase"].update(phase="B", docling_deferred=False)
                path = root / "structure" / f"page-{page.page_num:04d}.json"
                atomic_write_json(path, page.model_dump(mode="json"))
                files.append(path)
        write_receipt(request, "structure", files, seconds=time.perf_counter()-started, placement=placement)
    finally:
        extractor.close()

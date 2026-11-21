"""V6.3 phase A for one page (`ocr_source_policy: gpu_ocr_only_v1`, P09b).

Full-page OCR of the rendered page remains mandatory. Additional observed
region views use the same GPU, retaining full-page detection evidence and
mapping region readings back into the page. Visible PDF glyph geometry can
guide a turn but never supplies text. Docling runs separately in phase B.
Bounded diacritic retries also retain source pixels, hypotheses and placement.
"""
import logging
import time
from collections import Counter
from types import SimpleNamespace
from typing import Any, Dict, List

from ocr_contracts.page import ExtractedPage
from ocr_contracts.page_type import PageType
from ocr_document.document_furniture import protect_table_continuation_lines
from ocr_document.markdown_exporter import MarkdownExporter
from ocr_document.page_assembly import assign_region_statuses, build_evidence_blocks, build_page_quality
from ocr_document.reading_order import ReadingOrderSorter
from ocr_document.source_identity import lock_rejected_ocr_lines, lock_text_evidence
from ocr_document.xy_cut import METHOD as XY_CUT_METHOD
from ocr_layout.steps import apply_layout_model, link_blocks_to_regions
from ocr_pdf_io.coordinate_transform import page_display_chain
from ocr_pdf_io.scan_image import acquire_page_image, full_page_image_provenance
from ocr_pdf_io.orientation_regions import visible_orientation_geometry
from ocr_recognition.blocks import mark_review_regions, ocr_results_to_blocks
from ocr_recognition.inference import predict_image_counted
from ocr_recognition.regional_ocr import frame_orientation, retry_reading_regions
from ocr_recognition.diacritic_retry import retry_diacritic_blocks
from ocr_recognition.short_token_retry import retry_short_token_blocks
from ocr_recognition.detection_evidence import capture_primary_detections, input_image_identity
from ocr_recognition.ocr_layer_agreement import page_layer_agreement
from ocr_runtime.config import document_layout_settings, furniture_settings
from ocr_runtime.pipeline_policy import TEXT_REQUIRED
from ocr_runtime.structured_logging import log_context, progress
from ocr_structure.enrichment import drop_blocks_inside_tables, enrich_structures
from ocr_workers.page_engines import build_page_engines, validate_page_config

logger = logging.getLogger(__name__)

# Settings of the V6.3 phase A path; anything else belongs to another orchestrator.
_REQUIRED = TEXT_REQUIRED


class TextPageExtractor:
    """Phase A page extractor of V6.3. Construct once per document."""

    def __init__(self, doc, config: Dict[str, Any], doc_hash: str):
        wrong = {key: config.get(key) for key, value in _REQUIRED.items() if config.get(key) != value}
        if wrong or config.get("fallback_only_on_review") or config.get("docling", {}).get("enabled"):
            raise ValueError(f"TextPageExtractor runs only the V6.3 phase A path; unsupported settings: {wrong}")
        self.doc, self.config, self.doc_hash = doc, config, doc_hash
        self.document_layout = document_layout_settings(config)
        self.legacy_furniture = furniture_settings(self.document_layout)["method"] == "legacy_v6_1"
        engines = build_page_engines(doc, config, legacy_furniture=self.legacy_furniture)
        self.inspector, self.renderer = engines.inspector, engines.renderer
        self.ocr_engine, self.ocr_quality = engines.ocr_engine, engines.ocr_quality
        self.fallback_engine = engines.fallback_engine
        self.layout_engine, self.structure_extractor = engines.layout_engine, engines.structure_extractor
        self.content_analyzer = engines.content_analyzer
        validate_page_config(config)
        self._current_page_ocr_calls = 0
        self._page_errors = 0
        self._ocr_failures: List[str] = []

    def close(self) -> None:
        executor = getattr(self, "_ocr_executor", None)
        if executor is not None:
            executor.shutdown(wait=True)
            self._ocr_executor = None
        self.structure_extractor.close()

    def _record_ocr_failure(self, message: str) -> None:
        self._page_errors = getattr(self, "_page_errors", 0) + 1
        self._ocr_failures.append(message)

    def execute(self, page_num: int, total_pages: int, router, resume: bool = False) -> ExtractedPage:
        if resume:
            raise ValueError("Phase A has no page cache; resume is handled by phase checkpoints")
        with log_context(page=page_num):
            return self._extract(page_num, total_pages, router)

    def _extract(self, page_num: int, total_pages: int, router) -> ExtractedPage:
        t0 = time.perf_counter()
        self._current_page_ocr_calls = 0
        self._page_errors = 0
        self._ocr_failures = []
        render_dpi = self.config.get("render_dpi", 180)
        page_timeout_seconds = float(self.config.get("page_timeout_seconds", 120))
        self._active_page_deadline = page_timeout_seconds

        meta = self.inspector.inspect_page(page_num)
        page_type, native_score = router.execute(meta)
        page_w = meta["width"]
        page_h = meta["height"]
        doc_page = self.doc[page_num - 1]
        # Full-page OCR scales rendered pixels into page.rect (display points).
        meta["coordinate_space"] = {"name": "pdf_display_points",
                                    "native_to_output": page_display_chain(doc_page).to_dict()}
        regions = self.content_analyzer.analyze(meta, doc_page, display_coordinates=True)
        inspect_duration = time.perf_counter() - t0
        region_type_counts = dict(Counter(r.content_type.value for r in regions))
        progress(f"[{page_num}/{total_pages}] page={page_num} type={page_type.value} dpi={render_dpi} regions={len(regions)} {region_type_counts}",
                 stage="route", page_type=page_type.value, regions=region_type_counts)

        blocks: List[Any] = []
        diagrams: List[Any] = []
        rejected_evidence: List[Any] = []
        page_errors: List[str] = []
        suspicious_lines: List[str] = []
        candidates_for_fallback: List[Any] = []
        meta["routing_override"] = "native_first_disabled"

        actual_dpi = render_dpi
        t_render_start = time.perf_counter()
        meta["full_page_image"] = full_page_image_provenance(doc_page)
        if self.config.get("scan_image"):  # opt-in embedded image / DPI policy (R-08)
            img_bgr, meta["scan_image"] = acquire_page_image(
                self.doc, page_num, self.renderer, render_dpi, self.config["scan_image"])
            actual_dpi = meta["scan_image"]["processing_dpi"]
        else:
            img_bgr = self.renderer.render_page(page_num, dpi=render_dpi)
        if regions:
            region_type_counts = dict(Counter(r.content_type.value for r in regions))
            meta["region_count"] = len(regions)
        render_duration = time.perf_counter() - t_render_start
        t_evidence_start = time.perf_counter()
        primary_input = input_image_identity(img_bgr)
        detector_evidence_duration = time.perf_counter() - t_evidence_start
        t_ocr_start = time.perf_counter()
        direction_geometry = visible_orientation_geometry(doc_page)
        meta["orientation_geometry"] = direction_geometry
        hint = {"method":direction_geometry["method"],"clockwise_degrees":0} if direction_geometry["upright"] else None
        with frame_orientation(self.ocr_engine, hint):
            ocr_results = predict_image_counted(self, img_bgr)
        # Regional retries update the detector's last-call telemetry. Keep
        # the primary full-page probe separate for the phase A GPU receipt.
        detector = getattr(self.ocr_engine, "_detector", None)
        primary_shape = getattr(detector, "last_input_shape", None)
        self.primary_ocr_detector = ({"input_shape": list(primary_shape),
                                      "detection_count": len(detector.last_detections)}
                                     if isinstance(primary_shape, list) else None)
        ocr_duration = time.perf_counter() - t_ocr_start
        t_evidence_start = time.perf_counter()
        meta["primary_text_detection"] = capture_primary_detections(detector, primary_input, [page_w, page_h])
        detector_evidence_duration += time.perf_counter() - t_evidence_start
        t_regional_start = time.perf_counter()
        ocr_results, regional_record = retry_reading_regions(
            self, img_bgr, ocr_results, direction_geometry, [page_w,page_h])
        ocr_duration += time.perf_counter() - t_regional_start
        if regional_record["views"]:
            meta["regional_ocr"] = regional_record
        recognized_lines = len(ocr_results)
        recognizer_confidences = [r.get("confidence") for r in ocr_results if r.get("confidence") is not None]
        progress(f"[{page_num}/{total_pages}] primary={len(ocr_results)} lines time={ocr_duration:.2f}s", stage="ocr",
                 lines=len(ocr_results), seconds=round(ocr_duration, 3))
        scale_x = page_w / max(1.0, img_bgr.shape[1])
        scale_y = page_h / max(1.0, img_bgr.shape[0])
        ocr_results_to_blocks(
            ocr_results, regions=regions, page_num=page_num, scale_x=scale_x, scale_y=scale_y,
            use_raw_bbox=False, page_w=page_w, page_h=page_h, ocr_quality=self.ocr_quality,
            dla_engine=getattr(self.content_analyzer, "dla_engine", None),
            numeric_retry_threshold=float(self.config.get("numeric_retry_threshold", 0.85)),
            rejected_evidence=rejected_evidence, blocks=blocks,
            candidates_for_fallback=candidates_for_fallback, suspicious_lines=suspicious_lines)
        if page_type == PageType.SCANNED_WITH_OCR_LAYER:
            # Layer vs primary OCR (C2.1).
            meta["ocr_layer_agreement"] = page_layer_agreement(
                doc_page, blocks, meta, self.config.get("ocr_layer_agreement", {}).get("sample_lines", 5))
        t_retry_start = time.perf_counter()
        retry_record = retry_diacritic_blocks(self, page_num, blocks, candidates_for_fallback, suspicious_lines)
        short_record = retry_short_token_blocks(self, page_num, blocks, candidates_for_fallback, suspicious_lines)
        fallback_duration = time.perf_counter() - t_retry_start
        if self.config.get("diacritic_retry") or retry_record["suspect_lines"]:
            meta["diacritic_retry"] = retry_record
        if self.config.get("short_token_retry") or short_record["suspect_lines"]:
            meta["short_token_retry"] = short_record
        mark_review_regions(candidates_for_fallback, regions)

        apply_layout_model(self.content_analyzer.dla_engine, self.renderer, self.config,
                           doc_page, page_num, blocks, regions, meta, img_bgr)
        # Protect continuation signals before any structure worker can absorb
        # text, and freeze evidence before sorting/removal changes list order.
        if self.document_layout["method"] != "legacy":
            protect_table_continuation_lines([SimpleNamespace(page_num=page_num, blocks=blocks)])
            lock_text_evidence(blocks, self.doc_hash, page_num, meta)
        # Spec 13 §4.1: no recognized line leaves the record silently.
        lock_rejected_ocr_lines(rejected_evidence, self.doc_hash, page_num, meta, regions)
        if self.config.get("retain_table_ocr_lines", False):
            meta["table_ocr_evidence"] = {
                "method": "post-fallback-pre-table-v1",
                "lines": [b.to_dict() for b in blocks
                          if b.source in ("primary_ocr", "fallback_ocr", "image_ocr")],
            }
        layout_model_duration = meta.get("layout_model", {}).get("seconds", 0.0)
        t_table_start = time.perf_counter()
        allow_vector_tables = False  # vector tables read native words; V6.3 never uses them
        table_rejections: List[Dict[str, Any]] = []
        tables = self.layout_engine.extract_tables(doc_page, blocks, page_num, allow_vector_tables=False,
                                                   regions=regions, rejections=table_rejections)
        if table_rejections:
            meta["table_rejections"] = table_rejections

        t_docling_start = time.perf_counter()
        # Docling is disabled in phase A; this records the deferred stage.
        blocks, tables, formulas = enrich_structures(
            self.structure_extractor, doc_page, page_num, page_type, blocks, tables, regions, meta)
        docling_duration = time.perf_counter() - t_docling_start
        if meta["docling_structure"]["status"] == "FAILED_FALLBACK":
            page_errors.append(meta["docling_structure"]["error"])
        elif meta["docling_structure"].get("errors"):
            page_errors.extend(str(error) for error in meta["docling_structure"]["errors"])
        page_errors.extend(self._ocr_failures)
        blocks = drop_blocks_inside_tables(blocks, tables, self.layout_engine)
        table_duration = time.perf_counter() - t_table_start - docling_duration

        t_ro_start = time.perf_counter()
        reading_order = self.config.get("reading_order", {})
        order_sorter = ReadingOrderSorter(page_width=page_w, page_height=page_h,
                                          method=reading_order.get("method", "heuristic"),
                                          min_gap=reading_order.get("min_gap", 3.0))
        order_sorter.classify_furniture = self.legacy_furniture
        sorted_blocks = order_sorter.sort_blocks(blocks)
        if reading_order:  # explicit choice (C3.4 experiment) is recorded
            meta["reading_order"] = {"method": order_sorter.method, "min_gap": order_sorter.min_gap,
                                     **({"version": XY_CUT_METHOD} if order_sorter.method == "xycut" else {})}
        reading_order_duration = time.perf_counter() - t_ro_start

        block_regions = link_blocks_to_regions(sorted_blocks, regions, page_num)
        assign_region_statuses(regions, block_regions, tables, meta.get("orientation_fallback"))
        total_time = round(time.perf_counter() - t0, 2)
        durations = {"inspect": inspect_duration, "native_extract": 0.0, "image_region_ocr": 0.0,
                     "render_and_refine": render_duration, "primary_ocr": ocr_duration, "fallback": fallback_duration,
                     "primary_detector_evidence": detector_evidence_duration,
                     "table": table_duration, "docling_structure": docling_duration,
                     "layout_model": layout_model_duration, "reading_order": reading_order_duration}
        quality_report, calibrated_q = build_page_quality(
            page_num=page_num, page_type=page_type, native_score=native_score, total_time=total_time,
            recognizer_confidences=recognizer_confidences, sorted_blocks=sorted_blocks, tables=tables,
            diagrams=diagrams, regions=regions, rejected_evidence=rejected_evidence,
            candidates_for_fallback=candidates_for_fallback, suspicious_lines=suspicious_lines,
            ocr_calls=self._current_page_ocr_calls, page_errors=page_errors, recognized_lines=recognized_lines,
            fallback_count=retry_record["improved"], durations=durations, meta=meta, allow_vector_tables=allow_vector_tables,
            region_type_counts=region_type_counts)
        evidence_blocks = build_evidence_blocks(sorted_blocks, regions, page_num, rejected_evidence)
        extracted_page = ExtractedPage(
            page_num=page_num, width=page_w, height=page_h, page_type=page_type, rendered_dpi=actual_dpi,
            blocks=sorted_blocks, tables=tables, formulas=formulas, diagrams=diagrams, regions=regions,
            evidence_blocks=evidence_blocks, quality=quality_report, metadata=meta)
        extracted_page.markdown_content = MarkdownExporter.export_page_markdown(extracted_page)
        quality_text = f"{calibrated_q:.2f}" if calibrated_q is not None else "n/a"
        progress(f"[{page_num}/{total_pages}] quality={quality_text} fallback={retry_record['improved']}/{len(sorted_blocks)}", stage="quality")
        progress(f"[{page_num}/{total_pages}] completed time={total_time:.2f}s", stage="complete", seconds=round(total_time, 3))
        return extracted_page

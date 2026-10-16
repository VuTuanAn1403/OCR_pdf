from __future__ import annotations
import sys
import time
import math
import re
import hashlib
import json
import logging
from typing import Dict, Any, Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, wait as wait_for_futures
import numpy as np
import pymupdf
import cv2

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from src.ocr.domain.models.page import ExtractedPage
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.models.quality import PageQualityReport
from src.ocr.domain.models.region import ContentType, Region, BoundingBox
from src.ocr.domain.models.evidence_block import EvidenceBlock
from src.ocr.domain.routing.page_type import PageType
from src.ocr.infrastructure.pdf.pdf_inspector import PDFInspector
from src.ocr.infrastructure.pdf.pdf_renderer import PDFRenderer
from src.ocr.infrastructure.pdf.region_cropper import RegionCropper
from src.ocr.infrastructure.pdf.native_text_extractor import NativeTextExtractor
from src.ocr.infrastructure.ocr.vietnamese_seq2seq_engine import VietnameseSeq2SeqEngine
from src.ocr.infrastructure.ocr.rapidocr_engine import RapidOCREngine
from src.ocr.infrastructure.ocr.fallback_engine import FallbackEngine
from src.ocr.infrastructure.quality.ocr_quality import OCRQualityEvaluator
from src.ocr.infrastructure.layout.document_layout_engine import DocumentLayoutEngine
from src.ocr.infrastructure.layout.page_content_analyzer import PageContentAnalyzer
from src.ocr.infrastructure.postprocessing.reading_order import READING_ORDER_METHODS, ReadingOrderSorter
from src.ocr.infrastructure.postprocessing.xy_cut import METHOD as XY_CUT_METHOD
from src.ocr.infrastructure.pdf.scan_image import (
    acquire_page_image, full_page_image_info, validate_settings as validate_scan_image_settings)
from src.ocr.infrastructure.quality.ocr_layer_agreement import (
    METHOD as OCR_LAYER_METHOD, measure_agreement as measure_ocr_layer_agreement)
from src.ocr.infrastructure.postprocessing.unicode_normalizer import UnicodeNormalizer
from src.ocr.infrastructure.postprocessing.text_cleaner import TextCleaner
from src.ocr.infrastructure.quality.vietnamese_quality import VietnameseQualityEvaluator
from src.ocr.infrastructure.cache.artifact_cache import ArtifactCache
from src.ocr.application.route_page import RoutePageUseCase
from src.ocr.application.reocr_region import ReocrRegionUseCase
from src.ocr.infrastructure.export.markdown_exporter import MarkdownExporter
from src.ocr.infrastructure.layout.docling_structure import (
    DoclingStructureExtractor, DoclingStructureError, block_is_covered,
)
from src.ocr.infrastructure.export.manifest_exporter import PIPELINE_VERSION
from src.ocr.domain.models.schema import PAGE_SCHEMA_VERSION
from src.ocr.runtime.config import ConfigError, config_hash
from src.ocr.runtime.fingerprint import source_fingerprint
from src.ocr.runtime.models import model_fingerprint
from src.ocr.runtime.structured_logging import log_context, progress
from src.ocr.infrastructure.layout.dla_engine import DLAEngine

def _known(values) -> List[float]:
    """Drop unavailable (None) scores."""
    return [v for v in values if v is not None]


def _mean(values) -> Optional[float]:
    known = _known(values)
    return sum(known) / len(known) if known else None


def _score_or(value: Optional[float], default: float) -> float:
    return default if value is None else value


class ProcessPageUseCase:
    """
    Orchestrates extraction for a single PDF page:
    Route -> Native Extract or OCR -> Quality Gate -> Regional Fallback -> Layout/Tables -> Reading Order -> Cache.
    """
    _current_page_ocr_calls: int = 0
    _page_errors: int = 0
    _crop_cache: Dict[str, Any] = {}
    _rejected_evidence: List[Any] = []

    def __init__(
        self,
        doc: pymupdf.Document,
        config: Dict[str, Any],
        doc_hash: str,
        cache: Optional[ArtifactCache] = None,
        structure_extractor: Optional[DoclingStructureExtractor] = None,
    ):
        self.doc = doc
        self.config = config
        from src.ocr.runtime.config import document_layout_settings, furniture_settings
        self.document_layout = document_layout_settings(config)
        self.legacy_furniture = furniture_settings(self.document_layout)["method"] == "legacy_v6_1"
        self.doc_hash = doc_hash
        self.cache = cache

        self.inspector = PDFInspector(doc)
        self.renderer = PDFRenderer(doc)
        self.native_extractor = NativeTextExtractor(doc)
        primary_engine = config.get("primary_engine", "vietocr")
        threads = config.get("num_threads")
        if threads is not None:
            import torch
            torch.set_num_threads(threads)
            cv2.setNumThreads(threads)
        if primary_engine == "rapidocr":
            self.ocr_engine = RapidOCREngine(num_threads=threads) if threads else RapidOCREngine()
        elif primary_engine == "vietocr":
            detector_args = {}
            if config.get("text_detector"):
                from src.ocr.infrastructure.ocr.text_detector import ONNXTextDetector
                detector_args["detector"] = ONNXTextDetector(config["text_detector"], config.get("device", "cpu"),
                                                           num_threads=threads or 2)
            self.ocr_engine = VietnameseSeq2SeqEngine(
                batch_size=config.get("batch_size", 16), device=config.get("device"),
                **detector_args,
                **({"num_threads": threads} if threads is not None else {}))
        else:
            raise ValueError(f"Unsupported primary_engine: {primary_engine}")
        self.ocr_quality = OCRQualityEvaluator(min_confidence=config.get("min_confidence", 0.80))
        self.layout_engine = DocumentLayoutEngine()
        self._owns_structure_extractor = structure_extractor is None
        self.structure_extractor = structure_extractor or DoclingStructureExtractor(config.get("docling", {}))
        layout_config = config.get("layout_model", {})
        self.content_analyzer = PageContentAnalyzer(DLAEngine(
            model_path=layout_config.get("model_path"),
            model_config=layout_config,
            device=config.get("device", "cpu"),
            num_threads=layout_config.get("num_threads", config.get("num_threads", 4)),
            legacy_furniture=self.legacy_furniture,
        ))
        self.fallback_engine = FallbackEngine(
            upscale_dpi=config.get("fallback_upscale_dpi", 250),
            ocr_engine=self.ocr_engine,
        )
        self.reocr_use_case = ReocrRegionUseCase(self.renderer, self.fallback_engine)
        native_first = config.get("native_first", True)
        if not isinstance(native_first, bool):
            # A YAML string such as "false" would otherwise be truthy.
            raise ConfigError(f"native_first must be true or false, got {native_first!r}")
        self.native_first = native_first
        if not isinstance(config.get("retain_table_ocr_lines", False), bool):
            raise ConfigError("retain_table_ocr_lines must be true or false")
        if "native_review_threshold" in config:
            # Never read; keeping it would suggest a second native gate.
            raise ConfigError("native_review_threshold is not supported; set native_quality.review_threshold")
        reading_order = config.get("reading_order", {})
        if not isinstance(reading_order, dict) or set(reading_order) - {"method", "min_gap"} \
                or reading_order.get("method", "heuristic") not in READING_ORDER_METHODS:
            raise ConfigError(f"reading_order accepts method ({', '.join(READING_ORDER_METHODS)}) and min_gap")
        if "scan_image" in config:
            try:
                validate_scan_image_settings(config["scan_image"])
            except ValueError as exc:
                raise ConfigError(str(exc)) from exc
        agreement = config.get("ocr_layer_agreement", {})
        if not isinstance(agreement, dict) or set(agreement) - {"sample_lines"} \
                or type(agreement.get("sample_lines", 5)) is not int or not 3 <= agreement.get("sample_lines", 5) <= 5:
            raise ConfigError("ocr_layer_agreement accepts sample_lines 3-5 (proposal C2.1)")
        self._current_page_ocr_calls = 0
        self._page_errors = 0
        self._crop_cache: Dict[str, Any] = {}
        self._rejected_evidence: List[Any] = []
        self.cache_key = self._build_cache_key()

    def _build_cache_key(self) -> str:
        """Profile name plus a digest of everything that changes page output
        (ENV-04): resolved config, schema, code and Docling fingerprints."""
        parts = {
            "config": config_hash(self.config),
            "schema": PAGE_SCHEMA_VERSION,
            "pipeline": PIPELINE_VERSION,
            "code": source_fingerprint(),
            "models": model_fingerprint(self.config),
            "docling": self.structure_extractor.cache_fingerprint() if self.structure_extractor.enabled else None,
        }
        digest = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:16]
        return f"{self.config.get('profile', 'balanced')}-{digest}"

    def close(self) -> None:
        executor = getattr(self, "_ocr_executor", None)
        if executor is not None:
            executor.shutdown(wait=True)
            self._ocr_executor = None
        if getattr(self, "_owns_structure_extractor", True):
            self.structure_extractor.close()

    def _apply_layout_model(self, doc_page, page_num, blocks, regions, meta, image=None):
        """Run optional D1 in-process, retaining geometric regions on failure.

        Model candidates annotate text and supplement structure candidates;
        they do not remove OCR content or gate Docling before pilot validation.
        """
        engine = self.content_analyzer.dla_engine
        if engine.model_path is None:
            meta["layout_model"] = {"status": "HEURISTIC_ONLY"}
            return
        meta["layout_model"] = {"adapter": engine.adapter_kind, "model_path": str(engine.model_path),
                                "device": engine.device, "status": "FAILED_FALLBACK"}
        if (meta.get("is_1up_landscape") or meta.get("is_180_rotated")
                or getattr(doc_page, "rotation", 0)):
            # Legacy OCR can retain boxes in an upright page space. Until all
            # transforms are invertible, do not merge original-PDF model boxes.
            meta["layout_model"].update(status="SKIPPED_COORDINATE_TRANSFORM_REQUIRED",
                                        reason="Legacy rotated OCR/PDF coordinates need an explicit transform")
            if getattr(engine, "strict", False) is True:
                raise RuntimeError("Strict GPU layout requires a verified coordinate transform for this rotated page")
            return
        if engine._session is None:
            meta["layout_model"]["error"] = engine.last_error or "ONNX session unavailable"
            if getattr(engine, "strict", False) is True:
                raise RuntimeError(meta["layout_model"]["error"])
            return
        started = time.perf_counter()
        try:
            if image is None:
                image = self.renderer.render_page(page_num, dpi=self.config.get("layout_model", {}).get("render_dpi", 144))
            if isinstance(doc_page, pymupdf.Page):
                from src.ocr.infrastructure.pdf.coordinate_transform import render_coordinate_chain
                meta["layout_model"]["render_transform"] = render_coordinate_chain(doc_page, image.shape).to_dict()
            predicted = engine._run_onnx_dla(image, doc_page, page_num)
            text_labels = {"title", "section_header", "text", "header", "footer", "page_number", "caption", "list", "document_index"}
            for block in blocks:
                if len(block.bbox) != 4 or block.block_type == "formula":
                    continue
                bb = BoundingBox(x0=block.bbox[0], y0=block.bbox[1], x1=block.bbox[2], y1=block.bbox[3])
                matches = [r for r in predicted if r.label in text_labels and r.bbox.overlap_ratio(bb) >= .7
                           and r.bbox.contains_point((bb.x0 + bb.x1) / 2, (bb.y0 + bb.y1) / 2)]
                if matches:
                    region = max(matches, key=lambda r: (_score_or(r.dla_score, 0.0), -r.bbox.area))
                    block.block_type = region.label
            regions.extend(predicted)
            meta["layout_model"].update(status="MODEL_OUTPUT", regions=len(predicted),
                                        providers=engine._session.get_providers(),
                                        coordinates="PDF_POINTS_TOP_LEFT_CROP_RELATIVE",
                                        validated_on_pilot=False)
        except Exception as exc:
            if getattr(engine, "strict", False) is True:
                raise
            meta["layout_model"]["error"] = str(exc)
            logger.warning("Page %d: layout model failed; retaining geometric regions: %s", page_num, exc)
        finally:
            meta["layout_model"]["seconds"] = round(time.perf_counter() - started, 4)

    def _enrich_structures(self, doc_page, page_num, page_type, blocks, tables, regions, meta):
        extractor = self.structure_extractor
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
        # Populate empty cell text from high-accuracy native or VietOCR blocks
        for table in model_tables:
            for cell in table.cells:
                if not cell.text.strip() and cell.bbox and len(cell.bbox) == 4:
                    cx0, cy0, cx1, cy1 = cell.bbox
                    matched = [
                        b.text.strip() for b in blocks
                        if len(b.bbox) == 4 and cx0 <= (b.bbox[0] + b.bbox[2]) / 2.0 <= cx1
                        and cy0 <= (b.bbox[1] + b.bbox[3]) / 2.0 <= cy1
                        and b.text.strip()
                    ]
                    if matched:
                        cell.text = " ".join(matched)
        # Replace only overlapping heuristic tables. Unmatched tables and text
        # remain, including when Docling finds no structures.
        tables = [table for table in tables if not block_is_covered(table.bbox, model_tables, 0.5)] + model_tables
        # Freeze formula ownership at the point where the original text is
        # replaced. LaTeX need not equal the OCR transcription byte-for-byte.
        for block in blocks:
            if re.fullmatch(r"\(\d+(?:\.\d+)*\)", block.text.strip()):
                continue  # equation labels are independent visible evidence
            owners = [formula for formula in formulas if block_is_covered(block.bbox, [formula])]
            if len(owners) == 1 and block.source_id:
                parents = owners[0].provenance.setdefault("derived_from_source_ids", [])
                if block.source_id not in parents:
                    parents.append(block.source_id)
                owners[0].provenance["source_mapping_method"] = "docling-formula-absorption-v1"
        blocks = [block for block in blocks if not block_is_covered(block.bbox, model_tables)
                  and (re.fullmatch(r"\(\d+(?:\.\d+)*\)", block.text.strip())
                       or not block_is_covered(block.bbox, formulas))]
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

    @staticmethod
    def _full_page_image(doc_page) -> Optional[Dict[str, Any]]:
        if not isinstance(doc_page, pymupdf.Page):
            return None
        try:
            return full_page_image_info(doc_page)
        except Exception as exc:  # provenance only; never fails the page
            logger.warning("Full-page image inspection failed: %s", exc)
            return {"status": "UNAVAILABLE", "error": f"{type(exc).__name__}: {exc}"}

    def _ocr_layer_agreement(self, doc_page, blocks, meta) -> Dict[str, Any]:
        # "orientation_uncertain" keeps the original image; only rotate_* labels
        # (also per region, "<id>:rotate_90_cw") move OCR boxes.
        if ("rotate" in str(meta.get("orientation_fallback") or "") or meta.get("is_1up_landscape")
                or meta.get("is_180_rotated") or getattr(doc_page, "rotation", 0)):
            # OCR boxes are then in an upright space, not the layer's PDF space.
            return {"method": OCR_LAYER_METHOD, "status": "SKIPPED_COORDINATE_TRANSFORM_REQUIRED"}
        try:
            return measure_ocr_layer_agreement(
                doc_page, blocks, self.config.get("ocr_layer_agreement", {}).get("sample_lines", 5))
        except Exception as exc:
            logger.warning("OCR-layer agreement check failed: %s", exc, exc_info=True)
            return {"method": OCR_LAYER_METHOD, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}

    def _is_native_eligible(self, page_type: PageType, native_score: float, meta: Dict[str, Any]) -> bool:
        if meta.get("native_extraction_failed", False):
            return False
        if page_type in (PageType.NATIVE_TEXT, PageType.MIXED):
            # Same key RoutingPolicy uses (one threshold source, ENV-13).
            return native_score >= self.config.get("native_quality", {}).get("review_threshold", 0.70)
        return (page_type == PageType.PRODUCT_SHOWCASE_COLLAGE and meta.get("char_count", 0) >= 30
                and not meta.get("unicode_corruption_detected", False))

    def _record_ocr_failure(self, message: str) -> None:
        self._page_errors = getattr(self, "_page_errors", 0) + 1
        if not hasattr(self, "_ocr_failures"):
            self._ocr_failures = []
        self._ocr_failures.append(message)

    def _predict_image_counted(
        self, img_bgr: np.ndarray, timeout_seconds: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        self._current_page_ocr_calls = getattr(self, "_current_page_ocr_calls", 0) + 1
        timeout = timeout_seconds or getattr(self, "_active_page_deadline", None)
        if timeout is not None and timeout > 0:
            # Reuse the inference thread for the whole document. Native CPU
            # runtimes may allocate thread-local pools on each calling thread;
            # a fresh executor for every page/retry causes resource churn.
            if getattr(self, "_ocr_executor", None) is None:
                self._ocr_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr-inference")
            future = self._ocr_executor.submit(self.ocr_engine.predict_image, img_bgr)
            try:
                return future.result(timeout=timeout)
            except FutureTimeoutError:
                logger.error("OCR exceeded %.2fs; waiting for in-flight inference to finish", timeout)
                self._record_ocr_failure(f"OCR timed out after {timeout:.2f}s")
                # This remains a soft timeout: threads cannot stop native
                # inference. Drain it before fallback touches the shared model,
                # matching the previous executor context's shutdown(wait=True).
                wait_for_futures([future])
                return []
            except Exception as e:
                logger.exception("OCR execution failed")
                # Reported as a page error so an OCR crash is never
                # mistaken for a page that simply has no text.
                self._record_ocr_failure(f"OCR failed: {type(e).__name__}: {e}")
                return []
        return self.ocr_engine.predict_image(img_bgr)

    def execute(
        self,
        page_num: int,
        total_pages: int,
        router: RoutePageUseCase,
        resume: bool = False
    ) -> ExtractedPage:
        with log_context(page=page_num):  # ENV-11: every page log names its page
            return self._execute_page(page_num, total_pages, router, resume)

    def _execute_page(self, page_num: int, total_pages: int, router: RoutePageUseCase,
                      resume: bool) -> ExtractedPage:
        t0 = time.perf_counter()
        self._current_page_ocr_calls = 0
        self._page_errors = 0
        self._ocr_failures: List[str] = []
        cache_profile = self.cache_key
        render_dpi = self.config.get("render_dpi", 180)
        page_timeout_seconds = float(self.config.get("page_timeout_seconds", 120))
        self._active_page_deadline = page_timeout_seconds

        # 1. Check cache if resume is active
        if resume and self.cache:
            cached = self.cache.get_cached_page(self.doc_hash, page_num, cache_profile, render_dpi)
            if cached is not None:
                cached.quality.from_cache = True
                progress(f"[{page_num}/{total_pages}] page={page_num} [CACHED] restored from cache", stage="cache")
                return cached

        # 2. Inspect page metadata
        meta = self.inspector.inspect_page(page_num)
        page_type, native_score = router.execute(meta)

        page_w = meta["width"]
        page_h = meta["height"]

        # 2b. V3.1.2 Content Composition — decompose page into regions
        doc_page = self.doc[page_num - 1]
        regions = self.content_analyzer.analyze(meta, doc_page)
        inspect_duration = time.perf_counter() - t0
        native_extract_duration = 0.0
        render_duration = 0.0
        ocr_duration = 0.0
        image_region_ocr_duration = 0.0
        orientation_retry_duration = 0.0
        fallback_duration = 0.0
        table_duration = 0.0
        reading_order_duration = 0.0
        recognized_lines = 0
        recognizer_confidences: List[float] = []
        from collections import Counter
        region_type_counts = dict(Counter(r.content_type.value for r in regions))

        progress(f"[{page_num}/{total_pages}] page={page_num} type={page_type.value} dpi={render_dpi} regions={len(regions)} {region_type_counts}",
                 stage="route", page_type=page_type.value, regions=region_type_counts)

        blocks: List[TextBlock] = []
        self._current_page_diagrams: List[Any] = []
        self._rejected_evidence: List[EvidenceBlock] = []
        page_errors: List[str] = []
        fallback_count = 0
        suspicious_lines: List[str] = []
        candidates_for_fallback: List[TextBlock] = []
        actual_dpi = None
        page_timeout_seconds = float(self.config.get("page_timeout_seconds", 120))

        is_native_eligible = self._is_native_eligible(page_type, native_score, meta)
        # Ablation A1 (proposal 9.4): `native_first: false` OCRs every page and
        # also disables the defensive native recovery and vector tables below.
        native_first = getattr(self, "native_first", True)
        if not native_first:
            is_native_eligible = False
            meta["routing_override"] = "native_first_disabled"

        if is_native_eligible:
            t_nat_start = time.perf_counter()
            # Extract digital native text directly (accuracy-first, 0 OCR corruption)
            blocks = self.native_extractor.extract_blocks(page_num)
            for b in blocks:
                b.quality_score = round(native_score, 4)
                b.confidence = round(native_score, 4)
                b.confidence_source = "native_text_score"
                if b.bbox and len(b.bbox) >= 4:
                    dla = getattr(self.content_analyzer, "dla_engine", None)
                    if dla and hasattr(dla, "_classify_text_block"):
                        try:
                            res = dla._classify_text_block(
                                text=b.text,
                                bbox=BoundingBox(x0=b.bbox[0], y0=b.bbox[1], x1=b.bbox[2], y1=b.bbox[3]),
                                page_h=page_h,
                                page_w=page_w,
                                page_num=page_num,
                                base_font_size=11.0,
                                header_band_y=page_h * 0.10,
                                footer_band_y=page_h * 0.90,
                            )
                            if isinstance(res, (tuple, list)) and len(res) >= 2:
                                b.block_type = res[1]
                        except Exception as e:
                            logger.warning("Page %d layout classification failed for native block: %s", page_num, e)

            if not blocks and page_type == PageType.MIXED:
                # Defensive fallback: If native extractor returned 0 blocks on mixed page,
                # fall through to OCR to avoid content loss.
                is_native_eligible = False
            native_extract_duration = time.perf_counter() - t_nat_start

        if is_native_eligible:
            image_ocr_blocks: List[TextBlock] = []
            if (time.perf_counter() - t0) > page_timeout_seconds:
                logger.warning("Page %d exceeded timeout (%ss) before image region OCR", page_num, page_timeout_seconds)
                page_errors.append(f"Page timeout ({page_timeout_seconds}s) exceeded before image OCR")
            else:
                # Determine which regions require regional OCR (V4.0 Pillar 2: Vector & Diagram preservation):
                page_area = max(1.0, page_w * page_h)
                if page_type == PageType.PRODUCT_SHOWCASE_COLLAGE:
                    target_regions = [
                        r for r in regions
                        if r.content_type in (ContentType.IMAGE_TABLE, ContentType.DIAGRAM)
                        or (r.content_type in (ContentType.IMAGE, ContentType.FIGURE)
                            and r.bbox.area / page_area >= 0.02)
                    ]
                elif page_type == PageType.MIXED or meta.get("layout_type") in ("TWO_UP", "MULTI_REGION"):
                    target_regions = [
                        r for r in regions
                        if r.content_type in (ContentType.IMAGE, ContentType.IMAGE_TABLE, ContentType.VECTOR, ContentType.DIAGRAM)
                    ]
                else:
                    target_regions = [
                        r for r in regions
                        if r.content_type in (ContentType.IMAGE_TABLE, ContentType.DIAGRAM)
                        or (r.content_type in (ContentType.IMAGE, ContentType.FIGURE)
                            and r.bbox.area / page_area >= 0.20)
                    ]

                t_img_ocr = time.perf_counter()
                image_ocr_blocks = self._extract_image_regions(
                    doc_page,
                    page_num,
                    target_regions,
                    blocks,
                    render_dpi,
                    evaluate_all_orientations=meta.get("layout_type") in ("TWO_UP", "MULTI_REGION"),
                )
                image_region_ocr_duration = time.perf_counter() - t_img_ocr
                if image_ocr_blocks:
                    blocks.extend(image_ocr_blocks)
                    recognized_lines += len(image_ocr_blocks)
                    recognizer_confidences.extend(_known(b.confidence for b in image_ocr_blocks))

            if page_type == PageType.MIXED:
                progress(f"[{page_num}/{total_pages}] mixed composition: native={len(blocks) - len(image_ocr_blocks)} image_ocr={len(image_ocr_blocks)} lines", stage="native")
            elif image_ocr_blocks:
                progress(f"[{page_num}/{total_pages}] native+image_table: native={len(blocks) - len(image_ocr_blocks)} image_ocr={len(image_ocr_blocks)} lines", stage="native")
            else:
                progress(f"[{page_num}/{total_pages}] native={len(blocks)} lines (native text gate passed: {native_score:.2f})", stage="native")

        else:
            # Route to full-page OCR (scanned pages, corrupted native text, low-quality OCR layers)
            actual_dpi = render_dpi
            t_render_start = time.perf_counter()
            # Source DPI of the page image (C2.2): metadata for DPI-group reports.
            meta["full_page_image"] = self._full_page_image(doc_page)
            if self.config.get("scan_image"):  # opt-in embedded image / DPI policy (R-08)
                img_bgr, meta["scan_image"] = acquire_page_image(
                    self.doc, page_num, self.renderer, render_dpi, self.config["scan_image"])
                actual_dpi = meta["scan_image"]["processing_dpi"]
            else:
                img_bgr = self.renderer.render_page(page_num, dpi=render_dpi)
            refined_regions = regions
            if self.config.get("two_up_detection_enabled", True) or self.config.get("multi_region_detection_enabled", True):
                refined_regions = self.content_analyzer.refine_with_rendered_image(
                    meta, regions, img_bgr
                )
            if refined_regions:
                regions = refined_regions
                region_type_counts = dict(Counter(r.content_type.value for r in regions))
                meta["region_count"] = len(regions)
            render_duration = time.perf_counter() - t_render_start
            t_ocr_start = time.perf_counter()
            if (self.config.get("ocr_source_policy") != "gpu_ocr_only_v1"
                    and meta.get("layout_type") in ("TWO_UP", "MULTI_REGION") and len(regions) >= 2):
                ocr_results, orientation_fallback = self._predict_region_ocr(
                    img_bgr,
                    regions,
                    page_w,
                    page_h,
                    enabled=self.config.get("orientation_fallback_enabled", True),
                )
            else:
                ocr_results, orientation_fallback = self._predict_with_orientation_fallback(
                    img_bgr,
                    enabled=self.config.get("orientation_fallback_enabled", True),
                )
            if orientation_fallback:
                meta["orientation_fallback"] = orientation_fallback
                progress(f"[{page_num}/{total_pages}] orientation_fallback={orientation_fallback}", stage="ocr")
            ocr_duration = time.perf_counter() - t_ocr_start
            recognized_lines = len(ocr_results)
            recognizer_confidences = _known(result.get("confidence") for result in ocr_results)
            progress(f"[{page_num}/{total_pages}] primary={len(ocr_results)} lines time={ocr_duration:.2f}s", stage="ocr",
                     lines=len(ocr_results), seconds=round(ocr_duration, 3))

            # Check if this is a 1-Up landscape scan (full-page scan rotated 90 deg) or 180 deg scan
            is_1up_rotated_90 = (
                meta.get("layout_type") not in ("TWO_UP", "MULTI_REGION")
                and orientation_fallback in ("rotate_90_cw", "rotate_90_ccw")
            )
            is_1up_rotated_180 = (
                meta.get("layout_type") not in ("TWO_UP", "MULTI_REGION")
                and orientation_fallback == "rotate_180"
            )

            if is_1up_rotated_90:
                # Upright coordinate space: Swap page dimensions to effective landscape
                page_w, page_h = page_h, page_w
                meta["effective_width"] = page_w
                meta["effective_height"] = page_h
                meta["is_1up_landscape"] = True
                meta["page_orientation"] = "LANDSCAPE"
                # In 90 deg rotation, rotated image width is img_bgr.shape[0] and height is img_bgr.shape[1]
                rot_w = img_bgr.shape[0]
                rot_h = img_bgr.shape[1]
                scale_x = page_w / max(1.0, rot_w)
                scale_y = page_h / max(1.0, rot_h)
            elif is_1up_rotated_180:
                meta["is_180_rotated"] = True
                rot_w = img_bgr.shape[1]
                rot_h = img_bgr.shape[0]
                scale_x = page_w / max(1.0, rot_w)
                scale_y = page_h / max(1.0, rot_h)
            else:
                scale_x = page_w / max(1.0, img_bgr.shape[1])
                scale_y = page_h / max(1.0, img_bgr.shape[0])

            # Convert OCR results to TextBlocks and evaluate quality
            for idx, res in enumerate(ocr_results):
                raw_bbox = res.get("raw_bbox") if ((is_1up_rotated_90 or is_1up_rotated_180) and "raw_bbox" in res) else res["bbox"]
                scaled_bbox = [
                    round(raw_bbox[0] * scale_x, 2),
                    round(raw_bbox[1] * scale_y, 2),
                    round(raw_bbox[2] * scale_x, 2),
                    round(raw_bbox[3] * scale_y, 2)
                ]
                text = UnicodeNormalizer.normalize(res["text"])
                conf = res.get("confidence")

                # Filter hallucination / low confidence (A1)
                is_valid, reject_reason = self.classify_ocr_hallucination(text, conf)
                if not is_valid:
                    mid_x = (scaled_bbox[0] + scaled_bbox[2]) / 2.0
                    mid_y = (scaled_bbox[1] + scaled_bbox[3]) / 2.0
                    matching_region = next((r for r in regions if r.bbox.contains_point(mid_x, mid_y)), None)
                    if matching_region is None and regions:
                        matching_region = regions[0]
                    if matching_region is None:
                        matching_region = Region(
                            region_id=f"p{page_num}_rej_{len(self._rejected_evidence) + 1}",
                            bbox=BoundingBox(x0=scaled_bbox[0], y0=scaled_bbox[1], x1=scaled_bbox[2], y1=scaled_bbox[3]),
                            content_type=ContentType.IMAGE,
                            source="ocr",
                            orientation_status="UNCERTAIN",
                            failure_reason="Region inferred for rejected OCR candidate",
                        )
                        regions.append(matching_region)

                    self._rejected_evidence.append(EvidenceBlock(
                        block_id=f"B_{page_num:04d}_REJ_{len(self._rejected_evidence) + 1:04d}",
                        page=page_num,
                        region_id=matching_region.region_id,
                        bbox=scaled_bbox,
                        bbox_original=scaled_bbox,
                        bbox_normalized=scaled_bbox,
                        text=text,
                        content_type=ContentType.IMAGE,
                        source="ocr",
                        confidence=conf,
                        orientation=0.0,
                        quality_score=conf,
                        block_type="noise",
                        is_bold=False,
                        font_size=None,
                        reading_order=0,
                        status=reject_reason,
                        failure_reason=f"Rejected: {reject_reason} (conf={conf})",
                    ))
                    continue

                q_score, needs_fb, reasons = self.ocr_quality.evaluate_ocr_result(text, conf)
                if res.get("needs_review"):
                    needs_fb = True
                    q_score = min(q_score, .74) if q_score is not None else None
                    reasons.append(res.get("review_reason", "OCR backend requested review"))

                # A4: Numeric Verification Retry for profile balanced/accuracy
                num_threshold = float(self.config.get("numeric_retry_threshold", 0.85))
                contains_digit = bool(re.search(r"\d", text))
                if contains_digit and conf is not None and conf < num_threshold:
                    needs_fb = True
                    reasons.append(f"Numeric token with confidence {conf:.2f} < {num_threshold:.2f} (A4 check)")

                block = TextBlock(
                    text=text,
                    text_raw=res.get("text_raw", res["text"]),
                    correction_source=(res.get("correction_source") or "unicode_normalizer")
                        if text != res.get("text_raw", res["text"]) else None,
                    source="primary_ocr",
                    page=page_num,
                    bbox=scaled_bbox,
                    confidence=conf,
                    quality_score=q_score,
                    confidence_source="recognizer",
                    reviewed=False,
                    line_index=idx,
                    region_id=res.get("region_id"),
                    fallback_reason=res.get("review_reason") if res.get("needs_review") else None,
                )
                if scaled_bbox and len(scaled_bbox) >= 4:
                    dla = getattr(self.content_analyzer, "dla_engine", None)
                    if dla and hasattr(dla, "_classify_text_block"):
                        try:
                            c_res = dla._classify_text_block(
                                text=text,
                                bbox=BoundingBox(x0=scaled_bbox[0], y0=scaled_bbox[1], x1=scaled_bbox[2], y1=scaled_bbox[3]),
                                page_h=page_h,
                                page_w=page_w,
                                page_num=page_num,
                                base_font_size=11.0,
                                header_band_y=page_h * 0.10,
                                footer_band_y=page_h * 0.90,
                            )
                            if isinstance(c_res, (tuple, list)) and len(c_res) >= 2:
                                block.block_type = c_res[1]
                        except Exception as e:
                            logger.warning("Page %d layout classification failed for OCR block: %s", page_num, e)
                blocks.append(block)

                if needs_fb:
                    candidates_for_fallback.append(block)
                    suspicious_lines.append(f"{text} (reasons: {', '.join(reasons)})")

            if page_type == PageType.SCANNED_WITH_OCR_LAYER:
                # Before re-OCR changes any line: layer vs primary OCR (C2.1).
                meta["ocr_layer_agreement"] = self._ocr_layer_agreement(doc_page, blocks, meta)

            # 4. Adaptive Regional Fallback (if enabled)
            fallback_enabled = self.config.get("fallback_enabled", True) or self.config.get("fallback_only_on_review", False)
            if fallback_enabled and candidates_for_fallback:
                max_ratio = self.config.get("max_fallback_region_ratio", 0.15)
                max_count = self.config.get("max_fallback_regions_per_page", 20)
                total_lines = len(blocks)

                budget = min(max_count, max(1, math.ceil(total_lines * max_ratio))) if total_lines else 0
                if budget > 0:
                    t_fallback_start = time.perf_counter()
                    upscale_dpi = self.config.get("fallback_upscale_dpi", 250)
                    priority_candidates = sorted(
                        candidates_for_fallback,
                        key=lambda block: (_score_or(block.quality_score, 0.0), _score_or(block.confidence, 0.0)),
                    )[:budget]
                    _, improved = self.reocr_use_case.execute(
                        page_num=page_num,
                        blocks_to_retry=priority_candidates,
                        upscale_dpi=upscale_dpi
                    )
                    fallback_count = improved
                    fallback_duration = time.perf_counter() - t_fallback_start

            for candidate in candidates_for_fallback:
                if candidate.fallback_reason not in ("OCR candidates disagree on numeric text", "numeric_cleanup_rejected"):
                    continue
                if len(candidate.bbox) < 4:
                    continue
                mid_x = (candidate.bbox[0] + candidate.bbox[2]) / 2.0
                mid_y = (candidate.bbox[1] + candidate.bbox[3]) / 2.0
                for region in regions:
                    if region.bbox.contains_point(mid_x, mid_y):
                        region.needs_fallback = True
                        region.failure_reason = candidate.fallback_reason
                        break

        # Defensive Native Text Recovery: If OCR produced 0 blocks but the underlying PDF page
        # contains clean digital native text, recover native text to prevent accidental empty pages
        # and IMAGE_PRESERVED_NO_TEXT degeneration.
        if (native_first and not blocks and meta.get("char_count", 0) >= 20
                and not meta.get("unicode_corruption_detected", False)
                and not meta.get("has_hidden_ocr_layer") and not meta.get("hidden_text_only")):
            logger.info("Page %d: OCR returned 0 blocks but native text exists (%d chars). Applying defensive native recovery.", page_num, meta.get("char_count", 0))
            blocks = self.native_extractor.extract_blocks(page_num)
            for b in blocks:
                b.quality_score = round(native_score, 4)
                b.confidence = round(native_score, 4)
                b.confidence_source = "native_text_score"
                if b.bbox and len(b.bbox) >= 4:
                    dla = getattr(self.content_analyzer, "dla_engine", None)
                    if dla and hasattr(dla, "_classify_text_block"):
                        try:
                            res = dla._classify_text_block(
                                text=b.text,
                                bbox=BoundingBox(x0=b.bbox[0], y0=b.bbox[1], x1=b.bbox[2], y1=b.bbox[3]),
                                page_h=page_h,
                                page_w=page_w,
                                page_num=page_num,
                                base_font_size=11.0,
                                header_band_y=page_h * 0.10,
                                footer_band_y=page_h * 0.90,
                            )
                            if isinstance(res, (tuple, list)) and len(res) >= 2:
                                b.block_type = res[1]
                        except Exception as e:
                            logger.warning("Page %d layout classification failed for fallback native block: %s", page_num, e)

        # 5. Extract tables (doc_page already obtained during content analysis)
        self._apply_layout_model(doc_page, page_num, blocks, regions, meta,
                                 None if is_native_eligible else img_bgr)
        from src.ocr.infrastructure.structure.source_identity import lock_text_evidence
        from src.ocr.infrastructure.postprocessing.document_furniture import protect_table_continuation_lines
        # Protect continuation signals before any structure worker can absorb
        # text, and freeze evidence before sorting/removal changes list order.
        from types import SimpleNamespace
        if self.document_layout["method"] != "legacy":
            protect_table_continuation_lines([SimpleNamespace(page_num=page_num, blocks=blocks)])
            lock_text_evidence(blocks, self.doc_hash, page_num, meta)
        # Opt-in evidence for C3.5(a) experiments. Table extraction removes
        # covered blocks later; A and B must still receive the same OCR lines.
        if self.config.get("retain_table_ocr_lines", False):
            meta["table_ocr_evidence"] = {
                "method": "post-fallback-pre-table-v1",
                "lines": [b.to_dict() for b in blocks
                          if b.source in ("primary_ocr", "fallback_ocr", "image_ocr")],
            }
        layout_model_duration = meta.get("layout_model", {}).get("seconds", 0.0)
        t_table_start = time.perf_counter()
        allow_vector_tables = (
            native_first  # vector tables read native words; off in ablation A1
            and page_type in (PageType.NATIVE_TEXT, PageType.MIXED)
            and native_score >= self.config.get("vector_table_min_native_score", 0.90)
            and not meta.get("native_extraction_failed", False)
            and not (
                self.config.get("skip_vector_tables_on_restricted_pdf", True)
                and meta.get("copy_restricted", False)
            )
        )
        table_rejections: List[Dict[str, Any]] = []
        tables = self.layout_engine.extract_tables(
            doc_page,
            blocks,
            page_num,
            allow_vector_tables=allow_vector_tables,
            regions=regions,
            rejections=table_rejections,
        )
        if table_rejections:
            # Kept as text, not dropped: the review trail of C3.5(g).
            meta["table_rejections"] = table_rejections

        t_docling_start = time.perf_counter()
        blocks, tables, formulas = self._enrich_structures(
            doc_page, page_num, page_type, blocks, tables, regions, meta)
        docling_duration = time.perf_counter() - t_docling_start
        if meta["docling_structure"]["status"] == "FAILED_FALLBACK":
            page_errors.append(meta["docling_structure"]["error"])
        elif meta["docling_structure"].get("errors"):
            page_errors.extend(str(error) for error in meta["docling_structure"]["errors"])
        page_errors.extend(self._ocr_failures)

        # Filter out text blocks that fall inside legitimate tables to prevent duplication
        if tables:
            heuristic_tables = [t for t in tables if t.engine != "docling_tableformer"]
            model_tables = [t for t in tables if t.engine == "docling_tableformer"]
            blocks = [b for b in blocks if not (
                (heuristic_tables and self.layout_engine.is_block_inside_any_table(b.bbox, heuristic_tables))
                or block_is_covered(b.bbox, model_tables))]
        table_duration = time.perf_counter() - t_table_start - docling_duration

        # 6. Sort Reading Order
        t_ro_start = time.perf_counter()
        reading_order = self.config.get("reading_order", {})
        order_sorter = ReadingOrderSorter(page_width=page_w, page_height=page_h,
                                          method=reading_order.get("method", "heuristic"),
                                          min_gap=reading_order.get("min_gap", 3.0))
        order_sorter.classify_furniture = self.legacy_furniture
        sorted_blocks = order_sorter.sort_blocks(blocks)
        if reading_order:  # explicit choice (C3.4 experiment) is recorded; the default stays unchanged
            meta["reading_order"] = {"method": order_sorter.method, "min_gap": order_sorter.min_gap,
                                     **({"version": XY_CUT_METHOD} if order_sorter.method == "xycut" else {})}
        reading_order_duration = time.perf_counter() - t_ro_start

        block_regions = self._link_blocks_to_regions(sorted_blocks, regions, page_num)
        orientation_angle = {
            "rotate_90_cw": 90.0,
            "rotate_90_ccw": 270.0,
            "rotate_180": 180.0,
        }.get(meta.get("orientation_fallback"), 0.0)
        for region in regions:
            if region.region_id in block_regions:
                region.status = "NEEDS_REVIEW" if region.needs_fallback else "EXTRACTED"
                if region.orientation_status == "UNKNOWN":
                    region.rotation = orientation_angle
                    region.orientation_status = "CONFIDENT" if orientation_angle else "UNCERTAIN"
                    region.orientation_confidence = 0.8 if orientation_angle else 0.5
            elif any(
                table.bbox and len(table.bbox) >= 4
                and region.bbox.overlap_ratio(BoundingBox(
                    x0=table.bbox[0], y0=table.bbox[1],
                    x1=table.bbox[2], y1=table.bbox[3],
                )) > 0.5
                for table in tables
            ):
                region.status = "NEEDS_REVIEW" if region.needs_fallback else "EXTRACTED"
                if region.orientation_status == "UNKNOWN":
                    region.rotation = orientation_angle
                    region.orientation_status = "CONFIDENT" if orientation_angle else "UNCERTAIN"
                    region.orientation_confidence = 0.8 if orientation_angle else 0.5
            elif region.content_type in (ContentType.IMAGE, ContentType.IMAGE_TABLE, ContentType.FIGURE):
                region.status = "PRESERVED"
                if region.orientation_status == "UNKNOWN":
                    region.orientation_status = "UNCERTAIN"
                region.failure_reason = region.failure_reason or "Image preserved without recognized text"
            else:
                region.failure_reason = region.failure_reason or "No matching extracted block or table"

        total_time = round(time.perf_counter() - t0, 2)
        mean_conf = (
            sum(recognizer_confidences) / len(recognizer_confidences)
            if recognizer_confidences else None
        )
        # Weighted character average for quality score (Proposal R1). Blocks
        # without a calibrated score (model output) are excluded (C2.3d); a page
        # whose content has no measurable score gets None, not a constant.
        scored_blocks = [b for b in sorted_blocks if b.quality_score is not None]
        total_chars = sum(len(b.text.strip()) for b in scored_blocks)
        calibrated_q: Optional[float]
        if total_chars > 0:
            calibrated_q = sum(b.quality_score * len(b.text.strip()) for b in scored_blocks) / total_chars
        elif scored_blocks:
            calibrated_q = sum(b.quality_score for b in scored_blocks) / len(scored_blocks)
        elif tables:
            calibrated_q = _mean(t.confidence for t in tables)
        elif sorted_blocks:
            calibrated_q = None
        else:
            calibrated_q = 0.0

        native_q = _mean(b.quality_score for b in sorted_blocks if b.source == "native")
        ocr_q = _mean(b.quality_score for b in sorted_blocks
                      if b.source in ("primary_ocr", "image_ocr", "fallback_ocr"))
        known_q = _known(b.quality_score for b in sorted_blocks)
        min_reg_q = min(known_q) if known_q else (None if sorted_blocks else 1.0)
        low_conf_line_count = len(self._rejected_evidence) + sum(
            1 for b in sorted_blocks if b.confidence is not None and b.confidence < 0.60)

        if sorted_blocks or tables or self._current_page_diagrams:
            content_status, content_reason = "EXTRACTED", None
        elif any(r.content_type in (ContentType.IMAGE, ContentType.IMAGE_TABLE, ContentType.FIGURE) for r in regions):
            content_status = "IMAGE_PRESERVED_NO_TEXT"
            content_reason = "No text recognized; image is preserved for review"
        else:
            content_status = "UNRESOLVED_NO_CONTENT"
            content_reason = "No text, table, diagram, or image region was extracted"

        quality_report = PageQualityReport(
            page=page_num,
            page_type=page_type.value,
            native_text_score=round(native_score, 4),
            ocr_confidence=round(mean_conf, 4) if mean_conf is not None else None,
            ocr_confidence_samples=len(recognizer_confidences),
            quality_scored_blocks=len(scored_blocks),
            quality_scored_characters=total_chars,
            quality_score=round(calibrated_q, 4) if calibrated_q is not None else None,
            native_quality=round(native_q, 4) if native_q is not None else None,
            ocr_quality=round(ocr_q, 4) if ocr_q is not None else None,
            low_confidence_line_count=low_conf_line_count,
            min_region_quality=round(min_reg_q, 4) if min_reg_q is not None else None,
            ocr_calls=self._current_page_ocr_calls,
            errors=len(page_errors),
            total_blocks=len(sorted_blocks),
            recognized_lines=recognized_lines,
            fallback_count=fallback_count,
            review_required_count=(
                sum(not block.reviewed for block in candidates_for_fallback)
                + len(self._rejected_evidence)
                + sum(1 for r in regions if getattr(r, "needs_fallback", False))
            ),
            suspicious_lines=suspicious_lines[:10],
            time_taken_seconds=total_time,
            stage_seconds={
                "inspect": round(inspect_duration, 3),
                "native_extract": round(native_extract_duration, 3),
                "image_region_ocr": round(image_region_ocr_duration, 3),
                "render_and_refine": round(render_duration, 3),
                "primary_ocr": round(ocr_duration, 3),
                "fallback": round(fallback_duration, 3),
                "table": round(table_duration, 3),
                "docling_structure": round(docling_duration, 3),
                "layout_model": round(layout_model_duration, 3),
                "reading_order": round(reading_order_duration, 3),
                "postprocess": round(max(0.0, total_time - (inspect_duration + native_extract_duration + image_region_ocr_duration + render_duration + ocr_duration + fallback_duration + table_duration + docling_duration + layout_model_duration + reading_order_duration)), 3),
            },
            unicode_corruption_detected=bool(
                meta.get("unicode_corruption_detected", False)
                or meta.get("native_extraction_failed", False)
            ),
            native_extraction_failed=bool(meta.get("native_extraction_failed", False)),
            orientation_fallback=meta.get("orientation_fallback"),
            vector_table_probe_enabled=allow_vector_tables,
            region_count=len(regions),
            region_types=region_type_counts,
            content_status=content_status,
            content_reason=content_reason,
        )

        # 7. V3.1.2 EvidenceBlock intermediate representation (spec §3)
        evidence_blocks: List[EvidenceBlock] = []
        region_by_id = {r.region_id: r for r in regions}
        for idx, b in enumerate(sorted_blocks):
            src = "native" if b.source == "native" else "ocr"
            ct = ContentType.FORMULA if b.block_type == "formula" else (ContentType.TEXT if src == "native" else ContentType.IMAGE)
            block_region = region_by_id.get(b.region_id)
            rot = block_region.rotation if block_region else 0.0
            no_score = b.quality_score is None
            needs_review = no_score or b.quality_score < 0.75
            evidence_blocks.append(EvidenceBlock(
                block_id=f"B_{page_num:04d}_{idx + 1:04d}",
                page=page_num,
                region_id=b.region_id or f"p{page_num}_inferred",
                bbox=b.bbox if b.bbox else [],
                bbox_original=b.bbox if b.bbox else [],
                bbox_normalized=b.bbox if b.bbox else [],
                text=b.text,
                content_type=ct,
                source=src,
                confidence=b.confidence,
                confidence_source=b.confidence_source,
                orientation=rot,
                quality_score=b.quality_score,
                block_type=b.block_type or "paragraph",
                is_bold=b.is_bold,
                font_size=b.font_size,
                reading_order=idx + 1,
                status="UNRESOLVED" if needs_review else "EXTRACTED",
                failure_reason=("No calibrated confidence for model output" if no_score
                                else (b.fallback_reason or "Low OCR quality score") if needs_review else None),
            ))
        evidence_blocks.extend(self._rejected_evidence)

        extracted_page = ExtractedPage(
            page_num=page_num,
            width=page_w,
            height=page_h,
            page_type=page_type,
            rendered_dpi=actual_dpi,
            blocks=sorted_blocks,
            tables=tables,
            formulas=formulas,
            diagrams=self._current_page_diagrams,
            regions=regions,
            evidence_blocks=evidence_blocks,
            quality=quality_report,
            metadata=meta
        )

        # Export page markdown
        extracted_page.markdown_content = MarkdownExporter.export_page_markdown(extracted_page)

        # Save to cache
        if self.cache:
            if (meta.get("layout_model", {}).get("status") != "FAILED_FALLBACK"
                    and meta["docling_structure"]["status"] not in ("FAILED_FALLBACK", "partial_success")
                    and not meta["docling_structure"].get("errors")):
                self.cache.save_cached_page(self.doc_hash, extracted_page, cache_profile, render_dpi)

        quality_text = f"{calibrated_q:.2f}" if calibrated_q is not None else "n/a"
        progress(f"[{page_num}/{total_pages}] quality={quality_text} fallback={fallback_count}/{len(sorted_blocks)}", stage="quality")
        progress(f"[{page_num}/{total_pages}] completed time={total_time:.2f}s", stage="complete", seconds=round(total_time, 3))

        return extracted_page

    @staticmethod
    def _link_blocks_to_regions(
        blocks: List[TextBlock], regions: List[Region], page_num: int
    ) -> set[str]:
        linked: set[str] = set()
        for idx, block in enumerate(blocks):
            region = next((r for r in regions if r.region_id == block.region_id), None)
            if region is None and len(block.bbox) >= 4:
                mid_x = (block.bbox[0] + block.bbox[2]) / 2.0
                mid_y = (block.bbox[1] + block.bbox[3]) / 2.0
                candidates = [r for r in regions if r.bbox.contains_point(mid_x, mid_y)]
                preferred = (
                    (ContentType.TEXT, ContentType.OUTLINED_TEXT)
                    if block.source == "native"
                    else (ContentType.IMAGE, ContentType.IMAGE_TABLE, ContentType.TEXT)
                )
                candidates.sort(key=lambda r: (
                    0 if r.content_type in preferred else 1,
                    r.bbox.area,
                ))
                region = candidates[0] if candidates else None
            if region is None:
                bbox = block.bbox if len(block.bbox) >= 4 else [0.0, 0.0, 0.0, 0.0]
                region = Region(
                    region_id=f"p{page_num}_inferred_{idx + 1}",
                    bbox=BoundingBox(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3]),
                    content_type=ContentType.TEXT,
                    source="native" if block.source == "native" else "ocr",
                    orientation_status="UNCERTAIN",
                    failure_reason="Region inferred from text block bbox",
                )
                regions.append(region)
            block.region_id = region.region_id
            linked.add(region.region_id)
        return linked

    VN_LEXICON_WORDS = {
        "và", "các", "của", "trong", "năm", "cho", "tại", "báo", "cáo",
        "tài", "chính", "hợp", "nhất", "công", "ty", "cổ", "phần", "tiền",
        "phải", "thu", "trả", "chi", "phí", "hạn", "ngắn", "dài", "nợ",
        "số", "lượng", "thuyết", "minh", "tổng", "đầu", "tư", "giá", "trị",
        "dự", "phòng", "khách", "hàng", "bên", "liên", "quan", "xem", "không",
        "có", "vnd", "đồng", "stt", "ngày", "tháng", "kết", "thúc", "kinh",
        "doanh", "thương", "mại", "dịch", "vụ", "hoạt", "động", "lợi", "nhuận",
        "thuế", "tài", "sản", "ngân", "hàng", "khoản", "mục", "vay", "vốn",
        "chủ", "sở", "hữu", "lưu", "chuyển", "hàng", "tồn", "kho", "người",
        "đại", "diện", "theo", "pháp", "luật", "kế", "toán", "trưởng",
        "bctc", "tnhh", "ctcp", "hđqt", "bks", "bgđ", "cbtt", "usd", "tổng", "cộng"
    }

    VN_VOWELS = set("aeiouyàáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ")
    EN_COMMON_WORDS = {
        "the", "and", "of", "to", "in", "for", "is", "on", "that", "by", "this",
        "with", "from", "at", "as", "page", "report", "total", "net", "revenue",
        "profit", "date", "company", "no", "item", "note", "assets", "equity",
        "cash", "tax", "year", "quarter", "annual", "audit", "balance", "sheet",
        "income", "statement", "financial", "growth", "cost", "sales", "per", "share"
    }

    @classmethod
    def classify_ocr_hallucination(cls, text: str, conf: Optional[float]) -> Tuple[bool, Optional[str]]:
        """
        Evaluates whether an OCR text line is genuine content or hallucination/garbage noise (A1).
        Returns:
            (True, None) if valid.
            (False, reason) if rejected:
                - 'REJECTED_LOW_CONFIDENCE' if conf < 0.60
                - 'REJECTED_HALLUCINATION' if non-lexical noise or repeated artifacts
        """
        if conf is not None and conf < 0.60:
            return False, "REJECTED_LOW_CONFIDENCE"

        stripped = text.strip()
        if not stripped:
            return False, "REJECTED_HALLUCINATION"

        if TextCleaner.is_connector_line_artifact(stripped):
            return False, "REJECTED_HALLUCINATION"

        alnum_chars = [c for c in stripped if c.isalnum()]
        if not alnum_chars:
            return False, "REJECTED_HALLUCINATION"

        if conf is None or conf < 0.80:
            has_digit = any(c.isdigit() for c in stripped)
            if has_digit:
                digits_count = sum(1 for c in stripped if c.isdigit())
                if digits_count >= 2 or any(sym in stripped for sym in ("%", "$", "đ", "₫", "€", "¥", "VND", "vnd")):
                    return True, None

            words = [w.lower().strip(".,;:()[]{}'\"") for w in stripped.split()]
            for w in words:
                clean_w = "".join(c for c in w if c.isalpha())
                if clean_w in cls.VN_LEXICON_WORDS or clean_w in cls.EN_COMMON_WORDS:
                    return True, None

            has_vn_diacritic = any(c in "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđĐ" for c in stripped)
            if has_vn_diacritic:
                return True, None

            letters = [c.lower() for c in stripped if c.isalpha()]
            if len(letters) >= 3:
                vowels_count = sum(1 for c in letters if c in cls.VN_VOWELS)
                vowel_ratio = vowels_count / len(letters)
                if vowel_ratio < 0.15 or vowel_ratio > 0.85:
                    return False, "REJECTED_HALLUCINATION"
                return True, None
            elif len(letters) > 0 and not has_digit:
                return False, "REJECTED_HALLUCINATION"

        letters = [c.lower() for c in stripped if c.isalpha()]
        if len(letters) >= 5 and not any(c.isdigit() for c in stripped):
            vowels_count = sum(1 for c in letters if c in cls.VN_VOWELS)
            if vowels_count == 0:
                return False, "REJECTED_HALLUCINATION"

        return True, None

    @classmethod
    def _calculate_vn_lexicon_ratio(cls, results: List[Dict[str, Any]]) -> float:
        if not results:
            return 0.0
        total_words = 0
        hits = 0
        for item in results:
            words = str(item.get("text", "")).lower().split()
            for w in words:
                clean_w = "".join(c for c in w if c.isalpha())
                if clean_w:
                    total_words += 1
                    if clean_w in cls.VN_LEXICON_WORDS:
                        hits += 1
        return hits / max(1, total_words)

    def _predict_with_orientation_fallback(
        self,
        img_bgr,
        enabled: bool = True,
        allow_hybrid: bool = True,
        evaluate_all_orientations: bool = False,
    ):
        """Retry a failed landscape or 180-degree scan after rotating it for the detector.

        Evaluates orientations when first pass fails, produces vertical glyph noise,
        has low Vietnamese lexicon ratio (< 12%), or when evaluate_all_orientations is requested.
        """
        results = self._predict_image_counted(img_bgr)
        is_noise = self._is_glyph_noise(results)
        vn_ratio = self._calculate_vn_lexicon_ratio(results)

        # A high-confidence full page can be readable even when the recognizer omits
        # Vietnamese diacritics. Avoid three unnecessary rotated OCR passes in that case.
        if (
            results and not is_noise and not evaluate_all_orientations
            and (vn_ratio >= 0.12 or self._is_readable_ocr_layout(results))
        ):
            return results, None

        if not enabled:
            return results, None

        height, width = img_bgr.shape[:2]
        candidates = []
        if results and not is_noise:
            candidates.append((self._orientation_candidate_score(results, label=None), results, None, vn_ratio))

        # Rotate order: 90_CW is standard for landscape tables in Vietnamese portrait PDFs (top is on left),
        # followed by 90_CCW and 180.
        for code, label in (
            (cv2.ROTATE_90_CLOCKWISE, "rotate_90_cw"),
            (cv2.ROTATE_90_COUNTERCLOCKWISE, "rotate_90_ccw"),
            (cv2.ROTATE_180, "rotate_180"),
        ):

            rotated = cv2.rotate(img_bgr, code)
            rotated_results = self._predict_image_counted(rotated)
            if not rotated_results or self._is_glyph_noise(rotated_results):
                continue

            found_any = True
            rot_vn_ratio = self._calculate_vn_lexicon_ratio(rotated_results)

            mapped = []
            for item in rotated_results:
                bbox = item.get("bbox", [])
                if len(bbox) < 4:
                    continue
                rx0, ry0, rx1, ry1 = bbox
                if code == cv2.ROTATE_90_CLOCKWISE:
                    mapped_bbox = [ry0, height - rx1, ry1, height - rx0]
                elif code == cv2.ROTATE_90_COUNTERCLOCKWISE:
                    mapped_bbox = [width - ry1, rx0, width - ry0, rx1]
                else:  # ROTATE_180
                    mapped_bbox = [width - rx1, height - ry1, width - rx0, height - ry0]
                mapped_item = dict(item)
                mapped_item["bbox"] = [round(float(v), 2) for v in mapped_bbox]
                mapped_item["raw_bbox"] = [round(float(v), 2) for v in bbox]
                mapped.append(mapped_item)
            if mapped:
                candidates.append((self._orientation_candidate_score(mapped, label=label), mapped, label, rot_vn_ratio))

        if candidates:
            # If 0 deg was already valid text (not noise), require a significant score margin
            # or higher Vietnamese lexicon density before overriding with a rotation
            orig_candidate = next((c for c in candidates if c[2] is None), None)
            best_score, best_results, best_label, best_vn_ratio = max(candidates, key=lambda item: item[0])
            if orig_candidate and best_label is not None:
                orig_score, orig_results, _, orig_vn_ratio = orig_candidate
                # A small language-score gain can come from hallucinated words
                # on an upside-down crop. Keep the original angle and expose
                # ambiguity instead of silently rotating it.
                strong_language_recovery = orig_vn_ratio <= 0.02 and best_vn_ratio >= 0.25
                if best_score < orig_score + 8.0 and not strong_language_recovery:
                    return orig_results, "orientation_uncertain"
                if best_label == "rotate_180" and orig_vn_ratio >= 0.12 and best_vn_ratio <= orig_vn_ratio * 1.5:
                    return orig_results, None
            return best_results, best_label

        # If all rotations returned noise or empty, return original results if any
        if results:
            return results, None

        # Hybrid 2-Up check for landscape pages with mixed orientation sub-regions
        if allow_hybrid and width >= height * 1.25:
            hybrid_results, hybrid_label = self._predict_hybrid_2up(img_bgr, enabled=enabled)
            if hybrid_results:
                return hybrid_results, hybrid_label

        return [], None

    @staticmethod
    def _is_readable_ocr_layout(results: List[Dict[str, Any]]) -> bool:
        if len(results) < 20:
            return False
        texts = [str(item.get("text", "")).strip() for item in results]
        meaningful = sum(len(text) >= 12 for text in texts)
        mean_conf = sum(_score_or(item.get("confidence"), 0.0) for item in results) / len(results)
        return meaningful / len(results) >= 0.55 and mean_conf >= 0.90

    @staticmethod
    def _is_glyph_noise(results: List[Dict[str, Any]]) -> bool:
        """
        Detects whether OCR output is vertical glyph noise / isolated fragment noise
        (typically caused by running horizontal OCR on a 90-degree rotated page).
        """
        if not results:
            return True
        texts = [str(item.get("text", "")).strip() for item in results if str(item.get("text", "")).strip()]
        if not texts:
            return True
        total = len(texts)
        single_or_double = sum(1 for t in texts if len(t) <= 2)
        vertical_strokes = sum(1 for t in texts if t in ("I", "1", "l", "|", "!", "[", "]", "/", "-", "i", "L", "T", "H", "E", "F"))
        meaningful = sum(1 for t in texts if len(t) >= 4)
        total_chars = sum(len(t) for t in texts)
        avg_len = total_chars / max(1, total)

        # High proportion of isolated 1-2 char glyphs with few real words
        if total <= 20 and (single_or_double / total >= 0.40) and meaningful < 3:
            return True
        if (single_or_double / total >= 0.35) and meaningful < 12:
            return True
        if (vertical_strokes / total >= 0.25):
            return True
        if avg_len < 3.2 and (meaningful / total <= 0.15):
            return True
        if avg_len < 3.0 and meaningful <= 2:
            return True
        return False

    @staticmethod
    def _orientation_candidate_score(results: List[Dict[str, Any]], label: Optional[str] = None) -> float:
        """Prefer readable multi-character Vietnamese OCR over vertical glyph noise or upside-down text."""
        if not results:
            return float("-inf")
        texts = [str(item.get("text", "")).strip() for item in results]
        texts = [t for t in texts if t]
        if not texts:
            return float("-inf")

        total_lines = len(texts)
        chars = sum(len(text) for text in texts)
        meaningful = sum(1 for text in texts if len(text) >= 3)
        long_words = sum(1 for text in texts if len(text) >= 5)
        single_glyphs = sum(1 for text in texts if len(text) <= 2)
        confidence = sum(_score_or(item.get("confidence"), 0.0) for item in results) / total_lines

        # Common Vietnamese lexicon bonus (strongly prefers upright text over upside-down text)
        vn_keywords = {
            "và", "các", "của", "trong", "năm", "cho", "tại", "báo", "cáo",
            "tài", "chính", "hợp", "nhất", "công", "ty", "cổ", "phần", "tiền",
            "phải", "thu", "trả", "chi", "phí", "hạn", "ngắn", "dài", "nợ",
            "số", "lượng", "thuyết", "minh", "tổng", "đầu", "tư", "giá", "trị",
            "dự", "phòng", "khách", "hàng", "bên", "liên", "quan", "xem", "không",
            "có", "vnd", "đồng", "stt", "ngày", "tháng", "kết", "thúc", "kinh",
            "doanh", "thương", "mại", "dịch", "vụ", "hoạt", "động", "lợi", "nhuận",
            "thuế", "tài", "sản", "ngân", "hàng", "khoản", "mục", "vay", "vốn",
            "chủ", "sở", "hữu", "lưu", "chuyển", "hàng", "tồn", "kho", "người",
            "đại", "diện", "theo", "pháp", "luật", "kế", "toán", "trưởng"
        }
        keyword_hits = 0
        for text in texts:
            words = text.lower().split()
            for w in words:
                clean_w = "".join(c for c in w if c.isalpha())
                if clean_w in vn_keywords:
                    keyword_hits += 1

        vn_evaluator = VietnameseQualityEvaluator()
        vn_scores = [vn_evaluator.evaluate_line(text)[0] for text in texts if text]
        vn_quality = sum(vn_scores) / len(vn_scores) if vn_scores else 0.0

        suspicious = sum(
            1 for text in texts
            if UnicodeNormalizer.analyze(text).get("is_corrupted", False)
        )

        score = (
            (confidence * 2.0)
            + min(10.0, chars / 50.0)
            + (meaningful / total_lines) * 6.0
            + (long_words / total_lines) * 3.0
            + (vn_quality * 2.0)
            + min(30.0, keyword_hits * 1.5)
            - ((single_glyphs / total_lines) * 5.0)
            - (suspicious * 0.5)
        )
        return score

    def _predict_region_ocr(
        self,
        img_bgr,
        regions: List[Region],
        page_width: float,
        page_height: float,
        enabled: bool = True,
    ):
        """OCR each detected Two-Up/multi-region crop and restore page pixels."""
        image_h, image_w = img_bgr.shape[:2]
        combined = []
        orientations = []
        image_regions = [
            r for r in regions
            if r.content_type in (ContentType.IMAGE, ContentType.IMAGE_TABLE)
        ]
        route_regions = image_regions if len(image_regions) >= 2 else regions
        for region in sorted(route_regions, key=lambda r: (r.bbox.x0, r.bbox.y0)):
            bb = region.bbox
            px0 = max(0, min(image_w - 1, int(round(bb.x0 / max(page_width, 1.0) * image_w))))
            py0 = max(0, min(image_h - 1, int(round(bb.y0 / max(page_height, 1.0) * image_h))))
            px1 = max(px0 + 1, min(image_w, int(round(bb.x1 / max(page_width, 1.0) * image_w))))
            py1 = max(py0 + 1, min(image_h, int(round(bb.y1 / max(page_height, 1.0) * image_h))))
            crop = img_bgr[py0:py1, px0:px1]
            if crop.size == 0:
                continue

            local_results, label = self._predict_with_orientation_fallback(
                crop,
                enabled=enabled,
                allow_hybrid=False,
                evaluate_all_orientations=False,
            )
            if label:
                orientations.append(f"{region.region_id}:{label}")
                if "90_cw" in label:
                    region.rotation = 90.0
                    region.orientation_status = "CONFIDENT"
                    region.orientation_confidence = 0.80
                elif "90_ccw" in label:
                    region.rotation = 270.0
                    region.orientation_status = "CONFIDENT"
                    region.orientation_confidence = 0.80
                elif "180" in label:
                    region.rotation = 180.0
                    region.orientation_status = "CONFIDENT"
                    region.orientation_confidence = 0.80
                else:
                    region.orientation_status = "UNCERTAIN"
                    region.needs_fallback = True
                    region.failure_reason = "OCR orientations disagree; manual review required"

            for item in local_results:
                bbox = item.get("bbox", [])
                if len(bbox) < 4:
                    continue
                mapped = dict(item)
                mapped["bbox"] = [
                    round(float(bbox[0]) + px0, 2),
                    round(float(bbox[1]) + py0, 2),
                    round(float(bbox[2]) + px0, 2),
                    round(float(bbox[3]) + py0, 2),
                ]
                mapped["region_id"] = region.region_id
                combined.append(mapped)

        return combined, ";".join(orientations) if orientations else None

    def _predict_hybrid_2up(
        self,
        img_bgr,
        enabled: bool = True
    ):
        """
        V3.1.2 Phase F: Hybrid 2-Up orientation processing.
        Splits a landscape page into left and right halves, independently evaluates
        and rotates each half, then maps bounding boxes back to page space.
        """
        if not enabled:
            return [], None

        height, width = img_bgr.shape[:2]
        if width < height * 1.25:
            return [], None

        mid_x = width // 2
        left_img = img_bgr[:, :mid_x]
        right_img = img_bgr[:, mid_x:]

        left_results, left_orient = self._predict_with_orientation_fallback(
            left_img, enabled=True, allow_hybrid=False
        )
        right_results, right_orient = self._predict_with_orientation_fallback(
            right_img, enabled=True, allow_hybrid=False
        )

        if left_results or right_results:
            combined = []
            if left_results:
                combined.extend(left_results)
            if right_results:
                for item in right_results:
                    mapped_item = dict(item)
                    bx = list(item.get("bbox", [0.0, 0.0, 0.0, 0.0]))
                    mapped_item["bbox"] = [
                        round(bx[0] + mid_x, 2),
                        round(bx[1], 2),
                        round(bx[2] + mid_x, 2),
                        round(bx[3], 2),
                    ]
                    combined.append(mapped_item)

            orient_tag = f"hybrid_2up(L:{left_orient or '0'},R:{right_orient or '0'})"
            return combined, orient_tag

        return [], None

    def _extract_image_regions(
        self,
        doc_page: pymupdf.Page,
        page_num: int,
        target_regions: List[Region],
        native_blocks: List[TextBlock],
        render_dpi: int,
        evaluate_all_orientations: bool = False,
    ) -> List[TextBlock]:
        """
        V3.1.2 Phase B: Inspects and extracts text from candidate image regions.
        - Inspects large images even when a sparse native header/footer exists.
        - Skips images substantially covered by clean native text.
        - Runs OCR on cropped regions, maps coordinates back to page.
        - Suppresses OCR blocks that duplicate clean native text blocks (spec §17).
        """
        if not target_regions:
            return []

        image_blocks: List[TextBlock] = []
        page_rect = doc_page.rect
        page_area = max(1.0, float(page_rect.width * page_rect.height))
        native_bboxes = [b.bbox for b in native_blocks if b.bbox and len(b.bbox) >= 4]

        for region in target_regions:
            bb = region.bbox
            # FastIconFilter (spec §4.1): Skip decorative icons, bullets, and micro-images
            if bb.width < 60.0 or bb.height < 40.0 or bb.area < 3000.0:
                continue

            # Check if region is covered by native text blocks
            covered_area = 0.0
            for nb in native_bboxes:
                ix0 = max(bb.x0, nb[0])
                iy0 = max(bb.y0, nb[1])
                ix1 = min(bb.x1, nb[2])
                iy1 = min(bb.y1, nb[3])
                if ix1 > ix0 and iy1 > iy0:
                    covered_area += (ix1 - ix0) * (iy1 - iy0)
            if bb.area > 0 and (covered_area / bb.area) > 0.40:
                continue

            # Crop region and run OCR
            crop_bgr, actual_crop_rect = RegionCropper.crop_page_region(
                doc_page, bb, dpi=render_dpi, margin=3.0
            )
            if crop_bgr.size == 0 or crop_bgr.shape[0] < 16 or crop_bgr.shape[1] < 16:
                continue

            # P1: Fast variance pre-filter to skip blank/flat/gradient images in < 1ms
            crop_gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY) if len(crop_bgr.shape) == 3 else crop_bgr
            if crop_bgr.sum() > 0 and float(np.std(crop_gray)) < 15.0:
                continue

            crop_h, crop_w = crop_bgr.shape[:2]
            try:
                # P4: SHA-256 crop caching across identical graphical headers/logos
                _crop_cache = getattr(self, "_crop_cache", None)
                if _crop_cache is None:
                    _crop_cache = self._crop_cache = {}

                crop_hash = hashlib.sha256(crop_bgr.tobytes()).hexdigest()
                if crop_hash in _crop_cache:
                    ocr_results, orientation_label = _crop_cache[crop_hash]
                else:
                    ocr_results, orientation_label = self._predict_with_orientation_fallback(
                        crop_bgr,
                        enabled=self.config.get("orientation_fallback_enabled", True),
                        evaluate_all_orientations=evaluate_all_orientations,
                    )
                    _crop_cache[crop_hash] = (ocr_results, orientation_label)

                mean_confidence = (
                    sum(_score_or(item.get("confidence"), 0.0) for item in ocr_results) / len(ocr_results)
                    if ocr_results else 0.0
                )
                retry_risk = (
                    not ocr_results or self._is_glyph_noise(ocr_results)
                    or mean_confidence < self.config.get("min_confidence", 0.85)
                )
                num_threshold = float(self.config.get("numeric_retry_threshold", 0.85))
                has_low_conf_number = any(
                    bool(re.search(r"\d", item.get("text", ""))) and _score_or(item.get("confidence"), 0.0) < num_threshold
                    for item in ocr_results
                ) if ocr_results else False
                should_retry_region = (
                    (self.config.get("regional_retry_enabled", False) and retry_risk and bb.area / page_area >= 0.10)
                    or has_low_conf_number
                ) and self.config.get("fallback_upscale_dpi", render_dpi) > render_dpi
                if should_retry_region:
                    retry_crop, retry_rect = RegionCropper.crop_page_region(
                        doc_page, bb,
                        dpi=self.config["fallback_upscale_dpi"], margin=3.0,
                    )
                    if retry_crop.size:
                        retry_results, retry_orientation = self._predict_with_orientation_fallback(
                            retry_crop,
                            enabled=self.config.get("orientation_fallback_enabled", True),
                            evaluate_all_orientations=evaluate_all_orientations,
                        )
                        original_numbers = re.findall(
                            r"\d+(?:[.,/]\d+)*", " ".join(item.get("text", "") for item in ocr_results)
                        )
                        retry_numbers = re.findall(
                            r"\d+(?:[.,/]\d+)*", " ".join(item.get("text", "") for item in retry_results)
                        )
                        if ocr_results and retry_results and original_numbers != retry_numbers and (original_numbers or retry_numbers):
                            region.needs_fallback = True
                            region.failure_reason = "High-DPI OCR disagrees on numeric text; manual review required"
                        elif retry_results and (
                            not ocr_results
                            or self._orientation_candidate_score(retry_results) >
                            self._orientation_candidate_score(ocr_results) + 5.0
                        ):
                            ocr_results = retry_results
                            orientation_label = retry_orientation
                            crop_bgr, actual_crop_rect = retry_crop, retry_rect
                            crop_h, crop_w = retry_crop.shape[:2]
                if orientation_label:
                    if "90_cw" in orientation_label:
                        region.rotation = 90.0
                    elif "90_ccw" in orientation_label:
                        region.rotation = 270.0
                    elif "180" in orientation_label:
                        region.rotation = 180.0
                    if orientation_label == "orientation_uncertain":
                        region.orientation_status = "UNCERTAIN"
                        region.orientation_confidence = 0.0
                        region.needs_fallback = True
                        region.failure_reason = "OCR orientations disagree; manual review required"
                    else:
                        region.orientation_status = "CONFIDENT"
                        region.orientation_confidence = 0.80
                    progress(f"[{page_num}] region {region.region_id} rotation={region.rotation}° ({orientation_label}, {len(ocr_results)} lines)", stage="image_region_ocr")
                elif evaluate_all_orientations:
                    region.orientation_status = "CONFIDENT"
                    region.orientation_confidence = 0.70
            except Exception as e:
                # Recorded as a page error, not only printed: the region text is lost.
                logger.exception("Page %d: region OCR failed for %s", page_num, region.region_id)
                self._record_ocr_failure(f"Region OCR failed for {region.region_id}: {e}")
                continue
            if not ocr_results:
                continue

            # V4.0 Diagram Structure & Mermaid Detection (Pillar 4)
            from src.ocr.infrastructure.layout.diagram_builder import DiagramBuilder
            if region.content_type == ContentType.DIAGRAM or DiagramBuilder.is_diagram_content(ocr_results):
                diagram = DiagramBuilder.build_org_chart(
                    ocr_results,
                    page_num=page_num,
                    diagram_idx=len(self._current_page_diagrams) + 1
                )
                if diagram:
                    self._current_page_diagrams.append(diagram)
                    try:
                        progress(f"[{page_num}] Reconstructed diagram: {diagram.title} ({len(diagram.nodes)} nodes, {len(diagram.edges)} edges)", stage="diagram")
                    except Exception:
                        pass

            for idx, res in enumerate(ocr_results):
                raw_bbox = res.get("bbox", [])
                if len(raw_bbox) < 4:
                    continue

                text = UnicodeNormalizer.normalize(res.get("text", ""))
                conf = res.get("confidence")

                # Check hallucination / low confidence filter (A1)
                is_valid, reject_reason = self.classify_ocr_hallucination(text, conf)
                if not is_valid:
                    mapped_bbox = RegionCropper.map_crop_bbox_to_page(
                        raw_bbox, actual_crop_rect, crop_w, crop_h
                    )
                    _rejected_list = getattr(self, "_rejected_evidence", None)
                    if _rejected_list is None:
                        _rejected_list = self._rejected_evidence = []
                    _rejected_list.append(EvidenceBlock(
                        block_id=f"B_{page_num:04d}_REJ_{len(_rejected_list) + 1:04d}",
                        page=page_num,
                        region_id=region.region_id,
                        bbox=mapped_bbox,
                        bbox_original=mapped_bbox,
                        bbox_normalized=mapped_bbox,
                        text=text,
                        content_type=ContentType.IMAGE,
                        source="ocr",
                        confidence=conf,
                        orientation=region.rotation,
                        quality_score=conf,
                        block_type="noise",
                        is_bold=False,
                        font_size=None,
                        reading_order=0,
                        status=reject_reason,
                        failure_reason=f"Rejected: {reject_reason} (conf={conf})"
                    ))
                    continue

                mapped_bbox = RegionCropper.map_crop_bbox_to_page(
                    raw_bbox, actual_crop_rect, crop_w, crop_h
                )

                # Check if mapped OCR block overlaps with any native text block
                is_duplicate = False
                for nb in native_bboxes:
                    if RegionCropper.compute_intersection_over_min(mapped_bbox, nb) > 0.40:
                        is_duplicate = True
                        break

                if is_duplicate:
                    continue  # Native clean text takes priority

                q_score, _, _ = self.ocr_quality.evaluate_ocr_result(text, conf)
                if res.get("needs_review"):
                    q_score = min(q_score, .74) if q_score is not None else None
                    region.needs_fallback = True
                    region.failure_reason = res.get("review_reason", "OCR backend requested review")

                block = TextBlock(
                    text=text,
                    text_raw=res.get("text_raw", res.get("text", "")),
                    correction_source=(res.get("correction_source") or "unicode_normalizer")
                        if text != res.get("text_raw", res.get("text", "")) else None,
                    source="image_ocr",
                    page=page_num,
                    bbox=mapped_bbox,
                    confidence=conf,
                    quality_score=q_score,
                    confidence_source="recognizer",
                    reviewed=False,
                    line_index=len(native_blocks) + len(image_blocks) + idx,
                    region_id=region.region_id,
                    fallback_reason=res.get("review_reason") if res.get("needs_review") else None,
                )
                image_blocks.append(block)

        return image_blocks

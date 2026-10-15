import copy
import json
import logging
import time
import gc
from pathlib import Path
from typing import List, Dict, Any, Optional
import pymupdf

from src.ocr.domain.models.document import ExtractedDocument
from src.ocr.domain.models.quality import DocumentQualityReport, QualityMetrics
from src.ocr.domain.routing.routing_policy import RoutingPolicy
from src.ocr.application.route_page import RoutePageUseCase
from src.ocr.application.process_page import ProcessPageUseCase
from src.ocr.application.export_document import ExportDocumentUseCase
from src.ocr.infrastructure.cache.artifact_cache import ArtifactCache
from src.ocr.infrastructure.export.completion import mark_completed
from src.ocr.infrastructure.export.manifest_exporter import ManifestExporter
from src.ocr.runtime.atomic_io import atomic_write_json
from src.ocr.runtime.config import get_profile, load_profiles
from src.ocr.runtime.device import resolve_device
from src.ocr.runtime.structured_logging import log_context

logger = logging.getLogger(__name__)



def resolve_input_path(file_path: str) -> Path:
    input_path = Path(file_path)
    if input_path.is_file():
        return input_path.resolve()

    filename = input_path.name
    cwd = Path.cwd()
    project_root = Path(__file__).resolve().parent.parent.parent.parent

    # Candidate paths to inspect in priority order
    candidates = [
        cwd / file_path,
        cwd / "examples" / file_path,
        cwd / "examples" / filename,
        cwd / "inputs" / file_path,
        cwd / "inputs" / filename,
        project_root / file_path,
        project_root / "examples" / file_path,
        project_root / "examples" / filename,
        project_root / "inputs" / file_path,
        project_root / "inputs" / filename,
    ]

    checked_paths = []
    for cand in candidates:
        try:
            resolved_cand = cand.resolve()
        except Exception:
            resolved_cand = cand

        if resolved_cand not in checked_paths:
            checked_paths.append(resolved_cand)
            if resolved_cand.is_file():
                return resolved_cand

    error_lines = [
        f"PDF file not found: '{file_path}'",
        "Checked candidate paths:"
    ]
    for p in checked_paths:
        error_lines.append(f"  - {p}")

    raise FileNotFoundError("\n".join(error_lines))


class ConvertDocumentUseCase:
    """
    Main orchestration use case to convert a PDF document using the Hybrid OCR v3.1 Pipeline.
    """
    def __init__(self, config_path: Optional[str] = None, *, reuse_docling: bool = False,
                 thread_budget: Optional[int] = None, batch_size: Optional[int] = None):
        # Single config source; a missing file is an error (ENV-13).
        self.config_path, self.profiles = load_profiles(config_path)
        if thread_budget is not None and (type(thread_budget) is not int or thread_budget < 1):
            raise ValueError("thread_budget must be a positive integer")
        if batch_size is not None and (type(batch_size) is not int or batch_size < 1):
            raise ValueError("batch_size must be a positive integer")
        self.thread_budget = thread_budget
        # Hardware profile (ENV-01) override of the recognizer line batch.
        self.batch_size = batch_size
        self.reuse_docling = reuse_docling
        self._shared_docling = None
        self._shared_docling_key = None

    def close(self):
        if self._shared_docling is not None:
            self._shared_docling.close()
            self._shared_docling = None
            self._shared_docling_key = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _docling_client(self, config):
        if not self.reuse_docling or not config.get("docling", {}).get("enabled"):
            self.close()
            return None
        import os
        from src.ocr.runtime.models import model_fingerprint
        from src.ocr.runtime.fingerprint import source_fingerprint
        from src.ocr.infrastructure.layout.docling_structure import DoclingStructureExtractor
        key = (json.dumps(config["docling"], sort_keys=True), model_fingerprint(config), source_fingerprint(),
               os.getenv("OCR_DOCLING_PYTHON"), os.getenv("OCR_MODELS_DIR"))
        if key != self._shared_docling_key:
            self.close()
            self._shared_docling = DoclingStructureExtractor(config["docling"])
            self._shared_docling_key = key
        return self._shared_docling

    def resolve_profile(self, profile_name: str, device: Optional[str] = None) -> Dict[str, Any]:
        """Profile with the device resolved to `cpu` or `cuda` and passed to
        Docling too, so the resolved device is part of every cache key."""
        config = copy.deepcopy(get_profile(self.profiles, profile_name))
        if config.get("runtime", {}).get("scheduler") == "document_two_phase_v1":
            raise ValueError("This profile requires the CPU-only supervisor: python -m src.ocr.cli.gpu_runner")
        if self.batch_size is not None:
            config["batch_size"] = self.batch_size
        if self.thread_budget is not None:
            config["num_threads"] = self.thread_budget
            if config.get("docling", {}).get("enabled"):
                config["docling"]["num_threads"] = self.thread_budget
        requested_device = device or config.get("device")
        config["device"] = resolve_device(requested_device)
        if config.get("primary_engine", "vietocr") == "rapidocr" and config["device"] == "cuda":
            if requested_device == "auto":
                config["device"] = "cpu"
            else:
                raise ValueError("The RapidOCR primary backend currently runs on CPU; use --device cpu for this profile")
        config["backend_devices"] = {
            "ocr_detection": "cpu",  # RapidOCR detector GPU path awaits T4.
            "ocr_recognition": config["device"],
            "layout": config["device"],
            "docling": config["device"] if config.get("docling", {}).get("enabled") else None,
        }
        if isinstance(config.get("docling"), dict):
            config["docling"]["device"] = config["device"]
        return config

    def execute(
        self,
        file_path: str,
        profile_name: str = "balanced",
        pages: Optional[List[int]] = None,
        output_dir: str = "output",
        resume: bool = False,
        benchmark: bool = False,
        accuracy_report: bool = False,
        ground_truth_dir: str = "benchmark/ground_truth",
        device: Optional[str] = None,
        assets_mode: str = "png",
        warmup: bool = False,
        cache_dir: Optional[str] = None,
        output_mode: str = "legacy",
        keywords_file: Optional[str] = None,
    ) -> ExtractedDocument:
        # ENV-11: logs of this document carry its ID (the PDF stem, as below).
        with log_context(doc_id=Path(file_path).stem):
            return self._execute_document(file_path, profile_name, pages, output_dir, resume, benchmark,
                                          accuracy_report, ground_truth_dir, device, assets_mode, warmup,
                                          cache_dir, output_mode, keywords_file)

    def _execute_document(self, file_path, profile_name, pages, output_dir, resume, benchmark, accuracy_report,
                          ground_truth_dir, device, assets_mode, warmup, cache_dir, output_mode,
                          keywords_file=None) -> ExtractedDocument:
        from src.ocr.application.count_keywords import CountKeywordsUseCase
        from src.ocr.infrastructure.postprocessing.keyword_counter import KeywordCounter
        KeywordCounter.load_keywords(CountKeywordsUseCase.resolve_keywords_file(keywords_file))
        from src.ocr.infrastructure.export.output_contract import OUTPUT_MODES
        if output_mode not in OUTPUT_MODES:
            raise ValueError(f"Unknown output_mode {output_mode!r}")
        t0 = time.perf_counter()
        pdf_path = resolve_input_path(file_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {file_path}")

        doc_id = pdf_path.stem
        file_hash = ArtifactCache.compute_file_hash(str(pdf_path))
        config = self.resolve_profile(profile_name, device)

        if pages and any(isinstance(p, bool) or not isinstance(p, int) or p < 1 for p in pages):
            raise ValueError("pages must contain positive 1-based integer page numbers")

        doc = pymupdf.open(str(pdf_path))
        total_pages = len(doc)
        if total_pages <= 0:
            doc.close()
            raise ValueError("PDF has no pages to process")

        if pages is None or len(pages) == 0:
            target_pages = list(range(1, total_pages + 1))
        else:
            invalid = [p for p in pages if p > total_pages]
            if invalid:
                doc.close()
                raise ValueError(f"Pages outside the document's 1-{total_pages} range: {invalid}")
            target_pages = sorted(set(pages))

        page_processor = None
        try:
            cache = ArtifactCache(cache_dir)
            routing_policy = RoutingPolicy(
                reliable_threshold=config.get("native_quality", {}).get("reliable_threshold", 0.90),
                review_threshold=config.get("native_quality", {}).get("review_threshold", 0.70)
            )
            router = RoutePageUseCase(routing_policy)
            # Model loading happens here (and in the optional Docling warm-up), so
            # start-up is reported separately from page latency (PERF-05).
            t_startup = time.perf_counter()
            shared = self._docling_client(config)
            page_processor = (ProcessPageUseCase(doc, config, file_hash, cache, structure_extractor=shared)
                              if shared is not None else ProcessPageUseCase(doc, config, file_hash, cache))
            timing: Dict[str, Any] = {"model_init_seconds": round(time.perf_counter() - t_startup, 3)}
            if warmup and page_processor.structure_extractor.enabled:
                timing.update(self._warm_up_docling(page_processor, config))
            startup_seconds = round(time.perf_counter() - t_startup, 3)
            timing["startup_seconds"] = startup_seconds
        except Exception:
            if page_processor is not None:
                page_processor.close()
            doc.close()
            raise
        t_pages = time.perf_counter()

        extracted_pages = []
        pages_native = 0
        pages_ocr = 0
        pages_mixed = 0
        total_fallback = 0

        try:
            for page_num in target_pages:
                p_result = page_processor.execute(
                    page_num=page_num, total_pages=total_pages,
                    router=router, resume=resume)
                extracted_pages.append(p_result)
                pt_str = p_result.page_type.value if hasattr(p_result.page_type, "value") else str(p_result.page_type)
                if p_result.metadata.get("routing_override") == "native_first_disabled":
                    pages_ocr += 1  # counted by the route actually taken (ablation A1)
                elif pt_str in ("NATIVE_TEXT", "PRODUCT_SHOWCASE_COLLAGE"):
                    pages_native += 1
                elif pt_str == "MIXED":
                    pages_mixed += 1
                else:
                    pages_ocr += 1
                total_fallback += p_result.quality.fallback_count
                gc.collect()
        finally:
            page_processor.close()
            doc.close()

        timing["pages_seconds"] = round(time.perf_counter() - t_pages, 3)
        total_duration = time.perf_counter() - t0
        mean_native_score = sum(p.quality.native_text_score for p in extracted_pages) / len(extracted_pages) if extracted_pages else 1.0
        ocr_confidences = [p.quality.ocr_confidence for p in extracted_pages
                           if p.quality.recognized_lines > 0 and p.quality.ocr_confidence is not None]
        mean_ocr_conf = round(sum(ocr_confidences) / len(ocr_confidences), 4) if ocr_confidences else None
        # Pages without a measurable score (only model output) are left out (C2.3d).
        scored = [p.quality.quality_score for p in extracted_pages if p.quality.quality_score is not None]
        if scored:
            mean_qual_score = sum(scored) / len(scored)
        else:
            mean_qual_score = None
        pages_from_cache = sum(1 for p in extracted_pages if p.quality.from_cache)

        total_primary_lines = sum(p.quality.total_blocks for p in extracted_pages)
        total_recognized_lines = sum(p.quality.recognized_lines for p in extracted_pages)
        fb_ratio = (total_fallback / total_primary_lines) if total_primary_lines > 0 else 0.0

        quality_report = DocumentQualityReport(
            document_id=doc_id,
            total_pages=total_pages,
            mean_native_score=round(mean_native_score, 4),
            mean_ocr_confidence=mean_ocr_conf,
            ocr_confidence_scored_pages=len(ocr_confidences),
            quality_scored_pages=len(scored),
            mean_quality_score=round(mean_qual_score, 4) if mean_qual_score is not None else None,
            pages_processed_native=pages_native,
            pages_processed_ocr=pages_ocr,
            pages_processed_mixed=pages_mixed,
            total_fallback_regions=total_fallback,
            total_time_seconds=round(total_duration, 2),
            pages_from_cache=pages_from_cache,
            startup_seconds=startup_seconds,
            page_reports=[p.quality for p in extracted_pages],
            metrics=QualityMetrics(
                mean_confidence=mean_ocr_conf,
                primary_lines=total_primary_lines,
                fallback_lines=total_fallback,
                fallback_ratio=round(fb_ratio, 4)
            )
        )

        extracted_doc = ExtractedDocument(
            document_id=doc_id,
            file_path=str(pdf_path),
            file_hash=file_hash,
            total_pages=total_pages,
            selected_pages=target_pages,
            profile=profile_name,
            pages=extracted_pages,
            quality=quality_report
        )

        # Generate manifest
        extracted_doc.manifest = ManifestExporter.generate_manifest(
            extracted_doc, config, config_path=self.config_path,
            cache_key=page_processor.cache_key, resume=resume)
        from src.ocr.runtime.config import document_layout_settings
        from src.ocr.application.resolve_document_layout import resolve_document_layout
        t_layout = time.perf_counter()
        settings = document_layout_settings(config)
        if settings["method"] == "region_tree_graph_v1":
            resolve_document_layout(extracted_doc, settings)
        else:
            extracted_doc.manifest["document_layout"] = {"method": "legacy", "algorithm_version": "legacy-v6.1"}
        timing["document_layout_seconds"] = round(time.perf_counter() - t_layout, 3)

        # Benchmark data if requested
        benchmark_data = None
        if benchmark:
            benchmark_data = {
                "document_id": doc_id,
                "profile": profile_name,
                "pages_benchmarked": target_pages,
                "wall_clock_seconds": round(total_duration, 2),
                "seconds_per_page": round(total_duration / len(target_pages), 2),
                "pages_per_minute": round((len(target_pages) / total_duration) * 60, 2),
                "primary_lines": total_primary_lines,
                "recognized_lines": total_recognized_lines,
                "fallback_lines": total_fallback,
                "fallback_ratio": round(fb_ratio, 4),
                "mean_confidence": mean_ocr_conf,
                "mean_quality_score": round(mean_qual_score, 4) if mean_qual_score is not None else None,
                "pages_from_cache": pages_from_cache,
                "startup_seconds": startup_seconds,
            }

        # Export all artifacts to disk
        out_path = Path(output_dir)
        t_export = time.perf_counter()
        doc_dir = ExportDocumentUseCase.execute(extracted_doc, out_path, benchmark_data,
                                               assets_mode=assets_mode, output_mode=output_mode,
                                               keywords_file=keywords_file)
        timing["export_seconds"] = round(time.perf_counter() - t_export, 3)
        # Open to validation.json written, export included (6.1 document latency).
        timing["document_latency_seconds"] = round(time.perf_counter() - t0, 3)
        extracted_doc.timing = timing
        # Persist the same current-run duration used by performance reporting.
        # Cached page timers remain historical and are identified in manifest.cache.
        extracted_doc.quality.total_time_seconds = timing["document_latency_seconds"]
        extracted_doc.manifest["timing"] = timing
        extracted_doc.manifest["summary"]["total_time_seconds"] = timing["document_latency_seconds"]
        extracted_doc.manifest["summary"]["seconds_per_page"] = round(
            timing["document_latency_seconds"] / max(1, len(target_pages)), 4)
        atomic_write_json(doc_dir / "quality.json", extracted_doc.quality.model_dump(mode="json"))
        atomic_write_json(doc_dir / "manifest.json", extracted_doc.manifest)
        if benchmark_data is not None:
            benchmark_data.update(timing=timing, wall_clock_seconds=timing["document_latency_seconds"],
                                  seconds_per_page=round(timing["document_latency_seconds"] / max(1, len(target_pages)), 4),
                                  pages_per_minute=round(len(target_pages) * 60 / max(timing["document_latency_seconds"], .001), 4))
            atomic_write_json(doc_dir / "benchmark.json", benchmark_data)

        # Accuracy evaluation against ground truth if requested
        if accuracy_report:
            gt_path = Path(ground_truth_dir)
            if gt_path.exists():
                try:
                    from benchmark.evaluate import evaluate_document
                    acc_report = evaluate_document(doc_dir, gt_path)
                    extracted_doc.accuracy = acc_report
                    atomic_write_json(doc_dir / "accuracy.json", acc_report.model_dump(mode="json"))

                    # Update quality metrics with real CER and WER
                    extracted_doc.quality.metrics.cer = acc_report.mean_cer
                    extracted_doc.quality.metrics.wer = acc_report.mean_wer
                    atomic_write_json(doc_dir / "quality.json", extracted_doc.quality.model_dump(mode="json"))
                except Exception as e:
                    logger.warning("Accuracy evaluation failed: %s", e, exc_info=True)

        from src.ocr.infrastructure.export.json_exporter import JSONExporter
        atomic_write_json(doc_dir / "result.json", JSONExporter.export_document_json(extracted_doc))
        # Written last: a resumed batch trusts only outputs carrying this marker.
        validation = json.loads((doc_dir / "validation.json").read_text(encoding="utf-8"))
        mark_completed(doc_dir, validation["status"])
        return extracted_doc

    @staticmethod
    def _warm_up_docling(page_processor: ProcessPageUseCase, config: Dict[str, Any]) -> Dict[str, Any]:
        from src.ocr.infrastructure.layout.docling_structure import DoclingStructureError
        try:
            return {"docling_warmup_seconds": round(page_processor.structure_extractor.warmup(), 3)}
        except DoclingStructureError as exc:
            if config.get("docling", {}).get("on_error", "fallback") == "raise":
                raise
            # Pages then report the Docling failure individually.
            return {"docling_warmup_error": str(exc)}

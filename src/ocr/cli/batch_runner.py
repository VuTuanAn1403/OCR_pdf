import contextvars
import logging
import os
import sys
import time
import json
import csv
import io
import traceback
import psutil
from pathlib import Path
from typing import List, Dict, Any, Optional
import pymupdf

sys_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if sys_root not in sys.path:
    sys.path.insert(0, sys_root)

from src.ocr.application.convert_document import ConvertDocumentUseCase
from src.ocr.infrastructure.cache.artifact_cache import ArtifactCache
from src.ocr.domain.models.schema import PAGE_SCHEMA_VERSION
from src.ocr.infrastructure.export.completion import verify_completed
from src.ocr.infrastructure.export.manifest_exporter import PIPELINE_VERSION
from src.ocr.infrastructure.export.markdown_exporter import ASSET_MODES
from src.ocr.runtime.atomic_io import atomic_write_json, atomic_write_text
from src.ocr.runtime.config import ConfigError, config_hash
from src.ocr.infrastructure.export.output_contract import OUTPUT_MODES
from src.ocr.infrastructure.export.result_reader import load_document_json
from src.ocr.runtime.device import VALID_DEVICES
from src.ocr.runtime.fingerprint import source_fingerprint
from src.ocr.runtime.hardware import preparse_hardware
from src.ocr.runtime.models import model_fingerprint
from src.ocr.runtime.paths import cache_dir, data_dir, output_dir
from src.ocr.runtime.run_manifest import RunRecorder
from src.ocr.runtime.structured_logging import LOG_FORMATS, bind_context, configure_logging, progress

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


def consistency_review_items(doc_dir: Path, file_name: str) -> List[Dict[str, Any]]:
    """Review-queue items for the flag-only financial checks in document.json:
    arithmetic violations, continuation candidates and a failed check run."""
    path = doc_dir / "document.json"
    if not path.is_file():
        return []

    def item(priority: str, page: Optional[int], reason: str, snippet: Optional[str] = None) -> Dict[str, Any]:
        return {"priority": priority, "file": file_name, "page": page, "region_id": None, "reason": reason,
                "affected_lines": 0, "snippet": snippet}

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [item("CRITICAL", None, f"document.json unreadable: {error}")]
    checks = document.get("consistency_checks") or {}
    items = []
    if checks.get("status") == "FAILED":
        items.append(item("REVIEW", None, f"financial_checks_failed: {checks.get('error')}"))
    for table in document.get("tables", []):
        page = (table.get("pages") or [None])[0]
        for check in table.get("arithmetic_checks") or []:
            if check.get("status") == "VIOLATION":
                detail = (f"expected {check['expected']}, found {check['found']}" if "expected" in check
                          else check.get("reason"))
                items.append(item("REVIEW", page, f"financial_{check['check']}:{table['table_id']}:"
                                                  f"{check.get('row')}:{check.get('col')}",
                                  f"{check.get('label', '')} {detail}".strip()))
        if table.get("continuation"):
            items.append(item("REVIEW", page, f"table_continuation_candidate:{table['table_id']}",
                              ", ".join(table["continuation"].get("signals", []))))
    for check in checks.get("balance_identity") or []:
        if check.get("status") == "VIOLATION":
            items.append(item("REVIEW", None, f"financial_balance_identity:{check['assets']['table_id']}"
                                              f"={check['equity']['table_id']}"))
    return items


class BatchOCRRunner:
    """
    Orchestrates batch OCR processing over an entire directory of PDF documents,
    adhering strictly to checkpoint/resume, per-file isolation, metric tracking,
    quality evaluation, and final reporting.
    """
    def __init__(
        self,
        input_dir: str = "inputs",
        output_dir: str = "output",
        profile: str = "balanced",
        ground_truth_dir: str = "benchmark/ground_truth",
        max_retries: int = 2,
        excel_path: Optional[str] = "output/OCR_BENCHMARK_5_FILES.xlsx",
        force: bool = False,
        file_list: Optional[List[str]] = None,
        device: Optional[str] = None,
        assets_mode: str = "png",
        cache_dir: Optional[str] = None,
        shard: Optional[tuple] = None,
        config_path: Optional[str] = None,
        workers: int = 1,
        threads_per_worker: Optional[int] = None,
        batch_size: Optional[int] = None,
        hardware_profile: Optional[Dict[str, Any]] = None,
        output_mode: str = "legacy",
        keywords_file: Optional[str] = None,
    ):
        self.input_dir = Path(input_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.profile = profile
        self.ground_truth_dir = Path(ground_truth_dir).resolve()
        self.max_retries = max_retries
        self.excel_path = Path(excel_path).resolve() if excel_path else None
        self.force = force
        self.file_list = file_list
        self.assets_mode = assets_mode
        if output_mode not in OUTPUT_MODES:
            raise ValueError(f"Unknown output_mode {output_mode!r}")
        self.output_mode = output_mode
        from src.ocr.application.count_keywords import CountKeywordsUseCase
        from src.ocr.infrastructure.postprocessing.keyword_counter import KeywordCounter
        self.keywords_file = str(CountKeywordsUseCase.resolve_keywords_file(keywords_file))
        KeywordCounter.load_keywords(self.keywords_file)
        self.cache_dir = cache_dir
        # (index, count): this worker takes every count-th file (ENV-08).
        self.shard = shard
        if type(workers) is not int or workers < 1:
            raise ValueError("workers must be a positive integer")
        if threads_per_worker is not None and (type(threads_per_worker) is not int or threads_per_worker < 1):
            raise ValueError("threads_per_worker must be a positive integer")
        self.workers = workers
        self.worker_threads = threads_per_worker or (max(1, min(6, (os.cpu_count() or 1)//workers)) if workers > 1 else None)
        self._executor = None
        self._futures = {}

        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Parallel shards share document outputs but must never replace each
        # other's checkpoints and reports (ENV-08/14).
        self.report_dir = shard_report_dir(self.output_dir, self.shard)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.report_dir / "batch_manifest.json"
        self.report_json_path = self.report_dir / "batch_report.json"
        self.report_csv_path = self.report_dir / "batch_report.csv"
        self.report_md_path = self.report_dir / "BATCH_OCR_REPORT.md"
        self.review_queue_path = self.report_dir / "review_queue.json"
        self.final_validation_path = self.report_dir / "final_validation.json"
        if self.shard and self.excel_path:
            self.excel_path = self.report_dir / self.excel_path.name

        self.hardware_profile = hardware_profile
        self.converter = ConvertDocumentUseCase(config_path=config_path, batch_size=batch_size)
        self.converter.reuse_docling = True
        self.converter.thread_budget = self.worker_threads
        self.profile_config = self.converter.resolve_profile(profile, device)
        self.device = self.profile_config["device"]
        self.run_manifest_path = self.report_dir / "run_manifest.json"

    def discover_files(self) -> List[Dict[str, Any]]:
        """Scans the input directory and prepares fixed manifest entries."""
        pdf_files = sorted(list(self.input_dir.glob("*.pdf")))
        if self.file_list:
            targets = {Path(t).name.lower() for t in self.file_list}
            pdf_files = [f for f in pdf_files if f.name.lower() in targets or f.stem.lower() in targets]
        if self.shard:
            # Deterministic split over the sorted list, so N workers cover
            # every file exactly once and a rerun of a shard gets the same files.
            index, count = self.shard
            pdf_files = [f for position, f in enumerate(pdf_files) if position % count == index]
        manifest_entries = []

        for f in pdf_files:
            file_size = f.stat().st_size
            total_pages = -1
            status = "PENDING"
            error_msg = None

            try:
                doc = pymupdf.open(str(f))
                total_pages = len(doc)
                doc.close()
            except Exception as e:
                status = "FAILED_INPUT"
                error_msg = str(e)

            entry = {
                "file_name": f.name,
                "document_id": f.stem,
                "relative_path": str(f.relative_to(self.input_dir.parent)),
                "absolute_path": str(f.resolve()),
                "file_size_bytes": file_size,
                "file_size_mb": round(file_size / (1024 * 1024), 2),
                "total_pages": total_pages,
                "status": status,
                "error": error_msg,
                "start_time": None,
                "end_time": None,
                "wall_clock_seconds": None,
                "seconds_per_page": None,
                "pages_per_minute": None,
                "peak_memory_mb": None,
                "pages_native": None,
                "pages_ocr": None,
                "pages_mixed": None,
                "fallback_lines": None,
                "total_lines": None,
                "fallback_ratio": None,
                "mean_confidence": None,
                "mean_quality_score": None,
                "accuracy_status": "NOT_AVAILABLE_NO_GROUND_TRUTH",
                "cer": None,
                "wer": None,
                "diacritic_accuracy": None,
                "table_accuracy": None,
                "reading_order_accuracy": None,
                "classifications": []
            }

            if status != "FAILED_INPUT":
                cached_data = self.is_cached(f.stem, total_pages, source_file=f)
                if cached_data is not None:
                    bm = cached_data["benchmark"]
                    ql = cached_data["quality"]
                    acc = cached_data.get("accuracy")

                    entry["status"] = "CACHED"
                    entry["wall_clock_seconds"] = bm.get("wall_clock_seconds")
                    entry["seconds_per_page"] = bm.get("seconds_per_page")
                    entry["pages_per_minute"] = bm.get("pages_per_minute")
                    entry["fallback_lines"] = bm.get("fallback_lines")
                    entry["total_lines"] = bm.get("primary_lines")
                    entry["fallback_ratio"] = bm.get("fallback_ratio")
                    entry["mean_confidence"] = bm.get("mean_confidence")
                    entry["mean_quality_score"] = bm.get("mean_quality_score")
                    entry["pages_native"] = ql.get("pages_processed_native", 0)
                    entry["pages_ocr"] = ql.get("pages_processed_ocr", 0)
                    entry["pages_mixed"] = ql.get("pages_processed_mixed", 0)

                    if acc:
                        entry["accuracy_status"] = "EVALUATED_AGAINST_GROUND_TRUTH"
                        entry["cer"] = acc.get("mean_cer")
                        entry["wer"] = acc.get("mean_wer")
                        entry["diacritic_accuracy"] = acc.get("mean_diacritic_accuracy")
                        entry["table_accuracy"] = acc.get("mean_table_accuracy")
                        entry["reading_order_accuracy"] = acc.get("mean_reading_order_accuracy")

                    entry["classifications"] = self.classify_document(entry, None)

            manifest_entries.append(entry)

        return manifest_entries

    def is_cached(self, doc_id: str, expected_pages: int,
                  source_file: Optional[Path] = None) -> Optional[Dict[str, Any]]:
        """Verifies if complete, valid artifacts already exist for this document."""
        if self.force:
            return None
        doc_dir = self.output_dir / doc_id
        if not doc_dir.exists() or not doc_dir.is_dir():
            return None

        required_files = [
            doc_dir / "result.md",
            doc_dir / "result.json",
            doc_dir / "manifest.json",
            doc_dir / "quality.json",
            doc_dir / "benchmark.json",
            doc_dir / "validation.json"
        ]
        if not all(rf.exists() for rf in required_files):
            return None

        pages_dir = doc_dir / "pages"
        if not pages_dir.exists() or not pages_dir.is_dir():
            return None

        md_count = len(list(pages_dir.glob("page-*.md")))
        if expected_pages > 0 and md_count < expected_pages:
            return None

        # Only fully written outputs (ENV-14) produced by the same code,
        # schema, configuration and asset mode count as done (ENV-04).
        if not verify_completed(doc_dir) or not self._manifest_matches(doc_dir, source_file):
            return None

        # Load benchmark and quality
        try:
            current_docling = self.profile_config.get("docling", {})
            result_data = load_document_json(doc_dir / "result.json")
            if len(result_data.get("pages", [])) != expected_pages:
                return None
            for page_data in result_data.get("pages", []):
                if page_data.get("metadata", {}).get("layout_model", {}).get("status") == "FAILED_FALLBACK":
                    return None
            if current_docling.get("enabled"):
                from src.ocr.infrastructure.layout.docling_structure import DoclingStructureExtractor
                expected_fingerprint = DoclingStructureExtractor(current_docling).cache_fingerprint()
                for page_data in result_data.get("pages", []):
                    backend = page_data.get("metadata", {}).get("docling_structure", {})
                    if (backend.get("cache_fingerprint") != expected_fingerprint
                            or backend.get("status") in ("FAILED_FALLBACK", "partial_success")
                            or backend.get("errors")):
                        return None
            validation = json.loads((doc_dir / "validation.json").read_text(encoding="utf-8"))
            if validation.get("status") not in ("PASS", "NEEDS_REVIEW"):
                return None
            with open(doc_dir / "benchmark.json", "r", encoding="utf-8") as f:
                bm = json.load(f)
            with open(doc_dir / "quality.json", "r", encoding="utf-8") as f:
                ql = json.load(f)

            acc = None
            acc_file = doc_dir / "accuracy.json"
            if acc_file.exists():
                with open(acc_file, "r", encoding="utf-8") as f:
                    acc = json.load(f)

            # Refresh this inexpensive report even when OCR artifacts are reused.
            from src.ocr.application.count_keywords import CountKeywordsUseCase
            CountKeywordsUseCase.execute(doc_dir, doc_id, getattr(self, "keywords_file", None))
            return {
                "benchmark": bm,
                "quality": ql,
                "accuracy": acc
            }
        except Exception:
            return None

    def _manifest_matches(self, doc_dir: Path, source_file: Optional[Path] = None) -> bool:
        try:
            manifest = json.loads((doc_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(manifest, dict):
            return False
        if source_file is not None:
            try:
                actual_hash = ArtifactCache.compute_file_hash(str(source_file))
            except OSError:
                return False
            if manifest.get("file_hash") != actual_hash:
                return False
        return (
            manifest.get("profile") == self.profile
            and manifest.get("page_schema_version") == PAGE_SCHEMA_VERSION
            and manifest.get("source_fingerprint") == source_fingerprint()
            and manifest.get("assets_mode", "png") == self.assets_mode
            and manifest.get("output_mode", "legacy") == self.output_mode
            and config_hash(manifest.get("configuration") or {}) == config_hash(self.profile_config)
            and manifest.get("models_fingerprint") == model_fingerprint(self.profile_config)
        )

    def classify_document(self, doc_entry: Dict[str, Any], doc_obj: Any) -> List[str]:
        """Classifies document by content patterns (Calibrated per Proposal R7)."""
        classes = []
        tot = doc_entry.get("total_pages", 0)
        p_nat = doc_entry.get("pages_native", 0) or 0
        p_ocr = doc_entry.get("pages_ocr", 0) or 0
        p_mix = doc_entry.get("pages_mixed", 0) or 0

        if tot <= 0:
            return ["FAILED"]

        ocr_ratio = (p_ocr + p_mix) / float(tot)
        if ocr_ratio < 0.10:
            classes.append("NATIVE_TEXT_HEALTHY")
        elif ocr_ratio > 0.90:
            classes.append("SCANNED")
        else:
            classes.append("MIXED")

        # Table / Diagram checks based on percentage of pages (R7: >= 30% tables, >= 10% diagrams)
        pages_with_tables = 0
        pages_with_diagrams = 0
        if hasattr(doc_obj, "pages") and doc_obj.pages:
            for p in doc_obj.pages:
                if getattr(p, "tables", None):
                    pages_with_tables += 1
                if getattr(p, "diagrams", None) or (
                    getattr(p, "tables", None) == [] and len(getattr(p, "blocks", [])) < 15
                    and any(getattr(r, "content_type", None) and r.content_type.value in ("DIAGRAM", "FIGURE") for r in getattr(p, "regions", []))
                ):
                    pages_with_diagrams += 1

        if (pages_with_tables / float(tot)) >= 0.30:
            classes.append("TABLE_HEAVY")
        if (pages_with_diagrams / float(tot)) >= 0.10:
            classes.append("DIAGRAM_HEAVY")

        return classes

    def execute_batch(self, limit: Optional[int] = None) -> None:
        try:
            # Log context bound for this run stays inside the copied context.
            contextvars.copy_context().run(self._execute_batch, limit)
        finally:
            if self._executor is not None:
                self._executor.shutdown(wait=True, cancel_futures=True)
                self._executor = None
            close = getattr(self.converter, "close", None)
            if callable(close):
                close()

    def _execute_batch(self, limit: Optional[int] = None) -> None:
        """Executes full batch over all discovered files."""
        files_manifest = self.discover_files()
        total_files = len(files_manifest)
        if limit:
            files_manifest = files_manifest[:limit]
            total_files = len(files_manifest)

        print("=" * 70)
        print(f"BATCH OCR RUNNER v{PIPELINE_VERSION} — TOTAL INPUT FILES: {total_files}")
        print(f"Input Directory: {self.input_dir}")
        print(f"Output Directory: {self.output_dir}")
        print(f"Profile: {self.profile} (FROZEN)")
        print(f"Pipeline Version: {PIPELINE_VERSION}")
        print("=" * 70)

        # Save initial manifest
        atomic_write_json(self.manifest_path, files_manifest)
        recorder = RunRecorder(
            "batch", config_path=self.converter.config_path, profile=self.profile,
            profile_config=self.profile_config, device=self.device,
            cache_dir=Path(self.cache_dir) if self.cache_dir else cache_dir(),
            resume=not self.force, assets_mode=self.assets_mode,
            output_mode=self.output_mode,
            hardware_profile=self.hardware_profile,
        )
        recorder.data["shard"] = list(self.shard) if self.shard else None
        self._run_id = recorder.run_id
        bind_context(run_id=recorder.run_id)  # ENV-11; scoped by execute_batch
        self._batch_source_fingerprint = recorder.data["source_fingerprint"]
        recorder.data["scheduler"] = {"workers": self.workers, "threads_per_worker": self.worker_threads,
                                       "unit": "document", "docling_lifetime": "worker"}
        recorder.write(self.run_manifest_path, final=False)

        batch_t0 = time.time()
        if self.workers > 1:
            from concurrent.futures import ProcessPoolExecutor
            from src.ocr.runtime.document_pool import initialize_worker
            self._executor = ProcessPoolExecutor(max_workers=self.workers, initializer=initialize_worker,
                                                 initargs=(self.worker_threads,))
            for entry in files_manifest:
                if entry["status"] not in ("CACHED", "FAILED_INPUT"):
                    self._submit_document(entry)

        for idx, entry in enumerate(files_manifest):
            file_num = idx + 1
            f_name = entry["file_name"]
            doc_id = entry["document_id"]
            tot_pages = entry["total_pages"]

            if entry["status"] == "FAILED_INPUT":
                recorder.add_document({"document_id": doc_id, "status": "FAILED_INPUT",
                                       "error": entry["error"]})
                recorder.write(self.run_manifest_path, final=False)
                progress(f"\n[FILE {file_num:03d}/{total_files:03d}] {f_name} [FAILED_INPUT]: {entry['error']}",
                         stage="batch", level=logging.ERROR, doc_id=doc_id, status="FAILED_INPUT")
                continue

            if entry["status"] == "CACHED":
                recorder.add_document({"document_id": doc_id, "status": "CACHED",
                                       "file_path": entry["absolute_path"],
                                       "pages_from_cache": list(range(1, tot_pages + 1)),
                                       "timing_origin": "previous_run"})
                recorder.write(self.run_manifest_path, final=False)
                progress(f"\n[FILE {file_num:03d}/{total_files:03d}] {f_name} [CACHED]\n"
                         f"Pages: {tot_pages} | Sec/Page: {entry['seconds_per_page']}s | Status: CACHED/SKIPPED",
                         stage="batch", doc_id=doc_id, status="CACHED")
                continue

            # Check cache
            cached_data = self.is_cached(doc_id, tot_pages, source_file=Path(entry["absolute_path"]))
            if cached_data is not None:
                bm = cached_data["benchmark"]
                ql = cached_data["quality"]
                acc = cached_data.get("accuracy")

                entry["status"] = "CACHED"
                entry["wall_clock_seconds"] = bm.get("wall_clock_seconds")
                entry["seconds_per_page"] = bm.get("seconds_per_page")
                entry["pages_per_minute"] = bm.get("pages_per_minute")
                entry["fallback_lines"] = bm.get("fallback_lines")
                entry["total_lines"] = bm.get("primary_lines")
                entry["fallback_ratio"] = bm.get("fallback_ratio")
                entry["mean_confidence"] = bm.get("mean_confidence")
                entry["mean_quality_score"] = bm.get("mean_quality_score")
                entry["pages_native"] = ql.get("pages_processed_native", 0)
                entry["pages_ocr"] = ql.get("pages_processed_ocr", 0)
                entry["pages_mixed"] = ql.get("pages_processed_mixed", 0)

                if acc:
                    entry["accuracy_status"] = "EVALUATED_AGAINST_GROUND_TRUTH"
                    entry["cer"] = acc.get("mean_cer")
                    entry["wer"] = acc.get("mean_wer")
                    entry["diacritic_accuracy"] = acc.get("mean_diacritic_accuracy")
                    entry["table_accuracy"] = acc.get("mean_table_accuracy")
                    entry["reading_order_accuracy"] = acc.get("mean_reading_order_accuracy")

                entry["classifications"] = self.classify_document(entry, None)
                recorder.add_document({"document_id": doc_id, "status": "CACHED",
                                       "file_path": entry["absolute_path"],
                                       "pages_from_cache": list(range(1, tot_pages + 1)),
                                       "timing_origin": "previous_run"})
                atomic_write_json(self.manifest_path, files_manifest)
                recorder.write(self.run_manifest_path, final=False)

                progress(f"\n[FILE {file_num:03d}/{total_files:03d}] {f_name} [CACHED]\n"
                         f"Pages: {tot_pages} | Sec/Page: {entry['seconds_per_page']}s | Status: CACHED/SKIPPED",
                         stage="batch", doc_id=doc_id, status="CACHED")
                continue

            # Check Ground Truth
            from benchmark.evaluate import ground_truth_pages
            has_gt = bool(ground_truth_pages(self.ground_truth_dir, doc_id))

            # Real-time Start Output
            progress(f"\n[FILE {file_num:03d}/{total_files:03d}] {f_name}\nPages: {tot_pages}\nStatus: PROCESSING",
                     stage="batch", doc_id=doc_id, status="PROCESSING", pages=tot_pages)

            success = False
            attempts = 0
            file_t0 = time.time()
            entry["start_time"] = time.strftime("%Y-%m-%d %H:%M:%S")

            while attempts < self.max_retries and not success:
                attempts += 1
                try:
                    if self._executor is not None:
                        from src.ocr.runtime.document_pool import load_exported_document
                        future = self._futures.get(doc_id) or self._submit_document(entry)
                        result = future.result()
                        doc = load_exported_document(Path(result["document_dir"]))
                        entry["rss_at_completion_mb"] = result["rss_at_completion_mb"]
                        entry["memory_scope"] = result["memory_scope"]
                    else:
                        doc = self.converter.execute(
                        file_path=entry["absolute_path"],
                        profile_name=self.profile,
                        output_dir=str(self.output_dir),
                        resume=(not self.force),
                        benchmark=True,
                        accuracy_report=has_gt,
                        ground_truth_dir=str(self.ground_truth_dir),
                        device=self.device,
                        assets_mode=self.assets_mode,
                        output_mode=self.output_mode,
                        keywords_file=self.keywords_file,
                        cache_dir=self.cache_dir,
                    )
                    recorder.add_document(doc)

                    file_dur = (result["wall_clock_seconds"] if self._executor is not None
                                else time.time() - file_t0)
                    entry["timing_scope"] = "worker_job_including_export" if self._executor is not None else "document_including_export"
                    sec_p = file_dur / max(1, tot_pages)
                    ppm = (tot_pages / max(0.01, file_dur)) * 60

                    if self._executor is None:
                        entry["rss_at_completion_mb"] = round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
                        entry["memory_scope"] = "parent_process_snapshot"
                    entry["memory_measurement"] = "RSS snapshot, not sampled peak"

                    entry["status"] = "COMPLETED"
                    entry["end_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    entry["wall_clock_seconds"] = round(file_dur, 2)
                    entry["seconds_per_page"] = round(sec_p, 2)
                    entry["pages_per_minute"] = round(ppm, 2)
                    entry["peak_memory_mb"] = None
                    entry["pages_native"] = doc.quality.pages_processed_native
                    entry["pages_ocr"] = doc.quality.pages_processed_ocr
                    entry["pages_mixed"] = doc.quality.pages_processed_mixed
                    entry["fallback_lines"] = doc.quality.metrics.fallback_lines
                    entry["total_lines"] = doc.quality.metrics.primary_lines
                    entry["fallback_ratio"] = doc.quality.metrics.fallback_ratio
                    entry["mean_confidence"] = doc.quality.mean_ocr_confidence
                    entry["mean_quality_score"] = doc.quality.mean_quality_score

                    if has_gt and hasattr(doc, "accuracy") and doc.accuracy:
                        entry["accuracy_status"] = "EVALUATED_AGAINST_GROUND_TRUTH"
                        entry["cer"] = doc.accuracy.mean_cer
                        entry["wer"] = doc.accuracy.mean_wer
                        entry["diacritic_accuracy"] = doc.accuracy.mean_diacritic_accuracy
                        entry["table_accuracy"] = doc.accuracy.mean_table_accuracy
                        entry["reading_order_accuracy"] = doc.accuracy.mean_reading_order_accuracy

                    entry["classifications"] = self.classify_document(entry, doc)
                    success = True

                    # Real-time Done Output
                    progress(f"[DONE {file_num:03d}/{total_files:03d}] {f_name}\n"
                             f"Total: {file_dur:.2f}s | Pages: {tot_pages} | Avg: {sec_p:.2f}s/page | "
                             f"Fallback: {entry['fallback_ratio']:.2%}",
                             stage="batch", doc_id=doc_id, status="COMPLETED", seconds=round(file_dur, 3))

                except Exception as ex:
                    self._futures.pop(doc_id, None)
                    progress(f"[ATTEMPT {attempts}/{self.max_retries} FAILED] {f_name}: {ex}", file=sys.stderr,
                             stage="batch", level=logging.WARNING, doc_id=doc_id, status="ATTEMPT_FAILED")
                    is_oom = any(kw in str(ex).lower() for kw in ("paging file", "malloc", "outofmemory", "unable to allocate", "insufficient memory"))
                    if is_oom and hasattr(self.converter, "close"):
                        try:
                            self.converter.close()
                        except Exception:
                            pass
                        import gc
                        gc.collect()
                    if attempts >= self.max_retries:
                        entry["status"] = "FAILED_PROCESSING"
                        entry["end_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
                        entry["error"] = str(ex)
                        entry["traceback"] = traceback.format_exc()
                        recorder.add_document({"document_id": doc_id, "status": "FAILED_PROCESSING",
                                               "error": str(ex)})
                        progress(f"[FAILED {file_num:03d}/{total_files:03d}] {f_name} after {self.max_retries} attempts.",
                                 stage="batch", level=logging.ERROR, doc_id=doc_id, status="FAILED_PROCESSING",
                                 error=str(ex))

            # Flush manifests after each file for resilient checkpointing
            atomic_write_json(self.manifest_path, files_manifest)
            recorder.write(self.run_manifest_path, final=False)

            # Generate real-time reports and update Excel after each file
            try:
                self.generate_reports(files_manifest, time.time() - batch_t0)
            except Exception as e:
                print(f"[WARN] Incremental report generation failed: {e}", file=sys.stderr)

            # Explicit garbage collection to keep memory bounded
            import gc
            gc.collect()
            try:
                proc_rss = psutil.Process().memory_info().rss
                if proc_rss > 2500 * 1024 * 1024 and hasattr(self.converter, "close"):
                    self.converter.close()
                    gc.collect()
            except Exception:
                pass

        batch_duration = time.time() - batch_t0
        print("\n" + "=" * 70)
        print("ALL BATCH FILES PROCESSED. GENERATING CONSOLIDATED REPORTS...")
        print("=" * 70)

        self.generate_reports(files_manifest, batch_duration)
        recorder.write(self.run_manifest_path)

    def _submit_document(self, entry):
        from benchmark.evaluate import ground_truth_pages
        from src.ocr.runtime.document_pool import convert_job
        job = {"file_path": entry["absolute_path"], "config_path": str(self.converter.config_path),
               "thread_budget": self.worker_threads, "batch_size": self.converter.batch_size,
               "run_id": self._run_id,
               "profile": self.profile, "device": self.device,
               "output_dir": str(self.output_dir), "cache_dir": self.cache_dir, "resume": not self.force,
               "assets_mode": self.assets_mode, "ground_truth_dir": str(self.ground_truth_dir),
               "output_mode": self.output_mode,
               "keywords_file": self.keywords_file,
               "accuracy_report": bool(ground_truth_pages(self.ground_truth_dir, entry["document_id"])),
               "source_fingerprint": self._batch_source_fingerprint, "config_hash": config_hash(self.profile_config),
               "pdf_sha16": ArtifactCache.compute_file_hash(entry["absolute_path"])}
        future = self._executor.submit(convert_job, job)
        self._futures[entry["document_id"]] = future
        return future

    def generate_reports(self, manifest: List[Dict[str, Any]], batch_duration: float) -> None:
        """Generates all batch reports, CSV, Markdown summary, and review queue."""
        total_files = len(manifest)
        completed = sum(1 for e in manifest if e["status"] in ("COMPLETED", "CACHED"))
        cached = sum(1 for e in manifest if e["status"] == "CACHED")
        failed = sum(1 for e in manifest if e["status"] in ("FAILED_INPUT", "FAILED_PROCESSING"))

        total_pages = sum(e["total_pages"] for e in manifest if e.get("total_pages", -1) > 0)
        processed_pages = sum(e["total_pages"] for e in manifest if e["status"] in ("COMPLETED", "CACHED"))

        # Time statistics for processed files
        measured_entries = [e for e in manifest if e["status"] == "COMPLETED"]
        measured_pages = sum(e["total_pages"] for e in measured_entries)
        valid_times = [e["wall_clock_seconds"] for e in measured_entries if e.get("wall_clock_seconds")]
        valid_sec_per_page = [e["seconds_per_page"] for e in measured_entries if e.get("seconds_per_page") is not None]
        valid_fb = [e["fallback_ratio"] for e in manifest if e.get("fallback_ratio") is not None]
        valid_qual = [e["mean_quality_score"] for e in manifest if e.get("mean_quality_score") is not None]

        import numpy as np
        valid_entries = [e for e in measured_entries if e.get("seconds_per_page") is not None]
        fastest_file = min(valid_entries, key=lambda x: x.get("seconds_per_page"), default={})
        slowest_file = max(valid_entries, key=lambda x: x.get("seconds_per_page"), default={})
        median_sec = float(np.median(valid_sec_per_page)) if valid_sec_per_page else 0.0
        mean_sec = sum(valid_times) / measured_pages if measured_pages else 0.0
        p95_sec = float(np.percentile(valid_sec_per_page, 95)) if valid_sec_per_page else 0.0

        # Classification counts
        native_files = sum(1 for e in manifest if "NATIVE_TEXT_HEALTHY" in e.get("classifications", []))
        scanned_files = sum(1 for e in manifest if "SCANNED" in e.get("classifications", []))
        mixed_files = sum(1 for e in manifest if "MIXED" in e.get("classifications", []))
        table_heavy = sum(1 for e in manifest if "TABLE_HEAVY" in e.get("classifications", []))
        diagram_heavy = sum(1 for e in manifest if "DIAGRAM_HEAVY" in e.get("classifications", []))
        # Build Review Queue (Proposal R2 / Review V5 Item 10: Region-level and page-level precision)
        review_queue: List[Dict[str, Any]] = []
        seen_queue_keys = set()

        for e in manifest:
            doc_id = e.get("document_id")
            file_name = e.get("file_name", doc_id)
            if e["status"] in ("FAILED_INPUT", "FAILED_PROCESSING"):
                item = {
                    "priority": "CRITICAL",
                    "file": file_name,
                    "page": None,
                    "region_id": None,
                    "reason": f"Processing failed: {e.get('error')}",
                    "affected_lines": 0,
                    "snippet": None,
                }
                q_key = (file_name, None, None, item["reason"])
                if q_key not in seen_queue_keys:
                    seen_queue_keys.add(q_key)
                    review_queue.append(item)
                continue

            doc_dir = self.output_dir / doc_id if doc_id else None
            pages_data: List[Dict[str, Any]] = []

            # 1. Inspect page-level artifacts (pages/page-*.json or result.json)
            if doc_dir and doc_dir.is_dir():
                pages_dir = doc_dir / "pages"
                if pages_dir.is_dir():
                    for p_json in sorted(pages_dir.glob("page-*.json")):
                        try:
                            pages_data.append(json.loads(p_json.read_text(encoding="utf-8")))
                        except Exception:
                            pass
                elif (doc_dir / "result.json").is_file():
                    try:
                        r_data = load_document_json(doc_dir / "result.json")
                        pages_data = r_data.get("pages", [])
                    except Exception:
                        pass

            collected_region_ids = set()
            layout_review_counts = {p.get("page_num"): p.get("metadata", {}).get("document_layout_review_count", 0)
                                    for p in pages_data}

            # 2. Extract risk signals from individual regions (e.g. numeric disagreement, orientation conflict)
            for p_dict in pages_data:
                p_num = p_dict.get("page_num", 1)
                layout_signals = [(reason.get("code", "LAYOUT_UNCERTAIN"), reason.get("region_id"),
                                   reason.get("item_ids", []), None)
                                  for reason in p_dict.get("metadata", {}).get("layout_review", [])]
                for block in p_dict.get("blocks", []):
                    codes = block.get("review_reason_codes", [])
                    if block.get("role_status") == "NEEDS_REVIEW" and not codes:
                        codes = ["FURNITURE_ROLE_UNCERTAIN"]
                    for code in codes:
                        layout_signals.append((code, block.get("parent_region_id"),
                                               [block.get("source_id")], block.get("text")))
                for reason, region_id, source_ids, snippet in layout_signals:
                    q_key = (file_name, p_num, region_id, reason)
                    if q_key not in seen_queue_keys:
                        seen_queue_keys.add(q_key)
                        review_queue.append({"priority": "REVIEW", "file": file_name, "page": p_num,
                            "region_id": region_id, "reason": reason, "affected_lines": len(source_ids),
                            "source_ids": source_ids, "snippet": snippet})

                for reg in p_dict.get("regions", []):
                    r_id = reg.get("region_id")
                    r_needs_fallback = reg.get("needs_fallback", False)
                    r_reason = reg.get("failure_reason")

                    if r_needs_fallback or r_reason:
                        collected_region_ids.add((p_num, r_id))
                        reason_str = r_reason or "regional_fallback_triggered"
                        prio = "WARNING" if any(w in reason_str.lower() for w in ["disagree", "uncertain", "fail"]) else "REVIEW"
                        item = {
                            "priority": prio,
                            "file": file_name,
                            "page": p_num,
                            "region_id": r_id,
                            "reason": reason_str,
                            "affected_lines": 1,
                            "snippet": f"Region {r_id} ({reg.get('content_type', 'UNKNOWN')}): {reason_str}",
                        }
                        q_key = (file_name, p_num, r_id, reason_str)
                        if q_key not in seen_queue_keys:
                            seen_queue_keys.add(q_key)
                            review_queue.append(item)

                # 3. Extract risk signals from evidence blocks (UNRESOLVED, REJECTED_*)
                for eb in p_dict.get("evidence_blocks", []):
                    eb_id = eb.get("region_id")
                    eb_status = eb.get("status")
                    eb_reason = eb.get("failure_reason")

                    if eb_status in ("UNRESOLVED", "REJECTED_LOW_CONFIDENCE", "REJECTED_HALLUCINATION") or eb_reason:
                        collected_region_ids.add((p_num, eb_id))
                        reason_str = eb_reason or f"evidence_status_{eb_status}"
                        prio = "WARNING" if "REJECTED" in (eb_status or "") else "REVIEW"
                        txt = eb.get("text", "")
                        snippet_str = txt[:120] if txt else reason_str
                        item = {
                            "priority": prio,
                            "file": file_name,
                            "page": p_num,
                            "region_id": eb_id,
                            "reason": reason_str,
                            "affected_lines": 1,
                            "snippet": snippet_str,
                        }
                        q_key = (file_name, p_num, eb_id, reason_str)
                        if q_key not in seen_queue_keys:
                            seen_queue_keys.add(q_key)
                            review_queue.append(item)

            # 4. Extract page-level quality signals (quality.json)
            quality_path = doc_dir / "quality.json" if doc_dir else None
            if quality_path and quality_path.is_file():
                try:
                    q_data = json.loads(quality_path.read_text(encoding="utf-8"))
                    for pr in q_data.get("page_reports", []):
                        p_num = pr.get("page", 1)
                        if pr.get("suspicious_lines"):
                            for s_line in pr["suspicious_lines"][:3]:
                                item = {
                                    "priority": "WARNING",
                                    "file": file_name,
                                    "page": p_num,
                                    "region_id": None,
                                    "reason": "suspicious_line_detected",
                                    "affected_lines": 1,
                                    "snippet": s_line,
                                }
                                q_key = (file_name, p_num, None, s_line[:60])
                                if q_key not in seen_queue_keys:
                                    seen_queue_keys.add(q_key)
                                    review_queue.append(item)

                        if pr.get("orientation_fallback") == "orientation_uncertain":
                            reason_str = "orientation_uncertain"
                            item = {
                                "priority": "WARNING",
                                "file": file_name,
                                "page": p_num,
                                "region_id": None,
                                "reason": reason_str,
                                "affected_lines": 0,
                                "snippet": None,
                            }
                            q_key = (file_name, p_num, None, reason_str)
                            if q_key not in seen_queue_keys:
                                seen_queue_keys.add(q_key)
                                review_queue.append(item)

                        if pr.get("content_status") == "PARTIAL":
                            reason_str = pr.get("content_reason") or "partial_content_extraction"
                            item = {
                                "priority": "WARNING",
                                "file": file_name,
                                "page": p_num,
                                "region_id": None,
                                "reason": reason_str,
                                "affected_lines": pr.get("errors", 1),
                                "snippet": None,
                            }
                            q_key = (file_name, p_num, None, reason_str)
                            if q_key not in seen_queue_keys:
                                seen_queue_keys.add(q_key)
                                review_queue.append(item)

                        # If low confidence OCR blocks were reported and no region item was collected
                        req_count = max(0, pr.get("review_required_count", 0) - layout_review_counts.get(p_num, 0))
                        if req_count > 0 and not any(p == p_num for p, _ in collected_region_ids):
                            reason_str = "low_confidence_ocr_blocks"
                            item = {
                                "priority": "REVIEW",
                                "file": file_name,
                                "page": p_num,
                                "region_id": None,
                                "reason": reason_str,
                                "affected_lines": req_count,
                                "snippet": None,
                            }
                            q_key = (file_name, p_num, None, reason_str)
                            if q_key not in seen_queue_keys:
                                seen_queue_keys.add(q_key)
                                review_queue.append(item)
                except Exception:
                    pass

            # 2. Flag-only financial consistency checks from document.json (C3.5b/c)
            if doc_dir and doc_dir.is_dir():
                for item in consistency_review_items(doc_dir, file_name):
                    q_key = (file_name, item["page"], item["region_id"], item["reason"])
                    if q_key not in seen_queue_keys:
                        seen_queue_keys.add(q_key)
                        review_queue.append(item)

            # Update manifest entry review_required_count
            e["review_required_count"] = sum(1 for item in review_queue if item["file"] == file_name)

        atomic_write_json(self.review_queue_path, review_queue)

        stage_totals = {}
        for entry in manifest:
            if entry["status"] != "COMPLETED":
                continue
            quality_path = self.output_dir / entry["document_id"] / "quality.json"
            if not quality_path.is_file():
                continue
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
            for page in quality.get("page_reports", []):
                if page.get("from_cache", False):
                    continue
                for stage, duration in page.get("stage_seconds", {}).items():
                    stage_totals[stage] = stage_totals.get(stage, 0.0) + duration
        measured_total = sum(stage_totals.values())
        measured_stages = [
            {
                "rank": rank,
                "bottleneck": stage,
                "description": f"{duration:.1f}s ({duration / measured_total if measured_total else 0.0:.1%} of measured page processing time)",
            }
            for rank, (stage, duration) in enumerate(
                sorted(stage_totals.items(), key=lambda item: item[1], reverse=True), start=1
            )
        ]

        # Build batch_report.json
        report_data = {
            "summary": {
                "total_files": total_files,
                "completed": completed,
                "cached": cached,
                "failed": failed,
                "total_pages": total_pages,
                "processed_pages": processed_pages,
                "total_batch_duration_seconds": round(batch_duration, 2),
                "average_file_time_seconds": round(batch_duration / max(1, completed), 2),
                "average_seconds_per_page": round(mean_sec, 2),
                "median_seconds_per_page": round(median_sec, 2),
                "p95_seconds_per_page": round(p95_sec, 2),
                "overall_pages_per_minute": round((processed_pages / max(1.0, batch_duration)) * 60, 2),
                "fastest_file": {
                    "file_name": fastest_file.get("file_name"),
                    "seconds_per_page": fastest_file.get("seconds_per_page")
                },
                "slowest_file": {
                    "file_name": slowest_file.get("file_name"),
                    "seconds_per_page": slowest_file.get("seconds_per_page")
                },
                "classifications": {
                    "native_text_healthy": native_files,
                    "scanned": scanned_files,
                    "mixed": mixed_files,
                    "table_heavy": table_heavy,
                    "diagram_heavy": diagram_heavy
                },
                "pipeline_version": PIPELINE_VERSION,
                "profile": self.profile
            },
            "bottlenecks": measured_stages,
            "files": manifest
        }

        report_data["summary"]["pages_completed_current_run"] = measured_pages
        report_data["summary"]["pages_reused_from_document_cache"] = processed_pages - measured_pages
        report_data["summary"]["timing_population"] = "COMPLETED documents in this run; excludes CACHED documents"
        report_data["summary"]["overall_pages_per_minute"] = round(measured_pages / max(0.01, batch_duration) * 60, 2)
        atomic_write_json(self.report_json_path, report_data)

        # Build batch_report.csv
        csv_headers = [
            "file_name", "total_pages", "status", "wall_clock_seconds", "seconds_per_page",
            "pages_per_minute", "pages_ocr", "pages_native", "fallback_ratio", "mean_quality_score",
            "mean_confidence", "cer", "wer", "diacritic_accuracy", "table_accuracy", "reading_order_accuracy"
        ]
        csv_stream = io.StringIO(newline="")
        writer = csv.DictWriter(csv_stream, fieldnames=csv_headers, extrasaction="ignore")
        writer.writeheader()
        for row in manifest:
            clean_row = dict(row)
            if clean_row.get("fallback_ratio") is not None:
                clean_row["fallback_ratio"] = f"{clean_row['fallback_ratio']:.2%}"
            writer.writerow(clean_row)
        atomic_write_text(self.report_csv_path, csv_stream.getvalue())

        # Build BATCH_OCR_REPORT.md
        self.generate_markdown_report(report_data, manifest)
        print(f"Báo cáo Markdown đã tạo: {self.report_md_path}")
        print(f"Báo cáo JSON đã tạo: {self.report_json_path}")
        print(f"Báo cáo CSV đã tạo: {self.report_csv_path}")

    def generate_markdown_report(self, report: Dict[str, Any], manifest: List[Dict[str, Any]]) -> None:
        """Formats the Markdown executive summary, performance table, and accuracy analysis."""
        s = report["summary"]
        b = report["bottlenecks"]

        md_lines = [
            f"# BÁO CÁO BATCH OCR — {s['total_files']} PDF",
            f"> **Pipeline Version**: `{s['pipeline_version']}` | **Profile**: `{s['profile']}` | **Engine**: RapidOCR detection + VietOCR recognition",
            "",
            "## 1. Executive Summary (Tổng quan điều hành)",
            "",
            f"- **Tổng số tài liệu**: **{s['total_files']} files**",
            f"- **Tài liệu hoàn thành (Completed/Cached)**: **{s['completed']} files** ({s['cached']} files từ cache)",
            f"- **Tài liệu lỗi**: **{s['failed']} files**",
            f"- **Trang xử lý ở lần chạy này**: **{s['pages_completed_current_run']}**; **trang dùng lại toàn bộ tài liệu đã hoàn tất**: **{s['pages_reused_from_document_cache']}**",
            f"- **Tổng số trang xử lý**: **{s['processed_pages']} / {s['total_pages']} trang**",
            f"- **Tổng thời gian xử lý toàn batch**: **{s['total_batch_duration_seconds']:.2f} giây ({s['total_batch_duration_seconds']/60:.2f} phút / {s['total_batch_duration_seconds']/3600:.2f} giờ)**",
            f"- **Tốc độ trung bình có trọng số theo trang**: **{s['average_seconds_per_page']:.2f} giây/trang**",
            f"- **Trung vị tốc độ theo file**: **{s['median_seconds_per_page']:.2f} giây/trang**",
            f"- **P95 tốc độ theo file**: **{s['p95_seconds_per_page']:.2f} giây/trang**",
            f"- **Năng suất toàn hệ thống**: **{s['overall_pages_per_minute']:.2f} pages/minute**",
            "",
            "### Phân loại tài liệu trong tập dữ liệu:",
            f"- **Văn bản Native sạch (Healthy Native)**: **{s['classifications']['native_text_healthy']} files**",
            f"- **Tài liệu Scanned thuần túy**: **{s['classifications']['scanned']} files**",
            f"- **Tài liệu Hỗn hợp (Mixed Native + Scan)**: **{s['classifications']['mixed']} files**",
            f"- **Tài liệu chứa nhiều bảng biểu (Table Heavy)**: **{s['classifications']['table_heavy']} files**",
            f"- **Tài liệu chứa sơ đồ tổ chức (Diagram Heavy)**: **{s['classifications']['diagram_heavy']} files**",
            "",
            "---",
            "",
            "## 2. Phân tích hiệu năng & Tốc độ",
            "",
            f"- **File xử lý nhanh nhất**: `{s['fastest_file']['file_name']}` ({s['fastest_file']['seconds_per_page']} s/trang)",
            f"- **File xử lý chậm nhất**: `{s['slowest_file']['file_name']}` ({s['slowest_file']['seconds_per_page']} s/trang)",
            "",
            "### Thời gian đo theo công đoạn xử lý trang:",
            "Chỉ số tốc độ tài liệu loại trừ tài liệu CACHED; thời gian của tài liệu CACHED trong bảng là số đo lịch sử.",
            ""
        ]

        for b_item in b:
            md_lines.append(f"{b_item['rank']}. **{b_item['bottleneck']}**: {b_item['description']}")

        md_lines.extend([
            "",
            "---",
            "",
            f"## 3. Bảng chi tiết toàn bộ {s['total_files']} tài liệu",
            "",
            "| # | File | Pages | Time (s) | Sec/Page | Pages/Min | OCR | Native | Fallback % | Quality | CER | WER | Diacritic | Table | Order | Status |",
            "|---|------|------:|---------:|---------:|----------:|----:|-------:|-----------:|--------:|----:|----:|----------:|------:|------:|:------:|"
        ])

        for idx, e in enumerate(manifest):
            fn = e["file_name"]
            p = e.get("total_pages", 0)
            t = f"{e.get('wall_clock_seconds', 0.0):.1f}" if e.get("wall_clock_seconds") else "N/A"
            sp = f"{e.get('seconds_per_page', 0.0):.2f}" if e.get("seconds_per_page") else "N/A"
            ppm = f"{e.get('pages_per_minute', 0.0):.1f}" if e.get("pages_per_minute") else "N/A"
            p_ocr = e.get("pages_ocr", 0) or 0
            p_nat = e.get("pages_native", 0) or 0
            fb = f"{e.get('fallback_ratio', 0.0):.1%}" if e.get("fallback_ratio") is not None else "N/A"
            q = f"{e.get('mean_quality_score', 0.0):.2f}" if e.get("mean_quality_score") is not None else "N/A"

            cer = f"{e['cer']:.1%}" if e.get("cer") is not None else "N/A"
            wer = f"{e['wer']:.1%}" if e.get("wer") is not None else "N/A"
            diac = f"{e['diacritic_accuracy']:.1%}" if e.get("diacritic_accuracy") is not None else "N/A"
            tbl = f"{e['table_accuracy']:.1%}" if e.get("table_accuracy") is not None else "N/A"
            ro = f"{e['reading_order_accuracy']:.1%}" if e.get("reading_order_accuracy") is not None else "N/A"
            st = e.get("status", "UNKNOWN")

            md_lines.append(f"| {idx+1:03d} | `{fn}` | {p} | {t} | {sp} | {ppm} | {p_ocr} | {p_nat} | {fb} | {q} | {cer} | {wer} | {diac} | {tbl} | {ro} | {st} |")

        md_lines.extend([
            "",
            "---",
            "",
            "## 4. Phân tích độ tin cậy & Khuyến nghị (Reliability & Recommendations)",
            "- Số liệu thời gian theo công đoạn lấy từ `quality.json` của các trang đã hoàn tất; chưa bao gồm thời gian nạp mô hình và xuất artifact.",
            "- CER/WER và độ chính xác bảng chỉ có thể kết luận khi có ground truth độc lập.",
            ""
        ])

        atomic_write_text(self.report_md_path, "\n".join(md_lines))

        # Build final_validation.json
        validation_data = {
            "total_input_files": s["total_files"],
            "manifest_entries_count": len(manifest),
            "completed_files": s["completed"],
            "cached_files": s["cached"],
            "failed_files": s["failed"],
            "all_files_have_status": all(e.get("status") in ("COMPLETED", "CACHED", "FAILED_INPUT", "FAILED_PROCESSING") for e in manifest),
            "no_missing_files": (len(manifest) == s["total_files"]),
            "output_directory_exists": self.output_dir.exists(),
            "manifest_exists": self.manifest_path.exists(),
            "report_json_exists": self.report_json_path.exists(),
            "report_csv_exists": self.report_csv_path.exists(),
            "report_md_exists": self.report_md_path.exists(),
            "review_queue_exists": self.review_queue_path.exists(),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        atomic_write_json(self.final_validation_path, validation_data)
        print(f"Validation report đã tạo: {self.final_validation_path}")

        # Build Excel report if excel_path is specified
        if self.excel_path and not self.shard:
            try:
                from scripts.export_batch_excel import build_workbook
                build_workbook(self.output_dir, self.excel_path)
                print(f"Báo cáo Excel đã tạo: {self.excel_path}")
            except Exception as e:
                print(f"[WARN] Excel export failed: {e}", file=sys.stderr)

def main():
    import argparse
    try:
        # Before the parser, so its path defaults see the profile's paths.
        hardware = preparse_hardware()
    except ConfigError as exc:
        raise SystemExit(f"[ERROR] {exc}")
    parser = argparse.ArgumentParser(description=f"Batch OCR Runner v{PIPELINE_VERSION}")
    parser.add_argument("--hardware", default=None,
                        help="Hardware profile name in config/hardware/ or YAML path (device, workers, threads, paths)")
    parser.add_argument("--input", default=str(data_dir()), help="Input directory (default: $OCR_DATA_DIR or 'inputs')")
    parser.add_argument("--output", default=str(output_dir()), help="Output directory (default: $OCR_OUTPUT_DIR or 'output')")
    parser.add_argument("--cache-dir", default=None, help="Page cache directory (default: $OCR_CACHE_DIR or '.cache')")
    parser.add_argument("--config", default=None, help="Profile YAML path (default: $OCR_CONFIG_PATH or repository config/profiles.yaml)")
    parser.add_argument("--profile", default="balanced", help="Profile from the config file (default: balanced)")
    parser.add_argument("--workers", type=int, default=None,
                        help="Process workers, scheduled by document (default: hardware profile, else 1)")
    parser.add_argument("--threads-per-worker", type=int, help="Thread budget for torch/ONNX/Docling")
    parser.add_argument("--device", choices=VALID_DEVICES, default=None, help="Inference device; overrides the profile")
    parser.add_argument("--assets", choices=ASSET_MODES, default="png", help="Image crops: png, jpeg, or none")
    parser.add_argument("--output-mode", choices=OUTPUT_MODES, default="legacy",
                        help="legacy embeds pages in result.json; compact references complete per-page files")
    parser.add_argument("--shard", default=None, metavar="I/N",
                        help="Process only shard I of N (0-based), e.g. 0/4, splitting the sorted file list")
    parser.add_argument("--merge-shards", type=int, default=None, metavar="N",
                        help="Merge N completed shard manifests in --output and exit")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of files for testing")
    parser.add_argument("--excel", default=None, help="Excel output path (default: OCR_BENCHMARK_5_FILES.xlsx in --output)")
    parser.add_argument("--force", action="store_true", help="Force rerun without cache")
    parser.add_argument("--files", nargs="+", default=None, help="Filter specific files to run in batch")
    parser.add_argument("--keywords-file", default=None,
                        help="UTF-8 keyword file, one phrase per line (default: config/keywords.txt)")
    parser.add_argument("--log-format", choices=LOG_FORMATS, default="text",
                        help="text keeps console progress; json writes JSON lines with run/doc/page/stage/worker")
    args = parser.parse_args()
    configure_logging(args.log_format)

    if args.merge_shards is not None:
        if args.shard is not None:
            parser.error("--shard and --merge-shards cannot be used together")
        try:
            merged = merge_shard_manifests(Path(args.output), args.merge_shards)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(f"Merged {args.merge_shards} shards, {len(merged)} documents: {Path(args.output) / 'batch_manifest.json'}")
        return
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    try:
        runner = BatchOCRRunner(
            input_dir=args.input,
            output_dir=args.output,
            profile=args.profile,
            excel_path=args.excel or str(Path(args.output) / "OCR_BENCHMARK_5_FILES.xlsx"),
            force=args.force,
            file_list=args.files,
            keywords_file=args.keywords_file,
            device=args.device or (hardware and hardware.device),
            assets_mode=args.assets,
            output_mode=args.output_mode,
            cache_dir=args.cache_dir,
            shard=parse_shard(args.shard) if args.shard else None,
            config_path=args.config,
            # Explicit flags win even when invalid (0 is rejected, not replaced).
            workers=args.workers if args.workers is not None else ((hardware and hardware.workers) or 1),
            threads_per_worker=(args.threads_per_worker if args.threads_per_worker is not None
                                else hardware and hardware.threads_per_worker),
            batch_size=hardware.batch_size if hardware else None,
            hardware_profile=hardware.record() if hardware else None,
        )
    except (ConfigError, ValueError, RuntimeError, OSError) as exc:
        parser.error(str(exc))
    runner.execute_batch(limit=args.limit)


def parse_shard(value: str) -> tuple:
    """Parse 'I/N' into (I, N) with 0 <= I < N."""
    try:
        index, count = (int(part) for part in value.split("/"))
    except ValueError:
        raise SystemExit(f"--shard must look like I/N, got {value!r}")
    if count < 1 or not 0 <= index < count:
        raise SystemExit(f"--shard index must satisfy 0 <= I < N, got {value!r}")
    return index, count


def shard_report_dir(output: Path, shard: Optional[tuple]) -> Path:
    if shard is None:
        return Path(output)
    index, count = shard
    if count < 1 or not 0 <= index < count:
        raise ValueError("Shard index must satisfy 0 <= I < N")
    return Path(output) / "shards" / f"shard-{index:03d}-of-{count:03d}"


def merge_shard_manifests(output: Path, count: int) -> List[Dict[str, Any]]:
    """Merge only completed, compatible shards; reject duplicate documents."""
    if count < 1:
        raise ValueError("--merge-shards must be positive")
    output = Path(output)
    merged = []
    seen = set()
    runs = []
    expected_identity = None
    for index in range(count):
        directory = shard_report_dir(output, (index, count))
        try:
            entries = json.loads((directory / "batch_manifest.json").read_text(encoding="utf-8"))
            run = json.loads((directory / "run_manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Cannot read shard {index}/{count}: {exc}") from exc
        if not isinstance(entries, list) or not isinstance(run, dict):
            raise ValueError(f"Invalid manifest for shard {index}/{count}")
        if run.get("shard") != [index, count] or not run.get("finished_at_utc") or run.get("status") != "COMPLETED":
            raise ValueError(f"Shard {index}/{count} is not complete")
        identity = {key: run.get(key) for key in ("source_fingerprint", "page_schema_version", "device", "assets_mode")}
        identity["output_mode"] = run.get("output_mode", "legacy")
        identity["configuration"] = run.get("config", {}).get("resolved")
        identity["models"] = run.get("models_at_finish", run.get("models"))
        if expected_identity is not None and identity != expected_identity:
            raise ValueError(f"Shard {index}/{count} used a different pipeline, configuration or model")
        expected_identity = identity
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("document_id"):
                raise ValueError(f"Invalid document entry in shard {index}/{count}")
            if entry.get("status") not in ("COMPLETED", "CACHED", "FAILED_INPUT", "FAILED_PROCESSING"):
                raise ValueError(f"Unfinished document {entry['document_id']} in shard {index}/{count}")
            if entry["document_id"] in seen:
                raise ValueError(f"Duplicate document across shards: {entry['document_id']}")
            seen.add(entry["document_id"])
            merged.append(entry)
        runs.append({"shard": [index, count], "run_id": run.get("run_id"),
                     "manifest": str((directory / "run_manifest.json").relative_to(output))})
    merged.sort(key=lambda entry: entry.get("file_name", entry["document_id"]))
    atomic_write_json(output / "batch_manifest.json", merged)
    atomic_write_json(output / "shard_merge_manifest.json", {"shards": runs, "identity": expected_identity})
    return merged

if __name__ == "__main__":
    main()

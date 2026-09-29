import os
import sys
import time
import json
import csv
import traceback
import psutil
from pathlib import Path
from typing import List, Dict, Any, Optional
import pymupdf

sys_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if sys_root not in sys.path:
    sys.path.insert(0, sys_root)

from src.ocr.application.convert_document import ConvertDocumentUseCase
from src.ocr.infrastructure.export.manifest_exporter import PIPELINE_VERSION

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
        file_list: Optional[List[str]] = None
    ):
        self.input_dir = Path(input_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.profile = profile
        self.ground_truth_dir = Path(ground_truth_dir).resolve()
        self.max_retries = max_retries
        self.excel_path = Path(excel_path).resolve() if excel_path else None
        self.force = force
        self.file_list = file_list

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.output_dir / "batch_manifest.json"
        self.report_json_path = self.output_dir / "batch_report.json"
        self.report_csv_path = self.output_dir / "batch_report.csv"
        self.report_md_path = self.output_dir / "BATCH_OCR_REPORT.md"
        self.review_queue_path = self.output_dir / "review_queue.json"
        self.final_validation_path = self.output_dir / "final_validation.json"

        self.converter = ConvertDocumentUseCase()

    def discover_files(self) -> List[Dict[str, Any]]:
        """Scans the input directory and prepares fixed manifest entries."""
        pdf_files = sorted(list(self.input_dir.glob("*.pdf")))
        if self.file_list:
            targets = {Path(t).name.lower() for t in self.file_list}
            pdf_files = [f for f in pdf_files if f.name.lower() in targets or f.stem.lower() in targets]
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
                cached_data = self.is_cached(f.stem, total_pages)
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

    def is_cached(self, doc_id: str, expected_pages: int) -> Optional[Dict[str, Any]]:
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

        # Load benchmark and quality
        try:
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

            return {
                "benchmark": bm,
                "quality": ql,
                "accuracy": acc
            }
        except Exception:
            return None

    def classify_document(self, doc_entry: Dict[str, Any], doc_obj: Any) -> List[str]:
        """Classifies document by content patterns."""
        classes = []
        tot = doc_entry.get("total_pages", 0)
        p_nat = doc_entry.get("pages_native", 0) or 0
        p_ocr = doc_entry.get("pages_ocr", 0) or 0
        p_mix = doc_entry.get("pages_mixed", 0) or 0

        if tot <= 0:
            return ["FAILED"]

        if p_nat == tot:
            classes.append("NATIVE_TEXT_HEALTHY")
        elif p_ocr == tot:
            classes.append("SCANNED")
        else:
            classes.append("MIXED")

        # Table / Diagram / Image checks
        total_tables = 0
        total_diagram_blocks = 0
        if hasattr(doc_obj, "pages"):
            for p in doc_obj.pages:
                total_tables += len(getattr(p, "tables", []))
                if getattr(p, "tables", None) == [] and len(getattr(p, "blocks", [])) < 25:
                    total_diagram_blocks += 1

        if total_tables >= max(5, tot * 0.4):
            classes.append("TABLE_HEAVY")
        if total_diagram_blocks >= 2:
            classes.append("DIAGRAM_HEAVY")

        return classes

    def execute_batch(self, limit: Optional[int] = None) -> None:
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
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(files_manifest, f, ensure_ascii=False, indent=2)

        batch_t0 = time.time()

        for idx, entry in enumerate(files_manifest):
            file_num = idx + 1
            f_name = entry["file_name"]
            doc_id = entry["document_id"]
            tot_pages = entry["total_pages"]

            if entry["status"] == "FAILED_INPUT":
                print(f"\n[FILE {file_num:03d}/{total_files:03d}] {f_name} [FAILED_INPUT]: {entry['error']}")
                continue

            if entry["status"] == "CACHED":
                print(f"\n[FILE {file_num:03d}/{total_files:03d}] {f_name} [CACHED]")
                print(f"Pages: {tot_pages} | Sec/Page: {entry['seconds_per_page']}s | Status: CACHED/SKIPPED")
                continue

            # Check cache
            cached_data = self.is_cached(doc_id, tot_pages)
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

                print(f"\n[FILE {file_num:03d}/{total_files:03d}] {f_name} [CACHED]")
                print(f"Pages: {tot_pages} | Sec/Page: {entry['seconds_per_page']}s | Status: CACHED/SKIPPED")
                continue

            # Check Ground Truth
            has_gt = len(list(self.ground_truth_dir.glob(f"{doc_id.lower()[:3]}*.txt"))) > 0

            # Real-time Start Output
            print(f"\n[FILE {file_num:03d}/{total_files:03d}] {f_name}")
            print(f"Pages: {tot_pages}")
            print(f"Status: PROCESSING")

            success = False
            attempts = 0
            file_t0 = time.time()
            entry["start_time"] = time.strftime("%Y-%m-%d %H:%M:%S")

            while attempts < self.max_retries and not success:
                attempts += 1
                try:
                    doc = self.converter.execute(
                        file_path=entry["absolute_path"],
                        profile_name=self.profile,
                        output_dir=str(self.output_dir),
                        resume=(not self.force),
                        benchmark=True,
                        accuracy_report=has_gt,
                        ground_truth_dir=str(self.ground_truth_dir)
                    )

                    file_dur = time.time() - file_t0
                    sec_p = file_dur / max(1, tot_pages)
                    ppm = (tot_pages / max(0.01, file_dur)) * 60

                    proc = psutil.Process()
                    peak_mem = round(proc.memory_info().rss / (1024 * 1024), 1)

                    entry["status"] = "COMPLETED"
                    entry["end_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    entry["wall_clock_seconds"] = round(file_dur, 2)
                    entry["seconds_per_page"] = round(sec_p, 2)
                    entry["pages_per_minute"] = round(ppm, 2)
                    entry["peak_memory_mb"] = peak_mem
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
                    print(f"[DONE {file_num:03d}/{total_files:03d}] {f_name}")
                    print(f"Total: {file_dur:.2f}s | Pages: {tot_pages} | Avg: {sec_p:.2f}s/page | Fallback: {entry['fallback_ratio']:.2%}")

                except Exception as ex:
                    print(f"[ATTEMPT {attempts}/{self.max_retries} FAILED] {f_name}: {ex}", file=sys.stderr)
                    if attempts >= self.max_retries:
                        entry["status"] = "FAILED_PROCESSING"
                        entry["end_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
                        entry["error"] = str(ex)
                        entry["traceback"] = traceback.format_exc()
                        print(f"[FAILED {file_num:03d}/{total_files:03d}] {f_name} after {self.max_retries} attempts.")

            # Flush manifest after each file for resilient checkpointing
            with open(self.manifest_path, "w", encoding="utf-8") as f:
                json.dump(files_manifest, f, ensure_ascii=False, indent=2)

            # Generate real-time reports and update Excel after each file
            try:
                self.generate_reports(files_manifest, time.time() - batch_t0)
            except Exception as e:
                print(f"[WARN] Incremental report generation failed: {e}", file=sys.stderr)

            # Explicit garbage collection to keep memory bounded
            import gc
            gc.collect()

        batch_duration = time.time() - batch_t0
        print("\n" + "=" * 70)
        print("ALL BATCH FILES PROCESSED. GENERATING CONSOLIDATED REPORTS...")
        print("=" * 70)

        self.generate_reports(files_manifest, batch_duration)

    def generate_reports(self, manifest: List[Dict[str, Any]], batch_duration: float) -> None:
        """Generates all batch reports, CSV, Markdown summary, and review queue."""
        total_files = len(manifest)
        completed = sum(1 for e in manifest if e["status"] in ("COMPLETED", "CACHED"))
        cached = sum(1 for e in manifest if e["status"] == "CACHED")
        failed = sum(1 for e in manifest if e["status"] in ("FAILED_INPUT", "FAILED_PROCESSING"))

        total_pages = sum(e["total_pages"] for e in manifest if e.get("total_pages", -1) > 0)
        processed_pages = sum(e["total_pages"] for e in manifest if e["status"] in ("COMPLETED", "CACHED"))

        # Time statistics for processed files
        valid_times = [e["wall_clock_seconds"] for e in manifest if e.get("wall_clock_seconds")]
        valid_sec_per_page = [e["seconds_per_page"] for e in manifest if e.get("seconds_per_page") is not None]
        valid_fb = [e["fallback_ratio"] for e in manifest if e.get("fallback_ratio") is not None]
        valid_qual = [e["mean_quality_score"] for e in manifest if e.get("mean_quality_score") is not None]

        import numpy as np
        valid_entries = [e for e in manifest if e.get("seconds_per_page") is not None]
        fastest_file = min(valid_entries, key=lambda x: x.get("seconds_per_page"), default={})
        slowest_file = max(valid_entries, key=lambda x: x.get("seconds_per_page"), default={})
        median_sec = float(np.median(valid_sec_per_page)) if valid_sec_per_page else 0.0
        mean_sec = sum(valid_times) / processed_pages if processed_pages else 0.0
        p95_sec = float(np.percentile(valid_sec_per_page, 95)) if valid_sec_per_page else 0.0

        # Classification counts
        native_files = sum(1 for e in manifest if "NATIVE_TEXT_HEALTHY" in e.get("classifications", []))
        scanned_files = sum(1 for e in manifest if "SCANNED" in e.get("classifications", []))
        mixed_files = sum(1 for e in manifest if "MIXED" in e.get("classifications", []))
        table_heavy = sum(1 for e in manifest if "TABLE_HEAVY" in e.get("classifications", []))
        diagram_heavy = sum(1 for e in manifest if "DIAGRAM_HEAVY" in e.get("classifications", []))

        # Build Review Queue
        review_queue = []
        for e in manifest:
            q_score = e.get("mean_quality_score")
            q_score = 1.0 if q_score is None else q_score
            fb_r = e.get("fallback_ratio") or 0.0
            cer_v = e.get("cer")

            if e["status"] in ("FAILED_INPUT", "FAILED_PROCESSING"):
                review_queue.append({"priority": "CRITICAL", "file": e["file_name"], "reason": f"Processing failed: {e.get('error')}"})
            elif cer_v is not None and cer_v > 0.20:
                review_queue.append({"priority": "CRITICAL", "file": e["file_name"], "reason": f"High CER: {cer_v:.2%}"})
            elif q_score < 0.85:
                review_queue.append({"priority": "WARNING", "file": e["file_name"], "reason": f"Low quality score: {q_score:.4f}"})
            elif fb_r > 0.12:
                review_queue.append({"priority": "WARNING", "file": e["file_name"], "reason": f"High fallback ratio: {fb_r:.2%}"})
            elif e.get("seconds_per_page") and e["seconds_per_page"] > 25.0:
                review_queue.append({"priority": "REVIEW", "file": e["file_name"], "reason": f"Slow processing: {e['seconds_per_page']}s/page"})

        with open(self.review_queue_path, "w", encoding="utf-8") as f:
            json.dump(review_queue, f, ensure_ascii=False, indent=2)

        stage_totals = {}
        for entry in manifest:
            if entry["status"] not in ("COMPLETED", "CACHED"):
                continue
            quality_path = self.output_dir / entry["document_id"] / "quality.json"
            if not quality_path.is_file():
                continue
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
            for page in quality.get("page_reports", []):
                for stage, duration in page.get("stage_seconds", {}).items():
                    stage_totals[stage] = stage_totals.get(stage, 0.0) + duration
        measured_total = sum(stage_totals.values())
        measured_stages = [
            {
                "rank": rank,
                "bottleneck": stage,
                "description": f"{duration:.1f}s ({duration / measured_total:.1%} of measured page processing time)",
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

        with open(self.report_json_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)

        # Build batch_report.csv
        csv_headers = [
            "file_name", "total_pages", "status", "wall_clock_seconds", "seconds_per_page",
            "pages_per_minute", "pages_ocr", "pages_native", "fallback_ratio", "mean_quality_score",
            "mean_confidence", "cer", "wer", "diacritic_accuracy", "table_accuracy", "reading_order_accuracy"
        ]
        with open(self.report_csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=csv_headers, extrasaction="ignore")
            writer.writeheader()
            for row in manifest:
                clean_row = dict(row)
                if clean_row.get("fallback_ratio") is not None:
                    clean_row["fallback_ratio"] = f"{clean_row['fallback_ratio']:.2%}"
                writer.writerow(clean_row)

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

        with open(self.report_md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))

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
        with open(self.final_validation_path, "w", encoding="utf-8") as f:
            json.dump(validation_data, f, ensure_ascii=False, indent=2)
        print(f"Validation report đã tạo: {self.final_validation_path}")

        # Build Excel report if excel_path is specified
        if self.excel_path:
            try:
                from scripts.export_batch_excel import build_workbook
                build_workbook(self.output_dir, self.excel_path)
                print(f"Báo cáo Excel đã tạo: {self.excel_path}")
            except Exception as e:
                print(f"[WARN] Excel export failed: {e}", file=sys.stderr)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Batch OCR Runner v3.2.1 for 100 PDF benchmark")
    parser.add_argument("--input", default="inputs", help="Input directory")
    parser.add_argument("--output", default="output", help="Output directory")
    parser.add_argument("--profile", default="balanced", help="Profile (default: balanced)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of files for testing")
    parser.add_argument("--excel", default="output/OCR_BENCHMARK_5_FILES.xlsx", help="Excel output path")
    parser.add_argument("--force", action="store_true", help="Force rerun without cache")
    parser.add_argument("--files", nargs="+", default=None, help="Filter specific files to run in batch")
    args = parser.parse_args()

    runner = BatchOCRRunner(
        input_dir=args.input,
        output_dir=args.output,
        profile=args.profile,
        excel_path=args.excel,
        force=args.force,
        file_list=args.files
    )
    runner.execute_batch(limit=args.limit)

if __name__ == "__main__":
    main()

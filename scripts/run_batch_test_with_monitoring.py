#!/usr/bin/env python3
"""
Master Batch Test Runner with Continuous System Monitoring (CPU & RAM).
Processes PDF files in inputs/ using the local VietOCR profile (CUDA/CPU auto),
collects high-resolution system & process metrics,
and exports comprehensive Excel, CSV, JSON, and Markdown reports.
"""

import sys
import os
import io
import time
import json
import csv
import threading
import argparse
import platform
import math
from pathlib import Path
from typing import List, Dict, Any, Optional

# Force UTF-8 encoding on standard I/O for Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import psutil
from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from src.ocr.cli.batch_runner import BatchOCRRunner
from src.ocr.runtime.config import ConfigError
from scripts.export_batch_excel import build_workbook


HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
SUBHEADER_FILL = PatternFill("solid", fgColor="D9EAF7")
THIN_BORDER = Border(
    left=Side(style="thin", color="D9E1F2"),
    right=Side(style="thin", color="D9E1F2"),
    top=Side(style="thin", color="D9E1F2"),
    bottom=Side(style="thin", color="D9E1F2"),
)


class SystemMonitor(threading.Thread):
    """
    Background worker that continuously samples CPU utilization, system RAM,
    and process memory consumption during the batch test execution.
    """
    def __init__(self, interval: float = 1.0, existing_samples: Optional[List[Dict[str, Any]]] = None):
        super().__init__(daemon=True)
        self.interval = interval
        self.stop_event = threading.Event()
        self.samples: List[Dict[str, Any]] = list(existing_samples or [])
        self.prev_elapsed: float = float(self.samples[-1]["elapsed_seconds"]) if self.samples else 0.0
        self.current_file: str = "INITIALIZING"
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.proc = psutil.Process()

    def set_file_context(self, file_name: str) -> None:
        self.current_file = file_name

    def run(self) -> None:
        self.start_time = time.time()
        # Prime psutil measurement
        psutil.cpu_percent(interval=None)
        self.proc.cpu_percent(interval=None)

        while not self.stop_event.is_set():
            t_now = time.time()
            elapsed = round(self.prev_elapsed + (t_now - self.start_time), 2)
            sys_cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            proc_cpu = self.proc.cpu_percent(interval=None)
            try:
                proc_mem = self.proc.memory_info()
                proc_rss_mb = round(proc_mem.rss / (1024 * 1024), 2)
                proc_vms_mb = round(proc_mem.vms / (1024 * 1024), 2)
            except Exception:
                proc_rss_mb = 0.0
                proc_vms_mb = 0.0

            gpu_vram_mb = 0.0
            try:
                import torch
                if torch.cuda.is_available():
                    gpu_vram_mb = round(torch.cuda.memory_allocated() / (1024 * 1024), 2)
            except Exception:
                gpu_vram_mb = 0.0

            sample = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t_now)),
                "elapsed_seconds": elapsed,
                "current_file": self.current_file,
                "system_cpu_percent": round(sys_cpu, 1),
                "system_ram_used_mb": round(mem.used / (1024 * 1024), 1),
                "system_ram_total_mb": round(mem.total / (1024 * 1024), 1),
                "system_ram_percent": round(mem.percent, 1),
                "process_cpu_percent": round(proc_cpu, 1),
                "process_ram_rss_mb": proc_rss_mb,
                "process_ram_vms_mb": proc_vms_mb,
                "gpu_vram_mb": gpu_vram_mb,
            }
            self.samples.append(sample)
            time.sleep(self.interval)

        self.end_time = time.time()

    def stop(self) -> None:
        self.stop_event.set()

    def compute_summary(self) -> Dict[str, Any]:
        if not self.samples:
            return {}

        total_duration = round(self.prev_elapsed + ((self.end_time or time.time()) - (self.start_time or time.time())), 2)
        sys_cpus = [s["system_cpu_percent"] for s in self.samples]
        proc_cpus = [s["process_cpu_percent"] for s in self.samples]
        sys_rams = [s["system_ram_used_mb"] for s in self.samples]
        proc_rsss = [s["process_ram_rss_mb"] for s in self.samples]
        gpu_vrams = [s.get("gpu_vram_mb", 0.0) for s in self.samples]

        sorted_sys_cpus = sorted(sys_cpus)
        p95_idx = int(len(sorted_sys_cpus) * 0.95)
        sys_cpu_p95 = sorted_sys_cpus[min(p95_idx, len(sorted_sys_cpus) - 1)]

        # Per-file metrics aggregation
        file_samples: Dict[str, List[Dict[str, Any]]] = {}
        for s in self.samples:
            f = s["current_file"]
            if f not in file_samples:
                file_samples[f] = []
            file_samples[f].append(s)

        per_file = []
        for fname, s_list in file_samples.items():
            if fname in ("INITIALIZING", "COMPLETED", "FINALIZING"):
                continue
            f_sys_cpus = [x["system_cpu_percent"] for x in s_list]
            f_proc_rams = [x["process_ram_rss_mb"] for x in s_list]
            f_gpu_vrams = [x.get("gpu_vram_mb", 0.0) for x in s_list]
            per_file.append({
                "file_name": fname,
                "sample_count": len(s_list),
                "duration_seconds": round(len(s_list) * self.interval, 1),
                "avg_cpu_percent": round(sum(f_sys_cpus) / max(1, len(f_sys_cpus)), 1),
                "peak_cpu_percent": round(max(f_sys_cpus), 1) if f_sys_cpus else 0.0,
                "avg_proc_ram_mb": round(sum(f_proc_rams) / max(1, len(f_proc_rams)), 1),
                "peak_proc_ram_mb": round(max(f_proc_rams), 1) if f_proc_rams else 0.0,
                "avg_gpu_vram_mb": round(sum(f_gpu_vrams) / max(1, len(f_gpu_vrams)), 1) if f_gpu_vrams else 0.0,
                "peak_gpu_vram_mb": round(max(f_gpu_vrams), 1) if f_gpu_vrams else 0.0,
            })

        mem = psutil.virtual_memory()
        return {
            "total_duration_seconds": total_duration,
            "total_samples": len(self.samples),
            "sampling_interval_seconds": self.interval,
            "cpu_physical_cores": psutil.cpu_count(logical=False),
            "cpu_logical_cores": psutil.cpu_count(logical=True),
            "system_ram_total_gb": round(mem.total / (1024 ** 3), 2),
            "system_cpu_min_percent": round(min(sys_cpus), 1),
            "system_cpu_avg_percent": round(sum(sys_cpus) / len(sys_cpus), 1),
            "system_cpu_peak_percent": round(max(sys_cpus), 1),
            "system_cpu_p95_percent": round(sys_cpu_p95, 1),
            "process_cpu_avg_percent": round(sum(proc_cpus) / len(proc_cpus), 1),
            "process_cpu_peak_percent": round(max(proc_cpus), 1),
            "system_ram_used_avg_mb": round(sum(sys_rams) / len(sys_rams), 1),
            "system_ram_used_peak_mb": round(max(sys_rams), 1),
            "process_ram_rss_avg_mb": round(sum(proc_rsss) / len(proc_rsss), 1),
            "process_ram_rss_peak_mb": round(max(proc_rsss), 1),
            "gpu_vram_avg_mb": round(sum(gpu_vrams) / len(gpu_vrams), 1) if gpu_vrams else 0.0,
            "gpu_vram_peak_mb": round(max(gpu_vrams), 1) if gpu_vrams else 0.0,
            "per_file_metrics": per_file,
        }

    def save_reports(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        summary = self.compute_summary()

        # 1. system_metrics_summary.json
        sum_path = output_dir / "system_metrics_summary.json"
        with open(sum_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        # 2. system_metrics.json (all samples + summary)
        raw_path = output_dir / "system_metrics.json"
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump({
                "summary": summary,
                "samples": self.samples
            }, f, ensure_ascii=False, indent=2)

        # 3. system_metrics.csv
        csv_path = output_dir / "system_metrics.csv"
        if self.samples:
            fieldnames = list(self.samples[0].keys())
            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.samples)

        # 4. SYSTEM_METRICS_REPORT.md
        md_path = output_dir / "SYSTEM_METRICS_REPORT.md"
        md_lines = [
            "# Báo Cáo Đo Lường Tài Nguyên Hệ Thống Khi Chạy Test OCR",
            "",
            f"- **Thời điểm bắt đầu:** {self.samples[0]['timestamp'] if self.samples else 'N/A'}",
            f"- **Tổng thời gian chạy:** {summary.get('total_duration_seconds', 0)}s (~{summary.get('total_duration_seconds', 0)/60:.1f} phút)",
            f"- **Cấu hình CPU:** {summary.get('cpu_physical_cores', 'N/A')} nhân vật lý / {summary.get('cpu_logical_cores', 'N/A')} luồng logic",
            f"- **Tổng RAM vật lý:** {summary.get('system_ram_total_gb', 'N/A')} GB",
            "",
            "## 1. Chỉ Số Tải Hệ Thống Tổng Thể",
            "",
            "| Chỉ số đo lường | Giá trị | Đơn vị | Ghi chú |",
            "|---|:---:|:---:|---|",
            f"| CPU Hệ thống Trung bình | **{summary.get('system_cpu_avg_percent')}%** | % | Toàn hệ thống |",
            f"| CPU Hệ thống Đỉnh (Peak) | **{summary.get('system_cpu_peak_percent')}%** | % | Mức cao nhất ghi nhận |",
            f"| CPU Hệ thống P95 | **{summary.get('system_cpu_p95_percent')}%** | % | 95% thời gian dưới mức này |",
            f"| CPU Tiến trình OCR (TB) | **{summary.get('process_cpu_avg_percent')}%** | % | Tải tiến trình Python |",
            f"| RAM Hệ thống Trung bình | **{summary.get('system_ram_used_avg_mb'):,} MB** | MB | Tổng bộ nhớ đã dùng |",
            f"| RAM Hệ thống Đỉnh (Peak) | **{summary.get('system_ram_used_peak_mb'):,} MB** | MB | Mức RAM hệ thống cao nhất |",
            f"| RAM Tiến trình OCR (TB) | **{summary.get('process_ram_rss_avg_mb'):,} MB** | MB | RSS tiến trình Python |",
            f"| RAM Tiến trình OCR (Đỉnh) | **{summary.get('process_ram_rss_peak_mb'):,} MB** | MB | Đỉnh bộ nhớ tiến trình |",
            "",
            "## 2. Tiêu Thụ Tài Nguyên Theo Từng Tệp PDF",
            "",
            "| Tên tệp PDF | Thời gian (s) | CPU TB (%) | CPU Đỉnh (%) | RAM Process TB (MB) | RAM Process Đỉnh (MB) |",
            "|---|:---:|:---:|:---:|:---:|:---:|",
        ]

        for pf in summary.get("per_file_metrics", []):
            md_lines.append(
                f"| `{pf['file_name']}` | {pf['duration_seconds']}s | {pf['avg_cpu_percent']}% | {pf['peak_cpu_percent']}% | {pf['avg_proc_ram_mb']:,} MB | {pf['peak_proc_ram_mb']:,} MB |"
            )

        md_lines.extend([
            "",
            "## 3. Danh Sách Tệp Đo Lường Xuất Ra",
            "",
            f"- **File Excel đầy đủ:** `output/OCR_BENCHMARK_10_FILES.xlsx` (Bao gồm sheet riêng *'Do luong he thong'* kèm biểu đồ LineChart).",
            f"- **File Excel chuyên biệt đo hệ thống:** `output/SYSTEM_METRICS_REPORT.xlsx`",
            f"- **File CSV chuỗi thời gian:** `output/system_metrics.csv` ({len(self.samples)} mẫu đo).",
            f"- **File JSON chi tiết:** `output/system_metrics.json` và `output/system_metrics_summary.json`.",
        ])

        md_path.write_text("\n".join(md_lines), encoding="utf-8")

        # 5. Standalone SYSTEM_METRICS_REPORT.xlsx
        self.save_standalone_excel(output_dir / "SYSTEM_METRICS_REPORT.xlsx", summary)

    def save_standalone_excel(self, excel_path: Path, summary: Dict[str, Any]) -> None:
        wb = Workbook()
        ws = wb.active
        ws.title = "Do luong he thong"

        ws["A1"] = "BÁO CÁO ĐO LƯỜNG TÀI NGUYÊN HỆ THỐNG KHI TEST BATCH OCR"
        ws["A1"].font = Font(name="Arial", size=14, bold=True, color="FFFFFF")
        ws["A1"].fill = HEADER_FILL
        ws.merge_cells("A1:G1")
        ws.row_dimensions[1].height = 34

        kpis = [
            ("Thời gian chạy tổng cộng (giây)", summary.get("total_duration_seconds")),
            ("Thời gian chạy tổng cộng (phút)", round(summary.get("total_duration_seconds", 0) / 60, 2)),
            ("Số lượng mẫu đo (samples)", summary.get("total_samples")),
            ("Chu kỳ lấy mẫu (giây)", summary.get("sampling_interval_seconds")),
            ("CPU Physical Cores", summary.get("cpu_physical_cores")),
            ("CPU Logical Cores", summary.get("cpu_logical_cores")),
            ("Tổng dung lượng RAM (GB)", summary.get("system_ram_total_gb")),
            ("CPU Hệ thống TB (%)", summary.get("system_cpu_avg_percent")),
            ("CPU Hệ thống Đỉnh (%)", summary.get("system_cpu_peak_percent")),
            ("CPU Hệ thống P95 (%)", summary.get("system_cpu_p95_percent")),
            ("CPU Tiến trình OCR TB (%)", summary.get("process_cpu_avg_percent")),
            ("RAM Hệ thống TB (MB)", summary.get("system_ram_used_avg_mb")),
            ("RAM Hệ thống Đỉnh (MB)", summary.get("system_ram_used_peak_mb")),
            ("RAM Tiến trình OCR TB (RSS MB)", summary.get("process_ram_rss_avg_mb")),
            ("RAM Tiến trình OCR Đỉnh (RSS MB)", summary.get("process_ram_rss_peak_mb")),
        ]

        ws.append([])
        ws.append(["Chỉ Số Đo Lường", "Giá Trị"])
        for cell in ws[3]:
            cell.fill = SUBHEADER_FILL
            cell.font = Font(bold=True)
            cell.border = THIN_BORDER
        for label, val in kpis:
            ws.append([label, val])
            ws.cell(ws.max_row, 1).border = THIN_BORDER
            ws.cell(ws.max_row, 2).border = THIN_BORDER
            ws.cell(ws.max_row, 2).alignment = Alignment(horizontal="right", vertical="center")

        # Per-file Table
        file_stats = summary.get("per_file_metrics", [])
        if file_stats:
            ws.append([])
            ws.cell(row=ws.max_row + 1, column=1, value="Tiêu Thụ Tài Nguyên Theo Từng Tệp PDF").font = Font(bold=True, size=11, color="1F4E78")
            f_headers = ["Tệp PDF", "Thời gian (s)", "CPU TB (%)", "CPU Đỉnh (%)", "RAM Process TB (MB)", "RAM Process Đỉnh (MB)"]
            ws.append(f_headers)
            h_row = ws.max_row
            for cell in ws[h_row]:
                cell.fill = SUBHEADER_FILL
                cell.font = Font(bold=True)
                cell.border = THIN_BORDER
            for pf in file_stats:
                ws.append([
                    pf.get("file_name"),
                    pf.get("duration_seconds"),
                    pf.get("avg_cpu_percent"),
                    pf.get("peak_cpu_percent"),
                    pf.get("avg_proc_ram_mb"),
                    pf.get("peak_proc_ram_mb"),
                ])
                for col_idx in range(1, 7):
                    ws.cell(ws.max_row, col_idx).border = THIN_BORDER

        # Time-series Samples
        if self.samples:
            ws.append([])
            step = max(1, len(self.samples) // 1000)
            downsampled = self.samples[::step]
            ws.cell(row=ws.max_row + 1, column=1, value="Chuỗi Thời Gian Đo Lường (Telemetry Time-Series)").font = Font(bold=True, size=11, color="1F4E78")
            ts_headers = [
                "Thời điểm", "Giây trôi qua", "Tệp đang chạy", "CPU Hệ thống (%)",
                "RAM Hệ thống (MB)", "RAM Hệ thống (%)", "CPU Process (%)", "RAM Process RSS (MB)"
            ]
            ws.append(ts_headers)
            header_row = ws.max_row
            for cell in ws[header_row]:
                cell.fill = SUBHEADER_FILL
                cell.font = Font(bold=True)
                cell.border = THIN_BORDER

            data_start_row = header_row + 1
            for s in downsampled:
                ws.append([
                    s.get("timestamp"),
                    s.get("elapsed_seconds"),
                    s.get("current_file"),
                    s.get("system_cpu_percent"),
                    s.get("system_ram_used_mb"),
                    s.get("system_ram_percent"),
                    s.get("process_cpu_percent"),
                    s.get("process_ram_rss_mb"),
                ])
                for col_idx in range(1, 9):
                    ws.cell(ws.max_row, col_idx).border = THIN_BORDER
            data_end_row = ws.max_row

            if data_end_row > data_start_row:
                chart = LineChart()
                chart.title = "Đo Lường CPU (%) và RAM Tiến Trình (MB) Theo Thời Gian"
                chart.style = 13
                chart.y_axis.title = "Tải / Dung lượng"
                chart.x_axis.title = "Giây trôi qua"
                chart.height = 14
                chart.width = 24

                data_ref = Reference(ws, min_col=4, min_row=header_row, max_row=data_end_row)
                chart.add_data(data_ref, titles_from_data=True)
                cats = Reference(ws, min_col=2, min_row=data_start_row, max_row=data_end_row)
                chart.set_categories(cats)
                ws.add_chart(chart, "I3")

        # Column widths
        for idx, col in enumerate(ws.columns, 1):
            values = [str(c.value) if c.value is not None else "" for c in col[:200]]
            max_len = max([len(v) for v in values] + [12])
            ws.column_dimensions[get_column_letter(idx)].width = min(45, max_len + 3)

        try:
            wb.save(excel_path)
        except PermissionError:
            alt = excel_path.parent / f"{excel_path.stem}_latest{excel_path.suffix}"
            wb.save(alt)
            print(f"[WARN] {excel_path.name} locked, saved to {alt.name}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Run Batch OCR Test with Continuous System Monitoring")
    parser.add_argument("--inputs", default="inputs", help="Inputs folder path (default: inputs)")
    parser.add_argument("--output", default="output", help="Output directory path (default: output)")
    parser.add_argument("--profile", default="local",
                        help="local: VietOCR CUDA/CPU auto (default); fast: RapidOCR; balanced/accuracy: VietOCR + Docling for lab")
    parser.add_argument("--config", default=None, help="Custom processing profile YAML")
    parser.add_argument("--device", choices=("cpu", "cuda", "auto"), default=None)
    parser.add_argument("--threads-per-worker", type=int, default=None, help="Override backend CPU thread budget (local: 2)")
    parser.add_argument("--batch-size", type=int, default=None, help="Maximum VietOCR lines per recognition batch (local: 4)")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of PDF files to test")
    parser.add_argument("--files", nargs="+", default=None, help="Only test these PDF filenames or stems")
    parser.add_argument("--excel", default=None, help="Excel output path (default: OCR_BENCHMARK_RESULTS.xlsx in --output)")
    parser.add_argument("--interval", type=float, default=1.0, help="Monitoring sampling interval in seconds (default: 1.0)")
    parser.add_argument("--max-files", type=int, default=None, help="Stop this run gracefully after processing N files")
    parser.add_argument("--max-seconds", type=float, default=None, help="Stop this run gracefully after N seconds before starting next file")
    parser.add_argument("--force", action="store_true", help="Force re-run even if cached")
    args = parser.parse_args(argv)
    for option in ("threads_per_worker", "batch_size", "limit"):
        value = getattr(args, option)
        if value is not None and value < 1:
            parser.error(f"--{option.replace('_', '-')} must be positive")
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("--interval must be a finite positive number")

    input_dir = Path(args.inputs).resolve()
    output_dir = Path(args.output).resolve()
    excel_path = Path(args.excel).resolve() if args.excel else output_dir / "OCR_BENCHMARK_RESULTS.xlsx"

    if not input_dir.exists():
        print(f"[ERROR] Inputs directory not found: {input_dir}")
        sys.exit(1)

    pdf_files = sorted(list(input_dir.glob("*.pdf")))
    if args.files:
        targets = {Path(name).name.lower() for name in args.files}
        pdf_files = [path for path in pdf_files if path.name.lower() in targets or path.stem.lower() in targets]
    if args.limit is not None:
        pdf_files = pdf_files[:args.limit]
    if not pdf_files:
        print(f"[ERROR] No PDF files found in {input_dir}")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Validate the resolved configuration before starting a monitoring thread.
    try:
        runner = BatchOCRRunner(
            input_dir=str(input_dir), output_dir=str(output_dir), profile=args.profile,
            excel_path=str(excel_path), force=args.force, config_path=args.config,
            device=args.device, threads_per_worker=args.threads_per_worker,
            batch_size=args.batch_size, file_list=args.files,
        )
    except (ConfigError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))

    print("=" * 80)
    print("      VIETNAMESE OCR — BATCH TEST RUNNER WITH CONTINUOUS SYSTEM MONITORING")
    print("=" * 80)
    print(f"[*] Input Directory:   {input_dir}")
    print(f"[*] Output Directory:  {output_dir}")
    print(f"[*] OCR Profile:       {args.profile}")
    config = runner.profile_config
    print(f"[*] OCR Engine:        {config.get('primary_engine')} / {config.get('device')}")
    if config.get("device") == "cuda":
        import torch
        gpu = torch.cuda.get_device_properties(0)
        print(f"[*] VietOCR GPU:       {gpu.name} ({gpu.total_memory / 1024**3:.2f} GiB VRAM)")
    elif args.profile == "local":
        print("[*] VietOCR Device:    CPU (use --device cuda to require CUDA, --device cpu to force CPU)")
    print(f"[*] Backend Threads:   {config.get('num_threads', 'default')}")
    print(f"[*] Recognition Batch: {config.get('batch_size')}")
    print(f"[*] Docling Structure: {config.get('docling', {}).get('enabled', False)}")
    print(f"[*] Target Excel:      {excel_path}")
    print(f"[*] Total PDF Files:   {len(pdf_files)}")
    print(f"[*] Sampling Interval: {args.interval}s")
    print(f"[*] OS / Platform:     {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"[*] CPU Cores:         {psutil.cpu_count(logical=False)} Physical / {psutil.cpu_count(logical=True)} Logical")
    print(f"[*] System RAM:        {psutil.virtual_memory().total / (1024**3):.2f} GB")
    print("=" * 80)

    # 1. Start Background System Monitor (load prior samples if available)
    existing_samples = []
    raw_metrics_file = output_dir / "system_metrics.json"
    if not args.force and raw_metrics_file.exists():
        try:
            with open(raw_metrics_file, "r", encoding="utf-8") as f:
                prev_json = json.load(f)
                existing_samples = prev_json.get("samples", [])
        except Exception:
            existing_samples = []

    monitor = SystemMonitor(interval=args.interval, existing_samples=existing_samples)
    monitor.start()
    print(f"[*] Background System Monitor: RUNNING (polling CPU & RAM every {args.interval}s)...")

    class GracefulBatchStop(BaseException):
        pass

    processed_files_count = 0
    batch_t0 = time.time()

    # Hook converter execute to notify monitor of active file
    orig_execute = runner.converter.execute
    def monitored_execute(*exec_args, **exec_kwargs):
        nonlocal processed_files_count
        if args.max_seconds and (time.time() - batch_t0) >= args.max_seconds:
            print(f"\n[INFO] Reached max run duration ({args.max_seconds}s). Gracefully stopping batch.")
            raise GracefulBatchStop()
        if args.max_files and processed_files_count >= args.max_files:
            print(f"\n[INFO] Reached target file count ({args.max_files} files). Gracefully stopping batch.")
            raise GracefulBatchStop()

        f_path = exec_kwargs.get("file_path") or (exec_args[0] if exec_args else "")
        f_name = Path(f_path).name if f_path else "unknown"
        monitor.set_file_context(f_name)
        res = orig_execute(*exec_args, **exec_kwargs)
        processed_files_count += 1
        try:
            monitor.save_reports(output_dir)
        except Exception:
            pass
        return res

    runner.converter.execute = monitored_execute

    exit_code = 0
    try:
        runner.execute_batch(limit=args.limit)
    except GracefulBatchStop:
        print("\n[INFO] Batch cycle finished cleanly under target limit. Reports updated successfully.")
        exit_code = 0
    except KeyboardInterrupt:
        print("\n[WARN] Batch execution interrupted by user!")
        exit_code = 130
    except Exception as exc:
        print(f"\n[ERROR] Exception during batch run: {exc}")
        import traceback
        traceback.print_exc()
        exit_code = 1
    finally:
        monitor.set_file_context("FINALIZING")
        monitor.stop()
        monitor.join(timeout=5.0)

    total_batch_sec = round(time.time() - batch_t0, 2)
    print("\n" + "=" * 80)
    print("               TEST RUN COMPLETED — GENERATING REPORTS               ")
    print("=" * 80)
    print(f"[*] Total Execution Time: {total_batch_sec:.2f}s (~{total_batch_sec/60:.2f} phút)")

    # 3. Save System Metrics Reports (JSON, CSV, MD, standalone XLSX)
    monitor.save_reports(output_dir)
    print(f"[OK] System Metrics JSON:       {output_dir / 'system_metrics.json'}")
    print(f"[OK] System Metrics Summary:    {output_dir / 'system_metrics_summary.json'}")
    print(f"[OK] System Metrics CSV:        {output_dir / 'system_metrics.csv'}")
    print(f"[OK] System Metrics Markdown:   {output_dir / 'SYSTEM_METRICS_REPORT.md'}")
    print(f"[OK] System Metrics Standalone: {output_dir / 'SYSTEM_METRICS_REPORT.xlsx'}")

    # 4. Generate Master Excel Report (with all sheets including 'Do luong he thong')
    try:
        build_workbook(output_dir, excel_path)
        print(f"[OK] Master Benchmark Excel:    {excel_path}")
        # Mirror copy to OCR_BENCHMARK_RESULTS.xlsx
        mirror_path = output_dir / "OCR_BENCHMARK_RESULTS.xlsx"
        import shutil
        if excel_path.exists() and excel_path != mirror_path:
            shutil.copy2(excel_path, mirror_path)
            print(f"[OK] Mirrored Excel Copy:       {mirror_path}")
    except Exception as exc:
        print(f"[WARN] Master Excel export error: {exc}")

    # 5. Generate Markdown report for the selected files.
    try:
        generate_test_markdown_report(output_dir, len(pdf_files), excel_path)
    except Exception as exc:
        print(f"[WARN] Markdown report generation error: {exc}")

    print("\n" + "=" * 80)
    print("TEST RUN FINISHED — SEE BATCH REPORT FOR EACH FILE STATUS" if exit_code == 0
          else "TEST RUN INTERRUPTED OR FAILED — PARTIAL MEASUREMENTS EXPORTED")
    print("=" * 80)
    if exit_code:
        raise SystemExit(exit_code)


def generate_test_markdown_report(output_dir: Path, total_files: int, excel_path: Optional[Path] = None) -> None:
    batch_json = output_dir / "batch_report.json"
    if not batch_json.exists():
        return
    with open(batch_json, "r", encoding="utf-8") as f:
        batch_data = json.load(f)

    summary = batch_data.get("summary", {})
    files = batch_data.get("files", [])

    completed_files = [f for f in files if f.get("status") in ("COMPLETED", "CACHED")]
    tot_pages = sum(f.get("total_pages", 0) for f in files)
    tot_batch = sum(f.get("wall_clock_seconds", 0) or 0 for f in completed_files)
    avg_sec = (tot_batch / tot_pages) if tot_pages > 0 else 0.0
    ppm_all = ((tot_pages / tot_batch) * 60) if tot_batch > 0 else 0.0

    md_lines = [
        f"# Báo Cáo Kết Quả Kiểm Thử {len(files)} Tệp PDF Inputs",
        "",
        f"- **Thời điểm kiểm thử:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **Profile OCR:** `{summary.get('profile', 'unknown')}`",
        f"- **Số tệp được chọn:** {total_files}",
        f"- **Tổng số tệp đã kiểm thử:** {summary.get('total_files', len(files))}",
        f"- **Số tệp hoàn tất thành công:** {len(completed_files)}",
        f"- **Tổng số trang đã xử lý:** {tot_pages} trang",
        f"- **Tổng thời gian xử lý:** {tot_batch:.2f} giây (~{tot_batch/60:.1f} phút / ~{tot_batch/3600:.2f} giờ)",
        f"- **Tốc độ trung bình toàn bộ:** {avg_sec:.2f} giây/trang",
        f"- **Thông lượng xử lý:** {ppm_all:.2f} trang/phút",
        "",
        "## Chi Tiết Kết Quả Từng Tệp",
        "",
        "| Tệp PDF | Dung lượng (MB) | Tổng trang | Thời gian (s) | s/trang | Trang/phút | Native | OCR | Mixed | Trạng thái |",
        "|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]

    for item in files:
        w_sec = item.get('wall_clock_seconds')
        w_sec_str = f"{w_sec:.2f}s" if isinstance(w_sec, (int, float)) else "-"
        s_page = item.get('seconds_per_page')
        s_page_str = f"{s_page:.2f}s" if isinstance(s_page, (int, float)) else "-"
        p_min = item.get('pages_per_minute')
        p_min_str = f"{p_min:.2f}" if isinstance(p_min, (int, float)) else "-"

        md_lines.append(
            f"| `{item.get('file_name')}` | {item.get('file_size_mb')} | {item.get('total_pages')} | {w_sec_str} | {s_page_str} | {p_min_str} | {item.get('pages_native') or 0} | {item.get('pages_ocr') or 0} | {item.get('pages_mixed') or 0} | **{item.get('status')}** |"
        )

    md_lines.extend([
        "",
        "## Các Tệp Kết Quả Đã Xuất",
        "",
        f"- **Báo cáo Excel Đầy Đủ:** `{excel_path or output_dir / 'OCR_BENCHMARK_RESULTS.xlsx'}` (Tổng quan, Chi tiết file/trang, Cần kiểm tra, Đo lường hệ thống, Tiêu chuẩn đo lường).",
        f"- **Báo cáo Excel Đo Lường Hệ Thống:** `{output_dir / 'SYSTEM_METRICS_REPORT.xlsx'}`.",
        f"- **Dữ liệu Đo Lường Chuỗi Thời Gian (CSV):** `{output_dir / 'system_metrics.csv'}`.",
        f"- **Dữ liệu JSON Đo Lường:** `{output_dir / 'system_metrics.json'}` và `{output_dir / 'system_metrics_summary.json'}`.",
    ])

    out_md = output_dir / f"TEST_{len(files)}_FILES_REPORT.md"
    out_md.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"[OK] Batch Markdown Report:     {out_md}")


if __name__ == "__main__":
    main()

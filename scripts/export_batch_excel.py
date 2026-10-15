#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from src.ocr.infrastructure.export.result_reader import load_document_json


HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(color="FFFFFF", bold=True)
SUBHEADER_FILL = PatternFill("solid", fgColor="D9EAF7")
GOOD_FILL = PatternFill("solid", fgColor="E2F0D9")
WARN_FILL = PatternFill("solid", fgColor="FFF2CC")
BAD_FILL = PatternFill("solid", fgColor="FCE4D6")
THIN_BORDER = Border(
    left=Side(style="thin", color="D9E1F2"),
    right=Side(style="thin", color="D9E1F2"),
    top=Side(style="thin", color="D9E1F2"),
    bottom=Side(style="thin", color="D9E1F2"),
)


def load_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def style_table(ws, freeze: str = "A2") -> None:
    ws.freeze_panes = freeze
    ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = THIN_BORDER
    ws.row_dimensions[1].height = 32
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.border = THIN_BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def fit_columns(ws, min_width: int = 10, max_width: int = 55) -> None:
    for idx, column in enumerate(ws.columns, 1):
        values = [str(cell.value) if cell.value is not None else "" for cell in column[:200]]
        width = max([len(value) for value in values] + [min_width]) + 2
        ws.column_dimensions[get_column_letter(idx)].width = min(max_width, width)


def append_rows(ws, headers: List[str], rows: Iterable[List[Any]]) -> None:
    ws.append(headers)
    for row in rows:
        ws.append(row)


def build_workbook(output_dir: Path, excel_path: Path) -> None:
    batch = load_json(output_dir / "batch_report.json", {}) or {}
    summary = batch.get("summary", {})
    files = batch.get("files", [])
    review_queue = load_json(output_dir / "review_queue.json", []) or []

    workbook = Workbook()
    overview = workbook.active
    overview.title = "Tong quan"

    overview["A1"] = f"BÁO CÁO KIỂM NGHIỆM OCR BATCH - {len(files)} PDF"
    overview["A1"].font = Font(size=16, bold=True, color="FFFFFF")
    overview["A1"].fill = HEADER_FILL
    overview.merge_cells("A1:D1")
    overview["A2"] = "Pipeline"
    overview["B2"] = summary.get("pipeline_version")
    overview["C2"] = "Profile"
    overview["D2"] = summary.get("profile")

    completed_files = [f for f in files if f.get("status") in ("COMPLETED", "CACHED") and f.get("seconds_per_page") is not None]

    if completed_files:
        tot_files = len(files)
        comp_count = len(completed_files)
        fail_count = sum(1 for f in files if f.get("status") in ("FAILED", "FAILED_INPUT"))
        tot_pages = sum(f.get("total_pages", 0) for f in files)
        tot_duration = round(sum(f.get("wall_clock_seconds", 0) or 0 for f in completed_files), 2)
        sec_list = sorted([f.get("seconds_per_page") for f in completed_files if f.get("seconds_per_page") is not None])
        avg_sec = round(tot_duration / tot_pages, 2) if tot_pages > 0 else 0.0
        import statistics
        median_sec = round(statistics.median(sec_list), 2) if sec_list else 0.0
        p95_idx = int(0.95 * len(sec_list)) if sec_list else 0
        p95_sec = round(sec_list[min(p95_idx, len(sec_list)-1)], 2) if sec_list else 0.0
        ppm_val = round((tot_pages / tot_duration) * 60, 2) if tot_duration > 0 else 0.0
        fastest_item = min(completed_files, key=lambda x: x.get("seconds_per_page", 999999))
        slowest_item = max(completed_files, key=lambda x: x.get("seconds_per_page", -1))
        fastest_name = fastest_item.get("file_name", fastest_item.get("document_id"))
        slowest_name = slowest_item.get("file_name", slowest_item.get("document_id"))
    else:
        tot_files = summary.get("total_files")
        comp_count = summary.get("completed")
        fail_count = summary.get("failed")
        tot_pages = summary.get("processed_pages")
        tot_duration = summary.get("total_batch_duration_seconds")
        avg_sec = summary.get("average_seconds_per_page")
        median_sec = summary.get("median_seconds_per_page")
        p95_sec = summary.get("p95_seconds_per_page")
        ppm_val = summary.get("overall_pages_per_minute")
        fastest_name = (summary.get("fastest_file") or {}).get("file_name")
        slowest_name = (summary.get("slowest_file") or {}).get("file_name")

    metrics = [
        ("Tổng file", tot_files),
        ("File hoàn tất", comp_count),
        ("File lỗi", fail_count),
        ("Tổng trang", tot_pages),
        ("Tổng thời gian (giây)", tot_duration),
        ("Trung bình giây/trang", avg_sec),
        ("Trung vị giây/trang", median_sec),
        ("P95 giây/trang", p95_sec),
        ("Trang/phút", ppm_val),
        ("File nhanh nhất", fastest_name),
        ("File chậm nhất", slowest_name),
    ]
    overview.append([])
    overview.append(["Chỉ số", "Giá trị"])
    for label, value in metrics:
        overview.append([label, value])
    for cell in overview[4]:
        cell.fill = SUBHEADER_FILL
        cell.font = Font(bold=True)

    overview["F2"] = "File"
    overview["G2"] = "Giây/trang"
    for row_idx, item in enumerate(completed_files, 3):
        overview.cell(row=row_idx, column=6, value=item.get("document_id"))
        overview.cell(row=row_idx, column=7, value=item.get("seconds_per_page"))
    if completed_files:
        chart = BarChart()
        chart.title = "Tốc độ theo tài liệu"
        chart.y_axis.title = "Giây/trang"
        chart.x_axis.title = "Tài liệu"
        chart.height = 14
        chart.width = 24
        chart.add_data(Reference(overview, min_col=7, min_row=2, max_row=2 + len(completed_files)), titles_from_data=True)
        chart.set_categories(Reference(overview, min_col=6, min_row=3, max_row=2 + len(completed_files)))
        overview.add_chart(chart, "I2")
    overview.column_dimensions["A"].width = 30
    overview.column_dimensions["B"].width = 28
    overview.column_dimensions["C"].width = 18
    overview.column_dimensions["D"].width = 18
    overview.column_dimensions["F"].width = 32
    overview.column_dimensions["G"].width = 14

    file_sheet = workbook.create_sheet("Chi tiet file")
    file_headers = [
        "File", "Dung lượng MB", "Trang", "Trạng thái", "Thời gian (s)",
        "Giây/trang", "Trang/phút", "Native", "OCR", "Mixed", "Fallback lines",
        "Tổng lines", "Fallback %", "Confidence", "Quality", "Peak RAM MB",
        "Phân loại", "Unicode lỗi", "Xoay ảnh", "Extraction lỗi", "Số bảng",
    ]
    file_rows = []
    page_rows: List[List[Any]] = []
    review_rows: List[List[Any]] = []

    for item in files:
        document_id = item.get("document_id")
        quality = load_json(output_dir / document_id / "quality.json", {}) or {}
        result_path = output_dir / document_id / "result.json"
        result = load_document_json(result_path) if result_path.is_file() else {}
        result_pages = {p.get("page_num"): p for p in result.get("pages", [])}
        reports = quality.get("page_reports", [])
        unicode_pages = sum(bool(p.get("unicode_corruption_detected")) for p in reports)
        rotated_pages = sum(bool(p.get("orientation_fallback")) for p in reports)
        extraction_failures = sum(bool(p.get("native_extraction_failed")) for p in reports)
        table_count = sum(len(p.get("tables", [])) for p in result.get("pages", []))

        file_rows.append([
            item.get("file_name"), item.get("file_size_mb"), item.get("total_pages"),
            item.get("status"), item.get("wall_clock_seconds"), item.get("seconds_per_page"),
            item.get("pages_per_minute"), item.get("pages_native"), item.get("pages_ocr"),
            item.get("pages_mixed"), item.get("fallback_lines"), item.get("total_lines"),
            item.get("fallback_ratio"), item.get("mean_confidence"), item.get("mean_quality_score"),
            item.get("peak_memory_mb"), ", ".join(item.get("classifications", [])), unicode_pages,
            rotated_pages, extraction_failures, table_count,
        ])

        for page in reports:
            page_num = page.get("page")
            page_result = result_pages.get(page_num, {})
            suspicious = page.get("suspicious_lines", []) or []
            page_rows.append([
                item.get("file_name"), page_num, page.get("page_type"),
                page.get("time_taken_seconds"), page.get("native_text_score"),
                page.get("ocr_confidence"), page.get("quality_score"), page.get("total_blocks"),
                page.get("fallback_count"), len(suspicious), bool(page.get("unicode_corruption_detected")),
                bool(page.get("native_extraction_failed")), page.get("orientation_fallback"),
                bool(page.get("vector_table_probe_enabled")), len(page_result.get("tables", [])),
                " | ".join(suspicious),
            ])
            reasons = []
            if page.get("page_type") != "PRODUCT_SHOWCASE_COLLAGE":
                quality = page.get("quality_score", 1.0)
                if quality is None:
                    reasons.append("Quality unavailable (model output only)")
                elif quality < 0.90:
                    reasons.append("Quality < 0.90")
                confidence = page.get("ocr_confidence")
                if confidence is not None and confidence < 0.90:
                    reasons.append("Confidence < 0.90")
            if page.get("unicode_corruption_detected"):
                reasons.append("Unicode lỗi")
            if page.get("native_extraction_failed"):
                reasons.append("Không đọc được native text")
            if page.get("time_taken_seconds", 0.0) > 25.0:
                reasons.append("Chậm > 25 giây")
            if page.get("total_blocks", 0) == 0 and len(page_result.get("tables", [])) == 0:
                reasons.append("Không có block nội dung")
            if reasons:
                review_rows.append([
                    item.get("file_name"), page_num, ", ".join(reasons), page.get("page_type"),
                    page.get("time_taken_seconds"), page.get("ocr_confidence"),
                    page.get("quality_score"), len(suspicious), " | ".join(suspicious),
                ])

    append_rows(file_sheet, file_headers, file_rows)
    style_table(file_sheet)
    fit_columns(file_sheet)
    for row in range(2, file_sheet.max_row + 1):
        for col in (13, 14, 15):
            c = file_sheet.cell(row, col)
            if c.value is not None:
                c.number_format = "0.00%"
        status_cell = file_sheet.cell(row, 4)
        if status_cell.value in ("COMPLETED", "CACHED"):
            status_cell.fill = GOOD_FILL
        elif status_cell.value == "PENDING":
            status_cell.fill = WARN_FILL
        else:
            status_cell.fill = BAD_FILL

    page_sheet = workbook.create_sheet("Chi tiet trang")
    page_headers = [
        "File", "Trang", "Route", "Thời gian (s)", "Native score", "OCR confidence",
        "Quality", "Blocks", "Fallback", "Dòng nghi vấn", "Unicode lỗi",
        "Extraction lỗi", "Xoay ảnh", "Vector table probe", "Số bảng", "Chi tiết nghi vấn",
    ]
    append_rows(page_sheet, page_headers, page_rows)
    style_table(page_sheet)
    fit_columns(page_sheet, max_width=70)
    for row in range(2, page_sheet.max_row + 1):
        for col in (5, 6, 7):
            page_sheet.cell(row, col).number_format = "0.00%"
    if page_sheet.max_row > 1:
        page_sheet.conditional_formatting.add(
            f"G2:G{page_sheet.max_row}",
            ColorScaleRule(start_type="num", start_value=0.8, start_color="F8696B",
                           mid_type="num", mid_value=0.95, mid_color="FFEB84",
                           end_type="num", end_value=1.0, end_color="63BE7B"),
        )

    review_sheet = workbook.create_sheet("Can kiem tra")
    review_headers = [
        "File", "Trang", "Lý do", "Route", "Thời gian (s)", "Confidence",
        "Quality", "Dòng nghi vấn", "Chi tiết",
    ]
    append_rows(review_sheet, review_headers, review_rows)
    style_table(review_sheet)
    fit_columns(review_sheet, max_width=75)
    for row in range(2, review_sheet.max_row + 1):
        review_sheet.cell(row, 3).fill = WARN_FILL
        review_sheet.cell(row, 6).number_format = "0.00%"
        review_sheet.cell(row, 7).number_format = "0.00%"

    queue_sheet = workbook.create_sheet("Review queue")
    append_rows(
        queue_sheet,
        ["Mức độ", "File", "Lý do"],
        [[entry.get("priority"), entry.get("file"), entry.get("reason")] for entry in review_queue],
    )
    style_table(queue_sheet)
    fit_columns(queue_sheet)

    # Thêm trang đo lường hệ thống & tiêu chuẩn khoa học
    add_system_metrics_sheet(workbook, output_dir)
    add_standards_sheet(workbook)

    excel_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        workbook.save(excel_path)
    except PermissionError:
        alt_path = excel_path.parent / f"{excel_path.stem}_latest{excel_path.suffix}"
        workbook.save(alt_path)
        print(f"[WARN] {excel_path.name} is locked by another program. Saved to {alt_path.name}")
        excel_path = alt_path

    # Re-open once to ensure the generated workbook is structurally readable.
    try:
        checked = load_workbook(excel_path, read_only=True, data_only=False)
        expected = {"Tong quan", "Chi tiet file", "Chi tiet trang", "Can kiem tra", "Review queue"}
        if not expected.issubset(set(checked.sheetnames)):
            raise RuntimeError("Workbook validation failed: missing sheets")
        checked.close()
    except Exception as e:
        print(f"[WARN] Workbook read verification skipped: {e}")


def add_system_metrics_sheet(workbook: Workbook, output_dir: Path) -> None:
    summary = load_json(output_dir / "system_metrics_summary.json", {}) or {}
    metrics_data = load_json(output_dir / "system_metrics.json", {}) or {}
    samples = metrics_data.get("samples", [])
    if not summary and not samples:
        return

    ws = workbook.create_sheet("Do luong he thong")
    ws["A1"] = "BÁO CÁO ĐO LƯỜNG TÀI NGUYÊN HỆ THỐNG TRONG QUÁ TRÌNH KIỂM THỬ OCR"
    ws["A1"].font = Font(size=14, bold=True, color="FFFFFF")
    ws["A1"].fill = HEADER_FILL
    ws.merge_cells("A1:G1")
    ws.row_dimensions[1].height = 34

    kpis = [
        ("Thời gian đo tổng cộng (s)", summary.get("total_duration_seconds")),
        ("Thời gian đo (phút)", round(summary.get("total_duration_seconds", 0) / 60, 2) if summary.get("total_duration_seconds") else None),
        ("CPU Hệ thống TB (%)", summary.get("system_cpu_avg_percent")),
        ("CPU Hệ thống Đỉnh (%)", summary.get("system_cpu_peak_percent")),
        ("CPU Hệ thống P95 (%)", summary.get("system_cpu_p95_percent")),
        ("CPU Tiến trình TB (%)", summary.get("process_cpu_avg_percent")),
        ("RAM Hệ thống TB (MB)", summary.get("system_ram_used_avg_mb")),
        ("RAM Hệ thống Đỉnh (MB)", summary.get("system_ram_used_peak_mb")),
        ("RAM Tiến trình TB (RSS MB)", summary.get("process_ram_rss_avg_mb")),
        ("RAM Tiến trình Đỉnh (RSS MB)", summary.get("process_ram_rss_peak_mb")),
        ("Tổng RAM Hệ thống (GB)", summary.get("system_ram_total_gb")),
        ("Số nhân CPU (Physical / Logical)", f"{summary.get('cpu_physical_cores', 'N/A')} / {summary.get('cpu_logical_cores', 'N/A')} cores"),
    ]

    ws.append([])
    ws.append(["Chỉ số Đo Lường Hệ Thống", "Giá Trị"])
    for cell in ws[3]:
        cell.fill = SUBHEADER_FILL
        cell.font = Font(bold=True)
    for label, val in kpis:
        ws.append([label, val])

    for r in range(4, ws.max_row + 1):
        ws.cell(r, 1).border = THIN_BORDER
        ws.cell(r, 2).border = THIN_BORDER
        ws.cell(r, 2).alignment = Alignment(horizontal="right", vertical="center")

    file_stats = summary.get("per_file_metrics", [])
    if file_stats:
        ws.append([])
        start_r = ws.max_row + 1
        ws.cell(row=start_r, column=1, value="Tiêu Thụ Tài Nguyên Theo Từng Tệp PDF").font = Font(bold=True, size=11, color="1F4E78")
        f_headers = ["Tệp PDF", "Số Mẫu Đo (s)", "CPU TB (%)", "CPU Đỉnh (%)", "RAM Process TB (MB)", "RAM Process Đỉnh (MB)"]
        ws.append(f_headers)
        h_row = ws.max_row
        for cell in ws[h_row]:
            cell.fill = SUBHEADER_FILL
            cell.font = Font(bold=True)
            cell.border = THIN_BORDER
        for f in file_stats:
            ws.append([
                f.get("file_name"),
                f.get("sample_count"),
                f.get("avg_cpu_percent"),
                f.get("peak_cpu_percent"),
                f.get("avg_proc_ram_mb"),
                f.get("peak_proc_ram_mb"),
            ])
            for col_idx in range(1, 7):
                ws.cell(ws.max_row, col_idx).border = THIN_BORDER

    if samples:
        ws.append([])
        step = max(1, len(samples) // 1000)
        downsampled = samples[::step]
        ts_start_r = ws.max_row + 1
        ws.cell(row=ts_start_r, column=1, value="Nhật Ký Đo Lường Theo Thời Gian (Telemetry Time-Series)").font = Font(bold=True, size=11, color="1F4E78")
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
            ws.add_chart(chart, "J3")

    fit_columns(ws, min_width=12, max_width=45)


def add_standards_sheet(workbook: Workbook) -> None:
    ws = workbook.create_sheet("Tieu chuan do luong")
    headers = ["Nhóm Đo Lường", "Chỉ Số Khoa Học", "Công Cụ Tham Chiếu", "Công Thức / Quy Chuẩn", "Ngưỡng CPU / Yêu Cầu V5-V6"]
    rows = [
        ["Nhận diện ký tự & từ", "CER (Character Error Rate)", "Levenshtein distance (unclamped)", "(S + D + I) / N_ref (không cắt về 1.0)", "Báo cáo chi tiết S, D, I, H"],
        ["Nhận diện ký tự & từ", "WER_ws (Word Error Rate)", "JiWER parity", "(S + D + I) / N_words theo whitespace", "Đồng nhất 100% JiWER"],
        ["Tiếng Việt có dấu", "DER_VN_v1 (Diacritic Error Rate)", "ICDAR Vietnamese Standard", "Mô hình 3 slots: shape, tone, stroke bar 'đ'/'Đ'", "Phạt đúng thay thế/chèn dấu"],
        ["Cấu trúc bảng biểu", "TEDS / TEDS-struct", "IBM PubTabNet (APTED)", "1 - EditDist(T_ref, T_pred) / max(|T_ref|, |T_pred|)", "Tách biệt Table Detection P/R/F1"],
        ["Phân tích bố cục", "COCO mAP@50 & mAP@50:95", "Official COCOeval", "101 recall thresholds, 10 IoU thresholds", "Absent class AP=None"],
        ["Thực thể số liệu", "NumericExact (P, R, F1)", "Location-aware Cell Match", "Match cả giá trị và tọa độ cấu trúc", "Giữ nguyên '(100)', '00123', '12%'"],
        ["Thứ tự đọc", "Reading Order Precedence", "Sequential 1-to-1 tracking", "Pairwise precedence accuracy & Spearman rho", "Ghép 1-1 chống trùng tiền tố"],
        ["Hiệu năng thời gian", "Thời gian trung bình CPU", "End-to-end Wall clock", "Native <= 0.15s, Mixed <= 1.80s, Scan <= 2.50s/trang", "So sánh bằng số thực chưa làm tròn"]
    ]
    append_rows(ws, headers, rows)
    style_table(ws)
    fit_columns(ws, max_width=80)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export OCR batch results to Excel")
    parser.add_argument("--output-dir", default="output", help="Batch output directory")
    parser.add_argument("--excel", default="output/OCR_BENCHMARK_5_FILES.xlsx", help="Excel output path")
    args = parser.parse_args()
    build_workbook(Path(args.output_dir).resolve(), Path(args.excel).resolve())
    print(f"Excel report created: {Path(args.excel).resolve()}")


if __name__ == "__main__":
    main()

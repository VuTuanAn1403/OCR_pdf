"""Excel keyword report adapted from OCR_pdf_backup's three-column layout."""
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from src.ocr.domain.models.keyword import KeywordReport
from src.ocr.runtime.atomic_io import atomic_write_bytes


class KeywordExporter:
    @staticmethod
    def export_excel(report: KeywordReport, output_dir: Path) -> Path:
        workbook = Workbook()
        try:
            sheet = workbook.active
            sheet.title = "Thống kê từ khóa"
            sheet["A1"] = "BÁO CÁO TẦN SUẤT TỪ KHÓA"
            sheet["A1"].font = Font(size=14, bold=True, color="1F4E79")
            sheet["A2"] = (f"Tài liệu: {report.document_id}  |  "
                           f"Tổng số từ khóa tìm kiếm: {report.total_keywords_searched}  |  "
                           f"Tổng số lần xuất hiện: {report.total_matches}")
            for column, title in enumerate(("STT", "Từ khóa / Cụm từ", "Số lần xuất hiện"), 1):
                cell = sheet.cell(4, column, title)
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="1F4E79")
                cell.alignment = Alignment(horizontal="center")
            for index, keyword in enumerate(report.keywords, 1):
                row = index + 4
                sheet.cell(row, 1, index)
                cell = sheet.cell(row, 2, keyword.keyword)
                cell.data_type = "s"  # Keywords such as '=SUM' are literal text.
                sheet.cell(row, 3, keyword.count).number_format = "#,##0"
            total_row = len(report.keywords) + 5
            sheet.cell(total_row, 2, "Tổng cộng").font = Font(bold=True)
            sheet.cell(total_row, 3, report.total_matches).font = Font(bold=True)
            sheet.column_dimensions["A"].width = 8
            sheet.column_dimensions["B"].width = min(80, max(28, max(
                (len(item.keyword) + 6 for item in report.keywords), default=28)))
            sheet.column_dimensions["C"].width = 20
            sheet.freeze_panes = "A5"
            buffer = BytesIO()
            workbook.save(buffer)
            path = Path(output_dir) / "count_keywords.xlsx"
            atomic_write_bytes(path, buffer.getvalue())
            return path
        finally:
            workbook.close()

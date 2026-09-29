from src.ocr.domain.models.table import TableCell
from src.ocr.infrastructure.layout.document_layout_engine import DocumentLayoutEngine


def test_wrapped_description_is_joined_with_numeric_cells():
    cells = [
        TableCell(row_idx=0, col_idx=0, text="Khoản mục"),
        TableCell(row_idx=0, col_idx=1, text="Năm nay"),
        TableCell(row_idx=0, col_idx=2, text="Năm trước"),
        TableCell(row_idx=1, col_idx=0, text="Tổng giá trị các khoản phải thu quá hạn"),
        TableCell(row_idx=1, col_idx=1, text="hoặc chưa quá hạn nhưng khó"),
        TableCell(row_idx=2, col_idx=0, text="có khả năng thu hồi"),
        TableCell(row_idx=2, col_idx=1, text="35.296.305.783"),
        TableCell(row_idx=2, col_idx=2, text="9.912.807.558"),
    ]
    merged = DocumentLayoutEngine._merge_wrapped_numeric_rows(cells)
    assert max(cell.row_idx for cell in merged) == 1
    label = next(cell.text for cell in merged if cell.row_idx == 1 and cell.col_idx == 0)
    assert label == (
        "Tổng giá trị các khoản phải thu quá hạn hoặc chưa quá hạn nhưng khó "
        "có khả năng thu hồi"
    )
    assert {(cell.col_idx, cell.text) for cell in merged if cell.row_idx == 1 and cell.col_idx > 0} == {
        (1, "35.296.305.783"), (2, "9.912.807.558")
    }


def test_rows_with_existing_values_are_not_merged():
    cells = [
        TableCell(row_idx=0, col_idx=0, text="Khoản phải thu khách hàng dài hạn"),
        TableCell(row_idx=0, col_idx=1, text="35.296.305.783"),
        TableCell(row_idx=1, col_idx=0, text="Khoản khác"),
        TableCell(row_idx=1, col_idx=1, text="9.912.807.558"),
        TableCell(row_idx=1, col_idx=2, text="18.492.900.264"),
    ]
    merged = DocumentLayoutEngine._merge_wrapped_numeric_rows(cells)
    assert len(merged) == len(cells)
    assert max(cell.row_idx for cell in merged) == 1


def test_signature_block_is_not_a_financial_table():
    cells = [
        TableCell(row_idx=0, col_idx=0, text="Trần Nam Dũng"),
        TableCell(row_idx=0, col_idx=1, text="Phó Tổng Giám đốc"),
        TableCell(row_idx=1, col_idx=0, text="Kiểm toán viên"),
        TableCell(row_idx=2, col_idx=0, text="Ngày 8 tháng 4 năm"),
        TableCell(row_idx=2, col_idx=1, text="2022"),
    ]
    assert DocumentLayoutEngine._looks_like_signature_block(cells)


def test_real_financial_table_is_not_signature_block():
    cells = [
        TableCell(row_idx=0, col_idx=0, text="Giá gốc"),
        TableCell(row_idx=0, col_idx=1, text="Giá trị có thể thu hồi"),
        TableCell(row_idx=1, col_idx=0, text="Khoản phải thu"),
        TableCell(row_idx=1, col_idx=1, text="35.296.305.783"),
    ]
    assert not DocumentLayoutEngine._looks_like_signature_block(cells)

"""
V4.1 Pure Markdown Pipe Table Unit Tests
Verifies strict adherence to:
- Output strictly as Markdown Pipe Table (| Cột 1 | Cột 2 | ... |)
- Zero HTML table tags (<table>, <tr>, <td>, <th>)
- Zero colspan / rowspan attributes
- Zero HTML tables nested inside cells
- Every row has the exact same number of columns
- Multi-line cell text is preserved using <br> in the same cell
- Escape pipe (|) characters inside cell text
- No false positive tables created from narrative paragraphs
"""

import pytest
import re
from src.ocr.domain.models.table import TableStructure, TableCell


def test_duplicate_coordinates_preserve_all_source_text():
    table = TableStructure(table_id="overlap", page=1, num_rows=2, num_cols=2, cells=[
        TableCell(row_idx=0, col_idx=0, text="Khoản mục"),
        TableCell(row_idx=0, col_idx=1, text="Giá trị"),
        TableCell(row_idx=1, col_idx=0, text="Doanh thu 1.314,7"),
        TableCell(row_idx=1, col_idx=0, text="Lợi nhuận 237,2"),
        TableCell(row_idx=1, col_idx=1, text="2021"),
    ])
    markdown = table.to_pipe_table()
    assert "Doanh thu 1.314,7 Lợi nhuận 237,2" in markdown


def test_user_required_example_exact_match():
    """Verifies exact output format matching user specification."""
    cells = [
        TableCell(row_idx=0, col_idx=0, text="Cột 1"),
        TableCell(row_idx=0, col_idx=1, text="Cột 2"),
        TableCell(row_idx=0, col_idx=2, text="Cột 3"),
        TableCell(row_idx=0, col_idx=3, text="Cột 4"),
        TableCell(row_idx=1, col_idx=0, text="A"),
        TableCell(row_idx=1, col_idx=1, text="B"),
        TableCell(row_idx=1, col_idx=2, text="C"),
        TableCell(row_idx=1, col_idx=3, text="D"),
        TableCell(row_idx=2, col_idx=0, text="E"),
        TableCell(row_idx=2, col_idx=1, text="F"),
        TableCell(row_idx=2, col_idx=2, text="G"),
        TableCell(row_idx=2, col_idx=3, text="H"),
    ]
    table = TableStructure(table_id="spec_test", page=1, num_rows=3, num_cols=4, cells=cells)
    md = table.to_pipe_table()

    expected = (
        "| Cột 1 | Cột 2 | Cột 3 | Cột 4 |\n"
        "| :---- | :---- | :---- | :---- |\n"
        "| A     | B     | C     | D     |\n"
        "| E     | F     | G     | H     |"
    )
    assert md == expected
    assert "<table" not in md
    assert "colspan" not in md
    assert "rowspan" not in md


def test_every_row_same_column_count_with_spans():
    """Verifies every row has the exact same number of columns even with merged spans."""
    cells = [
        TableCell(row_idx=0, col_idx=0, text="Tiêu đề A", col_span=2),
        TableCell(row_idx=0, col_idx=2, text="Tiêu đề B"),
        TableCell(row_idx=1, col_idx=0, text="Dữ liệu 1"),
        TableCell(row_idx=1, col_idx=1, text="Dữ liệu 2"),
        TableCell(row_idx=1, col_idx=2, text="Dữ liệu 3"),
    ]
    table = TableStructure(table_id="span_test", page=1, num_rows=2, num_cols=3, cells=cells)
    md = table.to_pipe_table()

    lines = [l for l in md.split("\n") if l.startswith("|")]
    assert len(lines) == 3  # header, separator, 1 data row
    # Unescaped pipes count: N columns -> N + 1 pipes
    col_counts = [len(re.findall(r"(?<!\\)\|", l)) for l in lines]
    assert len(set(col_counts)) == 1
    assert col_counts[0] == 4  # 3 columns


def test_multiline_cell_preserved_as_inline_text():
    """A GFM row stays on one line and retains its text without HTML breaks."""
    cells = [
        TableCell(row_idx=0, col_idx=0, text="Khoản mục"),
        TableCell(row_idx=0, col_idx=1, text="Ghi chú"),
        TableCell(row_idx=1, col_idx=0, text="Doanh thu bán hàng"),
        TableCell(row_idx=1, col_idx=1, text="Dòng thuyết minh 1\nDòng thuyết minh 2\nDòng thuyết minh 3"),
    ]
    table = TableStructure(table_id="multiline_test", page=1, num_rows=2, num_cols=2, cells=cells)
    md = table.to_pipe_table()

    assert "Dòng thuyết minh 1 Dòng thuyết minh 2 Dòng thuyết minh 3" in md
    assert "<br>" not in md
    assert "<table" not in md
    # Ensure it is on a single physical line in markdown
    lines = md.split("\n")
    data_line = lines[2]
    assert data_line.startswith("| Doanh thu bán hàng | Dòng thuyết minh 1 ")


def test_pipes_in_cell_escaped():
    """Verifies pipe characters in cell text are escaped to prevent breaking markdown columns."""
    cells = [
        TableCell(row_idx=0, col_idx=0, text="Tiêu chí"),
        TableCell(row_idx=0, col_idx=1, text="Lựa chọn"),
        TableCell(row_idx=1, col_idx=0, text="Kích thước | Size"),
        TableCell(row_idx=1, col_idx=1, text="A | B | C"),
    ]
    table = TableStructure(table_id="pipe_test", page=1, num_rows=2, num_cols=2, cells=cells)
    md = table.to_pipe_table()

    assert "Kích thước \\| Size" in md
    assert "A \\| B \\| C" in md
    lines = [l for l in md.split("\n") if l.startswith("|")]
    col_counts = [len(re.findall(r"(?<!\\)\|", l)) for l in lines]
    assert len(set(col_counts)) == 1
    assert col_counts[0] == 3  # 2 columns


def test_no_html_table_tags_even_if_in_input():
    """Verifies that if cell text contains legacy HTML tags, they are stripped and output as pure markdown."""
    cells = [
        TableCell(row_idx=0, col_idx=0, text="Cột 1"),
        TableCell(row_idx=0, col_idx=1, text="Cột 2"),
        TableCell(row_idx=1, col_idx=0, text="<table><tr><td>Nội dung lồng</td></tr></table>"),
        TableCell(row_idx=1, col_idx=1, text="Bình thường"),
    ]
    table = TableStructure(table_id="html_strip_test", page=1, num_rows=2, num_cols=2, cells=cells)
    md = table.to_pipe_table()

    assert "<table" not in md
    assert "<tr" not in md
    assert "<td" not in md
    assert md.startswith("| Cột 1")
    assert "Cột 2" in md.splitlines()[0]


def test_anti_false_positive_single_column_narrative():
    """Verifies single column narrative text is converted to clean paragraphs instead of a table."""
    cells = [
        TableCell(row_idx=0, col_idx=0, text="Báo cáo tình hình hoạt động của Ban Kiểm soát trong năm tài chính."),
        TableCell(row_idx=1, col_idx=0, text="Trong năm qua, Ban Kiểm soát đã phối hợp chặt chẽ với Ban Điều hành."),
    ]
    table = TableStructure(table_id="narrative_test", page=1, num_rows=2, num_cols=1, cells=cells)
    md = table.to_pipe_table()

    assert not md.startswith("|")
    assert "<table" not in md
    assert "Ban Kiểm soát đã phối hợp" in md

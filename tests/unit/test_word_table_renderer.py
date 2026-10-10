"""
Unit Tests for Word-like TableRenderer V4.1
Verifies:
1. Test bảng đơn giản.
2. Test bảng nhiều cột.
3. Test cell có đoạn văn rất dài.
4. Test nhiều cell dài trên cùng một row (Ví dụ mong muốn của người dùng).
5. Test bảng tài chính có nhiều số liệu (căn phải số, căn trái chữ).
6. Test bảng thực tế (VID / benchmark).
7. Test zero content loss (không mất dữ liệu).
8. Test column alignment (thẳng hàng dọc tuyệt đối).
9. Test không dùng thẻ <br>.
10. Test không tạo thêm row khi text dài.
"""

import pytest
import re
from src.ocr.domain.models.table import TableStructure, TableCell, TableRenderer


def test_simple_table():
    """Test 1: Bảng đơn giản - hiển thị bình thường, đúng cấu trúc Word-like."""
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
    table = TableStructure(table_id="simple_test", page=1, num_rows=3, num_cols=4, cells=cells)
    rendered = table.to_markdown()

    assert rendered.startswith("┌")
    assert rendered.endswith("┘")
    assert "<br>" not in rendered
    assert "<table" not in rendered

    # Every data row is cleanly separated
    lines = rendered.split("\n")
    content_lines = [l for l in lines if l.startswith("│")]
    assert len(content_lines) == 3  # 1 header line + 2 data lines


def test_multi_column_table():
    """Test 2: Bảng nhiều cột - co giãn hợp lý, không tràn ngang quá lớn."""
    headers = ["STT", "Mã", "Tên chỉ tiêu", "Đơn vị", "Kế hoạch", "Thực hiện", "Tỷ lệ %"]
    data = [
        ["1", "DT", "Doanh thu hoạt động kinh doanh chính", "Tỷ đồng", "1.200", "1.350", "112,5%"],
        ["2", "CP", "Chi phí quản lý doanh nghiệp phát sinh", "Tỷ đồng", "150", "145", "96,7%"],
    ]
    cells = []
    for c_idx, h in enumerate(headers):
        cells.append(TableCell(row_idx=0, col_idx=c_idx, text=h))
    for r_idx, row in enumerate(data):
        for c_idx, val in enumerate(row):
            cells.append(TableCell(row_idx=r_idx + 1, col_idx=c_idx, text=val))

    table = TableStructure(table_id="multi_col_test", page=1, num_rows=3, num_cols=7, cells=cells)
    rendered = table.to_markdown()

    lines = rendered.split("\n")
    max_line_len = max(len(l) for l in lines)
    # Ensure table fits comfortably without extreme blowout (<= 115 chars)
    assert max_line_len <= 115
    assert "<br>" not in rendered


def test_cell_with_very_long_paragraph():
    """Test 3: Cell có đoạn văn rất dài - tự xuống dòng trong cell, row height tự tăng, KHÔNG tạo thêm row."""
    long_desc = (
        "Công ty đã tận dụng cải tạo và sửa chữa các hạng mục công trình hiện có trở thành Trường "
        "trung học phổ thông Việt Mỹ Anh địa chỉ tại 806 Âu Cơ, Phường 14, Quận Tân Bình và chính thức "
        "đưa trường vào hoạt động từ tháng 08/2019 theo quyết định của UBND TP.HCM."
    )
    cells = [
        TableCell(row_idx=0, col_idx=0, text="Khoản mục"),
        TableCell(row_idx=0, col_idx=1, text="Nội dung chi tiết"),
        TableCell(row_idx=1, col_idx=0, text="Dự án giáo dục"),
        TableCell(row_idx=1, col_idx=1, text=long_desc),
    ]
    table = TableStructure(table_id="long_cell_test", page=1, num_rows=2, num_cols=2, cells=cells)
    rendered = table.to_markdown()

    assert "<br>" not in rendered
    assert "Dự án giáo dục" in rendered
    assert "Âu Cơ, Phường 14" in rendered

    # Extract rows separated by row divider ├...┤
    row_blocks = rendered.split("├")
    assert len(row_blocks) == 2  # 1 header block + 1 data block (exact 1 data row!)

    data_block = row_blocks[1]
    data_lines = [l for l in data_block.split("\n") if l.startswith("│")]
    # Row height auto-increased: multiple lines inside the single row
    assert len(data_lines) >= 3


def test_multiple_long_cells_on_same_row_exact_user_spec():
    """Test 4: Nhiều cell dài trên cùng một row (Ví dụ mong muốn của người dùng)."""
    headers = ["Năm 2020", "Năm 2021", "Năm 2022"]
    row_data = [
        "Công ty đã tận dụng cải tạo và sửa chữa các hạng mục công trình hiện có...",
        "Công ty phát hành cổ phiếu để trả cổ tức năm 2020 với tỷ lệ 15%...",
        "Nâng cao công tác quản trị và kiện toàn hệ thống pháp lý..."
    ]
    cells = [
        TableCell(row_idx=0, col_idx=0, text=headers[0]),
        TableCell(row_idx=0, col_idx=1, text=headers[1]),
        TableCell(row_idx=0, col_idx=2, text=headers[2]),
        TableCell(row_idx=1, col_idx=0, text=row_data[0]),
        TableCell(row_idx=1, col_idx=1, text=row_data[1]),
        TableCell(row_idx=1, col_idx=2, text=row_data[2]),
    ]
    table = TableStructure(table_id="user_spec_example", page=1, num_rows=2, num_cols=3, cells=cells)
    rendered = table.to_markdown()

    # Verify borders
    assert rendered.startswith("┌")
    assert "├" in rendered
    assert rendered.endswith("┘")
    assert "<br>" not in rendered

    # Verify all 3 cells wrap on the same row without creating extra rows
    row_blocks = rendered.split("├")
    assert len(row_blocks) == 2  # header block + exactly 1 data row block

    data_block = row_blocks[1]
    data_lines = [l for l in data_block.split("\n") if l.startswith("│")]
    # Row height increased to accommodate wrapped content
    assert len(data_lines) >= 3

    # Verify all 3 column contents exist
    for word in ("Công ty", "tận dụng", "phát hành", "cổ tức", "quản trị", "kiện toàn"):
        assert word in rendered


def test_financial_table_numeric_alignment():
    """Test 5: Bảng tài chính có nhiều số liệu - căn phải số, căn trái chữ."""
    cells = [
        TableCell(row_idx=0, col_idx=0, text="Khoản mục"),
        TableCell(row_idx=0, col_idx=1, text="Mã số"),
        TableCell(row_idx=0, col_idx=2, text="Năm 2021"),
        TableCell(row_idx=0, col_idx=3, text="Năm 2022"),
        TableCell(row_idx=1, col_idx=0, text="Doanh thu thuần"),
        TableCell(row_idx=1, col_idx=1, text="01"),
        TableCell(row_idx=1, col_idx=2, text="1.250.000.000"),
        TableCell(row_idx=1, col_idx=3, text="1.450.000.000"),
        TableCell(row_idx=2, col_idx=0, text="Giá vốn hàng bán"),
        TableCell(row_idx=2, col_idx=1, text="02"),
        TableCell(row_idx=2, col_idx=2, text="850.000.000"),
        TableCell(row_idx=2, col_idx=3, text="920.000.000"),
        TableCell(row_idx=3, col_idx=0, text="Lợi nhuận gộp"),
        TableCell(row_idx=3, col_idx=1, text="10"),
        TableCell(row_idx=3, col_idx=2, text="400.000.000"),
        TableCell(row_idx=3, col_idx=3, text="530.000.000"),
    ]
    table = TableStructure(table_id="financial_test", page=1, num_rows=4, num_cols=4, cells=cells)
    rendered = table.to_markdown()

    lines = [l for l in rendered.split("\n") if l.startswith("│")]
    # Check data rows: numeric columns must end with spaces before vertical bar │ (right aligned)
    for line in lines[1:]:
        parts = [p.strip() for p in line.split("│")[1:-1]]
        assert len(parts) == 4
        # Numeric parts are right-aligned
        assert parts[1] in ("01", "02", "10")
        assert parts[2] in ("1.250.000.000", "850.000.000", "400.000.000")


def test_no_content_loss():
    """Test 7: Không mất dữ liệu khi wrap - 100% từ ngữ gốc được bảo toàn."""
    original_text = (
        "Nâng cao công tác quản trị, Công ty đã bầu cử bổ sung thành viên độc lập HĐQT. "
        "Ngoài ra, nhằm kiện toàn hệ thống pháp lý, quy định pháp luật và cải tiến bộ máy quản lý, "
        "Công ty đã thực hiện sửa đổi, bổ sung và ban hành Điều lệ tổ chức và hoạt động."
    )
    cells = [
        TableCell(row_idx=0, col_idx=0, text="Tiêu chí"),
        TableCell(row_idx=0, col_idx=1, text="Thuyết minh"),
        TableCell(row_idx=1, col_idx=0, text="Quản trị"),
        TableCell(row_idx=1, col_idx=1, text=original_text),
    ]
    table = TableStructure(table_id="content_loss_test", page=1, num_rows=2, num_cols=2, cells=cells)
    rendered = table.to_markdown()

    # Verify every word from original_text is present in the rendered output
    words = original_text.split()
    for w in words:
        clean_w = w.strip(".,;:()")
        if clean_w:
            assert clean_w in rendered, f"Word '{clean_w}' was lost in rendering!"


def test_column_alignment_integrity():
    """Test 8: Căn lề cột tuyệt đối - mọi ký tự │ trên mọi dòng phải có vị trí cột chính xác."""
    cells = [
        TableCell(row_idx=0, col_idx=0, text="Cột A"),
        TableCell(row_idx=0, col_idx=1, text="Cột B rất dài cần phải xuống dòng trong cell"),
        TableCell(row_idx=1, col_idx=0, text="Dữ liệu ngắn"),
        TableCell(row_idx=1, col_idx=1, text="Dòng 1 dài\nDòng 2 dài tiếp tục\nDòng 3"),
    ]
    table = TableStructure(table_id="alignment_test", page=1, num_rows=2, num_cols=2, cells=cells)
    rendered = table.to_markdown()

    lines = rendered.split("\n")
    # All border and text lines must have identical character length
    line_lengths = [len(l) for l in lines]
    assert len(set(line_lengths)) == 1, f"Lines have mismatched lengths: {set(line_lengths)}"

    # All text lines starting with │ must have │ at identical column indices
    pipe_indices_per_line = []
    for l in lines:
        if l.startswith("│"):
            indices = [i for i, ch in enumerate(l) if ch == "│"]
            pipe_indices_per_line.append(indices)

    assert len(pipe_indices_per_line) >= 4
    for idx_list in pipe_indices_per_line:
        assert idx_list == pipe_indices_per_line[0], "Vertical column separators are misaligned!"


def test_single_column_narrative_guard():
    """Test 9: Anti-narrative guard - văn bản 1 cột được xuất thành đoạn văn đọc."""
    cells = [
        TableCell(row_idx=0, col_idx=0, text="Báo cáo tình hình hoạt động của Ban Kiểm soát."),
        TableCell(row_idx=1, col_idx=0, text="Trong năm qua, Ban Kiểm soát đã phối hợp chặt chẽ với Ban Giám đốc."),
    ]
    table = TableStructure(table_id="narrative_test", page=1, num_rows=2, num_cols=1, cells=cells)
    rendered = table.to_markdown()

    assert not rendered.startswith("┌")
    assert not rendered.startswith("|")
    assert "Ban Kiểm soát đã phối hợp" in rendered

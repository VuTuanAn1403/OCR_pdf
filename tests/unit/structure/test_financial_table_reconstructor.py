"""
Unit tests for FinancialTableReconstructor.
Tests multi-column borderless tables, multi-level spanning headers,
right-aligned numeric clustering, and multi-line text wrapping on BCTC.
"""
from ocr_contracts.text_block import TextBlock
from ocr_structure.financial_table_reconstructor import FinancialTableReconstructor


def test_reconstruct_financial_table_lam_son_bctc():
    """
    Simulates the exact layout of Lam Son Sugar BCTC (page 102 - Table 2):
    - Sub-section: 'b) Đầu tư góp vốn vào đơn vị khác'
    - Multi-level header:
        Row 0: 30/06/2025 (spanning 2 cols) | 01/07/2024 (spanning 2 cols)
        Row 1: Giá gốc | Dự phòng | Giá gốc | Dự phòng
        Row 2: VND | VND | VND | VND
    - Multi-line wrapped entity:
        '- Công ty Cổ phần Đầu tư Phát triển'
        'Lam Sơn - Như Xuân' with '1.000.000.000' and '(415.591.564)'
    - Total row: 'Cộng' with '6.251.145.800' and '(415.591.564)'
    """
    reconstructor = FinancialTableReconstructor()

    # Coordinates roughly matching a standard 595x842 page
    # Col 0 (Description): 50 - 280
    # Col 1 (30/06/2025 - Giá gốc): 300 - 370 (right anchor: 370)
    # Col 2 (30/06/2025 - Dự phòng): 385 - 445 (right anchor: 445)
    # Col 3 (01/07/2024 - Giá gốc): 460 - 530 (right anchor: 530)
    # Col 4 (01/07/2024 - Dự phòng): 545 - 605 (right anchor: 605)

    blocks = [
        # Sub-heading
        TextBlock(text="b) Đầu tư góp vốn vào đơn vị khác", bbox=[50, 460, 200, 475], page=1),

        # Header Row 0: Dates spanning multiple columns
        TextBlock(text="30/06/2025", bbox=[320, 490, 425, 502], page=1),
        TextBlock(text="01/07/2024", bbox=[480, 490, 585, 502], page=1),

        # Header Row 1: Sub-headers
        TextBlock(text="Giá gốc", bbox=[320, 510, 360, 522], page=1),
        TextBlock(text="Dự phòng", bbox=[395, 510, 440, 522], page=1),
        TextBlock(text="Giá gốc", bbox=[480, 510, 520, 522], page=1),
        TextBlock(text="Dự phòng", bbox=[555, 510, 600, 522], page=1),

        # Header Row 2: Units
        TextBlock(text="VND", bbox=[335, 530, 355, 540], page=1),
        TextBlock(text="VND", bbox=[410, 530, 430, 540], page=1),
        TextBlock(text="VND", bbox=[495, 530, 515, 540], page=1),
        TextBlock(text="VND", bbox=[570, 530, 590, 540], page=1),

        # Data Row 1: Mia duong I
        TextBlock(text="- Tổng Công ty Mía đường I - Công ty Cổ phần", bbox=[50, 570, 275, 582], page=1),
        TextBlock(text="2.974.645.800", bbox=[300, 570, 370, 582], page=1),
        TextBlock(text="-", bbox=[420, 570, 428, 582], page=1),
        TextBlock(text="2.974.645.800", bbox=[460, 570, 530, 582], page=1),
        TextBlock(text="-", bbox=[580, 570, 588, 582], page=1),

        # Data Row 2: Lam Ha
        TextBlock(text="- Công ty Cổ phần Thương mại Lam Hà", bbox=[50, 595, 250, 607], page=1),
        TextBlock(text="2.276.500.000", bbox=[300, 595, 370, 607], page=1),
        TextBlock(text="-", bbox=[420, 595, 428, 607], page=1),
        TextBlock(text="2.276.500.000", bbox=[460, 595, 530, 607], page=1),
        TextBlock(text="-", bbox=[580, 595, 588, 607], page=1),

        # Data Row 3: Lam Son - Nhu Xuan (Wrapped across 2 lines!)
        TextBlock(text="- Công ty Cổ phần Đầu tư Phát triển", bbox=[50, 620, 245, 632], page=1),
        TextBlock(text="Lam Sơn - Như Xuân", bbox=[60, 635, 175, 647], page=1),
        TextBlock(text="1.000.000.000", bbox=[300, 635, 370, 647], page=1),
        TextBlock(text="(415.591.564)", bbox=[380, 635, 445, 647], page=1),
        TextBlock(text="1.000.000.000", bbox=[460, 635, 530, 647], page=1),
        TextBlock(text="(415.591.564)", bbox=[540, 635, 605, 647], page=1),

        # Data Row 4: Total Row (Cộng)
        TextBlock(text="Cộng", bbox=[50, 665, 80, 677], page=1),
        TextBlock(text="6.251.145.800", bbox=[300, 665, 370, 677], page=1),
        TextBlock(text="(415.591.564)", bbox=[380, 665, 445, 677], page=1),
        TextBlock(text="6.251.145.800", bbox=[460, 665, 530, 677], page=1),
        TextBlock(text="(415.591.564)", bbox=[540, 665, 605, 677], page=1),
    ]

    tables = reconstructor.reconstruct_tables(blocks, page_width=650, page_height=850, page_num=102)

    assert len(tables) == 1
    table = tables[0]

    # Verify column count: 1 description col + 4 numeric cols = 5 cols
    assert table.num_cols == 5
    assert table.engine == "deterministic_geometry"

    # Verify Spanning Header (Row 0):
    # '30/06/2025' spans col 1-2 (colspan 2)
    # '01/07/2024' spans col 3-4 (colspan 2)
    span_headers = [c for c in table.cells if c.column_header]
    assert any("30/06/2025" in c.text and c.col_span >= 2 for c in span_headers)
    assert any("01/07/2024" in c.text and c.col_span >= 2 for c in span_headers)

    # Verify Multi-line Text Wrapping:
    # Row 3 (Lam Son - Nhu Xuan) MUST be merged into 1 single cell in col 0!
    lam_son_cell = next(
        c for c in table.cells
        if c.col_idx == 0 and "Lam Sơn - Như Xuân" in c.text
    )
    assert "- Công ty Cổ phần Đầu tư Phát triển Lam Sơn - Như Xuân" in lam_son_cell.text

    # The same row must contain the exact aligned numbers in col 1..4:
    lam_son_row_idx = lam_son_cell.row_idx
    row_values = {
        c.col_idx: c.text for c in table.cells
        if c.row_idx == lam_son_row_idx
    }
    assert row_values[1] == "1.000.000.000"
    assert row_values[2] == "(415.591.564)"
    assert row_values[3] == "1.000.000.000"
    assert row_values[4] == "(415.591.564)"

    # Verify Total Row
    total_cells = [c for c in table.cells if "Cộng" in c.text or c.row_section]
    assert len(total_cells) >= 1
    total_row_idx = total_cells[0].row_idx
    total_values = {
        c.col_idx: c.text for c in table.cells
        if c.row_idx == total_row_idx
    }
    assert total_values[1] == "6.251.145.800"
    assert total_values[2] == "(415.591.564)"

    # Verify Markdown export produces structured output without losing numbers or text
    md = table.to_markdown(format="pipe")
    assert "2.974.645.800" in md
    assert "(415.591.564)" in md
    assert "Lam Sơn - Như Xuân" in md


def test_reconstruct_financial_table_with_attribute_text_columns():
    """
    Tests Table 3 from Lam Son Sugar BCTC (Attribute text columns):
    Col 0: Tên công ty nhận đầu tư
    Col 1: Nơi thành lập và hoạt động (Hà Nội, Thanh Hóa)
    Col 2: Tỷ lệ lợi ích (0,68%, 18,97%, 2,00%)
    Col 3: Tỷ lệ quyền biểu quyết (0,68%, 18,97%, 2,00%)
    """
    reconstructor = FinancialTableReconstructor()

    blocks = [
        # Headers
        TextBlock(text="Tên công ty nhận đầu tư", bbox=[50, 100, 200, 115], page=1),
        TextBlock(text="Nơi thành lập và hoạt động", bbox=[220, 100, 310, 115], page=1),
        TextBlock(text="Tỷ lệ lợi ích", bbox=[330, 100, 380, 115], page=1),
        TextBlock(text="Tỷ lệ quyền biểu quyết", bbox=[400, 100, 470, 115], page=1),

        # Row 1
        TextBlock(text="Tổng Công ty Mía đường I - Công ty Cổ phần", bbox=[50, 130, 210, 142], page=1),
        TextBlock(text="Hà Nội", bbox=[230, 130, 270, 142], page=1),
        TextBlock(text="0,68%", bbox=[340, 130, 375, 142], page=1),
        TextBlock(text="0,68%", bbox=[410, 130, 445, 142], page=1),

        # Row 2
        TextBlock(text="Công ty Cổ phần Thương mại Lam Hà", bbox=[50, 150, 210, 162], page=1),
        TextBlock(text="Hà Nội", bbox=[230, 150, 270, 162], page=1),
        TextBlock(text="18,97%", bbox=[340, 150, 375, 162], page=1),
        TextBlock(text="18,97%", bbox=[410, 150, 445, 162], page=1),

        # Row 3
        TextBlock(text="Công ty Cổ phần Đầu tư Phát triển Lam Sơn - Như Xuân", bbox=[50, 170, 215, 182], page=1),
        TextBlock(text="Thanh Hóa", bbox=[230, 170, 280, 182], page=1),
        TextBlock(text="2,00%", bbox=[340, 170, 375, 182], page=1),
        TextBlock(text="2,00%", bbox=[410, 170, 445, 182], page=1),
    ]

    tables = reconstructor.reconstruct_tables(blocks, page_width=500, page_height=400, page_num=1)
    assert len(tables) == 1
    table = tables[0]

    assert table.num_cols == 4
    # Check that attribute col (Nơi thành lập) is preserved in col 1
    col1_texts = [c.text for c in table.cells if c.col_idx == 1 and not c.column_header]
    assert "Hà Nội" in col1_texts
    assert "Thanh Hóa" in col1_texts

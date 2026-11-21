from types import SimpleNamespace

from ocr_contracts.geometry import LINE_ORIENTATION_METHOD
from ocr_contracts.text_block import TextBlock
from ocr_structure.document_layout_engine import DocumentLayoutEngine


def test_wide_spread_keeps_upright_hypotheses_year_headers_and_opening_totals():
    blocks = []
    rows = [('Chỉ tiêu', '2024', '2023'),
            ('TỔNG CỘNG TÀI SẢN', '3.420.741', '2.120.531'),
            ('Tài sản ngắn hạn', '1.100.101', '1.200.201'),
            ('Tiền và tương đương tiền', '400.123', '300.456')]
    for r, values in enumerate(rows):
        for c, text in enumerate(values):
            x = (40, 320, 450)[c]
            blocks.append(TextBlock(text=text, text_raw=text, source='primary_ocr', source_id=f'r{r}c{c}',
                bbox=[x, 100+30*r, x+(230 if c == 0 else 90), 114+30*r], page=1,
                recognition_provenance=dict(method=LINE_ORIENTATION_METHOD,
                    rotation_clockwise_degrees=0, candidates=[dict(rotation_clockwise_degrees=180)])))
    footer = TextBlock(text='34 example.org', bbox=[40, 815, 180, 828], page=1, source_id='folio')
    blocks.append(footer)
    page = SimpleNamespace(rect=SimpleNamespace(width=1200, height=842))
    tables = DocumentLayoutEngine().extract_tables(page, blocks, 1, allow_vector_tables=False)
    assert len(tables) == 1
    table = tables[0]
    assert table.engine == 'deterministic_geometry' and table.num_cols == 3
    assert any(c.column_header and c.text == '2024' for c in table.cells)
    assert any(c.text == 'TỔNG CỘNG TÀI SẢN' and not c.column_header for c in table.cells)
    assert any(c.text == '3.420.741' for c in table.cells)
    assert table.provenance['source_coverage'] == 'COMPLETE'
    assert all('example.org' not in c.text for c in table.cells)
    assert footer.text == '34 example.org'

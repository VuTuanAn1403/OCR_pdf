"""Counterexamples and source-preservation checks for financial geometry integration."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from ocr_contracts.page_type import PageType
from ocr_contracts.region import BoundingBox, ContentType, Region
from ocr_contracts.table import TableCell, TableStructure
from ocr_contracts.text_block import TextBlock
from ocr_structure.document_layout_engine import DocumentLayoutEngine
from ocr_structure.enrichment import enrich_structures
from ocr_structure.financial_table_reconstructor import FinancialTableReconstructor, is_total_line


def financial_blocks():
    rows = [
        (10, [(10, 100, "Chỉ tiêu"), (150, 190, "Giá gốc"), (230, 275, "Dự phòng")]),
        (30, [(10, 100, "Công ty A"), (140, 190, "1.234.000"), (220, 275, "(123.000)")]),
        (50, [(10, 100, "Công ty B"), (140, 190, "2.345.000"), (220, 275, "(234.000)")]),
    ]
    return [TextBlock(text=text, text_raw=text, bbox=[x0, y, x1, y + 10], page=1,
                      source_id=f"source-{index}")
            for index, (x0, y, x1, text) in enumerate(
                (x0, y, x1, text) for y, entries in rows for x0, x1, text in entries)]


def reconstruct(blocks):
    return FinancialTableReconstructor().reconstruct_tables(blocks, 800, 200, 1)


def test_geometry_preserves_raw_sources_and_does_not_invent_confidence():
    blocks = financial_blocks()
    blocks[3].text_raw = "Cong ty A"
    before = [b.model_dump() for b in blocks]
    table, = reconstruct(blocks)
    assert [b.model_dump() for b in blocks] == before
    assert table.confidence is None and table.confidence_source == "unavailable"
    assert table.needs_review and all(c.confidence is None for c in table.cells)
    assert table.provenance["source_coverage"] == "COMPLETE"
    assert table.provenance["numeric_guard"] == "PRESERVED"
    assert {s["source_id"] for s in table.provenance["source_blocks"]} == {b.source_id for b in blocks}
    assert next(s for s in table.provenance["source_blocks"] if s["source_id"] == "source-3")["text_raw"] == "Cong ty A"
    assert [c.text for c in table.cells if c.col_idx == 2 and not c.column_header] == ["(123.000)", "(234.000)"]


@pytest.mark.parametrize("shift", [0, 400])
def test_columns_do_not_depend_on_absolute_page_origin(shift):
    blocks = financial_blocks()
    for block in blocks:
        block.bbox[0] += shift
        block.bbox[2] += shift
    table, = reconstruct(blocks)
    assert table.num_cols == 3 and table.num_rows == 3
    assert "2.345.000" in table.to_pipe_table()


@pytest.mark.parametrize("kind", ["native", "oriented", "bad-box", "one-column", "page-numbers", "duplicate-id", "straddling"])
def test_unsupported_or_ambiguous_sources_do_not_become_geometric_tables(kind):
    blocks = financial_blocks()
    if kind == "native":
        for block in blocks:
            block.source = "native"
    elif kind == "oriented":
        for block in blocks:
            block.recognition_provenance = {"method": "line-orientation-v2", "rotation_clockwise_degrees": 180}
    elif kind == "bad-box":
        for block in blocks:
            block.bbox = [1, 1, 1, 1]
    elif kind == "one-column":
        blocks = [b for index, b in enumerate(blocks) if index % 3 != 2]
    elif kind == "page-numbers":
        for index in (4, 5, 7, 8):
            blocks[index].text = "100"
    elif kind == "duplicate-id":
        blocks[4].source_id = blocks[7].source_id
    elif kind == "straddling":
        blocks[4].bbox[2] = 270
    before = [b.model_dump() for b in blocks]
    assert reconstruct(blocks) == []
    assert [b.model_dump() for b in blocks] == before


def test_unaccented_headers_are_matched_without_editing_source_text():
    blocks = financial_blocks()
    for block, text in zip(blocks, ("Chi tieu", "Gia goc", "Du phong")):
        block.text = text
    table, = reconstruct(blocks)
    assert [c.text for c in table.cells if c.column_header] == ["Chi tieu", "Gia goc", "Du phong"]


def test_long_bulleted_entity_and_numbers_on_continuation_form_one_row():
    blocks = financial_blocks()
    for block in blocks[3:]:
        block.bbox[1] += 20
        block.bbox[3] += 20
    blocks[3].text = "- Công ty Cổ phần Đầu tư Phát triển Thương mại Dịch vụ"
    blocks[3].bbox[1:4:2] = [30, 40]
    blocks.append(TextBlock(text="Lam Sơn - Như Xuân", bbox=[10, 50, 100, 60], page=1, source_id="wrapped"))
    table, = reconstruct(blocks)
    cell, = [c for c in table.cells if "Lam Sơn" in c.text]
    assert "Thương mại Dịch vụ Lam Sơn - Như Xuân" in cell.text
    assert next(c for c in table.cells if c.row_idx == cell.row_idx and c.col_idx == 1).text == "1.234.000"


def test_financial_geometry_is_used_before_image_region_fallback():
    blocks = financial_blocks()
    page = SimpleNamespace(rect=SimpleNamespace(width=300, height=100))
    region = Region(region_id="table", bbox=BoundingBox(x0=0, y0=0, x1=300, y1=100),
                    content_type=ContentType.IMAGE_TABLE)
    table, = DocumentLayoutEngine().extract_tables(page, blocks, 1, allow_vector_tables=False, regions=[region])
    assert table.engine == "deterministic_geometry" and table.needs_review


@pytest.mark.parametrize("model_kind", ["same-region", "unrelated", "spanning", "unverified-geometry"])
def test_docling_protects_only_source_checked_geometry_for_the_same_region(model_kind):
    blocks = financial_blocks()
    table, = reconstruct(blocks)
    box = list(table.bbox)
    if model_kind == "unrelated":
        box = [400, 10, 600, 60]
    elif model_kind == "spanning":
        box = [0, 0, 800, 200]
    elif model_kind == "unverified-geometry":
        table.provenance.pop("numeric_guard")
    model = TableStructure(table_id="model", page=1, bbox=box, num_rows=1, num_cols=1,
                           cells=[TableCell(row_idx=0, col_idx=0, text="9.999", bbox=box)],
                           engine="docling_tableformer", needs_review=True)
    extractor = SimpleNamespace(enabled=True, options={"page_selection": "all"}, cache_fingerprint=lambda: "test",
                                extract_page=lambda *args: {"tables": [model], "formulas": [], "metadata": {"status": "success"}})
    meta = {"width": 800, "height": 200}
    before = deepcopy([b.model_dump() for b in blocks])
    _, result, _ = enrich_structures(extractor, None, 1, PageType.MIXED, blocks, [table], [], meta)
    if model_kind == "same-region":
        assert result == [table] and meta["docling_structure"]["preserved_geometric_tables"] == [table.table_id]
    elif model_kind == "unrelated":
        assert result == [table, model]
    else:
        assert result == [model]
    assert [b.model_dump() for b in blocks] == before


def test_company_is_not_a_total_and_a_formatted_amount_is_not_a_section():
    from ocr_structure.financial_table_reconstructor import SECTION_TITLE_RE, strip_accents
    assert is_total_line("Cộng")
    assert not is_total_line("Tổng Công ty Mía đường I")
    assert SECTION_TITLE_RE.match(strip_accents("2. Thông tin"))
    assert not SECTION_TITLE_RE.match("2.276.500.000")


def test_mixed_table_keeps_the_trailing_business_activity_column():
    header = [(10, 100, "Tên công ty"), (130, 190, "Nơi thành lập"), (230, 270, "Tỷ lệ lợi ích"),
              (300, 340, "Tỷ lệ quyền biểu quyết"), (380, 550, "Hoạt động kinh doanh chính")]
    blocks = [TextBlock(text=text, bbox=[x0, 10, x1, 20], page=1) for x0, x1, text in header]
    for y, company, percent in ((30, "Công ty A", "12,5%"), (50, "Công ty B", "18,9%")):
        for (x0, x1, _), text in zip(header, (company, "Hà Nội", percent, percent, "Sản xuất mía đường")):
            blocks.append(TextBlock(text=text, bbox=[x0, y, x1, y + 10], page=1))
    table, = reconstruct(blocks)
    assert table.num_cols == 5
    assert [c.text for c in table.cells if c.col_idx == 4 and not c.column_header] == ["Sản xuất mía đường"] * 2


def test_repeated_headers_separate_two_financial_tables_on_one_page():
    first, second = financial_blocks(), financial_blocks()
    for block in second:
        block.bbox[1] += 100
        block.bbox[3] += 100
        block.source_id += "-second"
    tables = reconstruct(first + second)
    assert len(tables) == 2
    assert all(t.num_rows == 3 and t.num_cols == 3 for t in tables)

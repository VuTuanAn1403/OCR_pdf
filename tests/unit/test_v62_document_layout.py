import itertools
import json
import random

import pytest

from src.ocr.application.resolve_document_layout import resolve_document_layout
from src.ocr.domain.models.document import ExtractedDocument
from src.ocr.domain.models.page import ExtractedPage
from src.ocr.domain.models.quality import DocumentQualityReport, PageQualityReport
from src.ocr.domain.models.table import TableStructure, TableCell
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.routing.page_type import PageType
from src.ocr.infrastructure.export.document_contracts import build_extraction_json, render_document_md
from src.ocr.infrastructure.export.markdown_exporter import MarkdownExporter
from src.ocr.infrastructure.layout.region_tree import LayoutItem, infer_region_tree
from src.ocr.infrastructure.postprocessing.document_furniture import DocumentFurnitureResolver
from src.ocr.infrastructure.postprocessing.reading_order import ReadingOrderSorter
from src.ocr.infrastructure.postprocessing.reading_order_graph import Edge, solve_order
from src.ocr.infrastructure.structure.ids import document_items, page_items
from src.ocr.infrastructure.structure.section_tree import build_structure
from src.ocr.infrastructure.structure.source_identity import assign_source_ids
from src.ocr.infrastructure.structure.source_identity import lock_text_evidence
from src.ocr.infrastructure.export.contract_schema import validate_extraction, validate_contract_set
from src.ocr.infrastructure.export.document_contracts import build_document_json, build_structure_json


def block(text, box, page=1, **kwargs):
    return TextBlock(text=text, bbox=box, page=page, **kwargs)


def document(pages):
    result = []
    for n, blocks in enumerate(pages, 1):
        result.append(ExtractedPage(page_num=n, width=600, height=842, blocks=blocks,
                                   page_type=PageType.NATIVE_TEXT,
                                   quality=PageQualityReport(page=n, page_type="NATIVE_TEXT")))
    return ExtractedDocument(document_id="test", file_hash="0123456789abcdef", file_path="test.pdf",
                             total_pages=len(result), pages=result,
                             quality=DocumentQualityReport(document_id="test", total_pages=len(result)))


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6, 8])
def test_columns_with_misaligned_baselines_and_narrow_gutters(count):
    nodes = [LayoutItem(f"c{c}-{r}", [c * 70, 100 + r * 18 + c % 3,
                                     c * 70 + 60, 110 + r * 18 + c % 3])
             for c in range(count) for r in range(4)]
    expected = [node.id for node in nodes]
    random.Random(4).shuffle(nodes)
    result = infer_region_tree(nodes)
    assert result.ordered_ids == expected
    assert len(set(result.ordered_ids)) == len(nodes)


def test_nested_partial_heading_and_horizontal_change_in_columns():
    nodes = [LayoutItem("heading", [0, 50, 130, 65], "Article A", "title"),
             LayoutItem("a1", [0, 80, 60, 100]), LayoutItem("a2", [70, 80, 130, 100]),
             LayoutItem("b1", [150, 50, 210, 70]), LayoutItem("b2", [150, 80, 210, 100]),
             LayoutItem("bottom-title", [0, 160, 210, 175], "Next band", "title"),
             LayoutItem("d1", [0, 190, 95, 210]), LayoutItem("d2", [115, 190, 210, 210])]
    assert infer_region_tree(nodes).ordered_ids == ["heading", "a1", "a2", "b1", "b2", "bottom-title", "d1", "d2"]


def test_form_is_read_in_rows_instead_of_prose_columns():
    nodes = [LayoutItem("label1", [0, 100, 100, 110], "Mã cổ phiếu"),
             LayoutItem("value1", [400, 100, 450, 110], "ABC"),
             LayoutItem("label2", [0, 118, 100, 128], "Vốn điều lệ"),
             LayoutItem("value2", [400, 118, 450, 128], "230 tỷ")]
    assert infer_region_tree(nodes).ordered_ids == ["label1", "value1", "label2", "value2"]


def test_identity_permutations_raw_hash_ties_and_ambiguity():
    records = [{"ref": "a", "kind": "b", "text_raw": " raw ", "bbox": [0, 10, 20, 30]},
               {"ref": "b", "kind": "b", "text_raw": "raw", "bbox": [0, 10.01, 20, 30.01]},
               {"ref": "c", "kind": "b", "text_raw": "loose", "bbox": []}]
    identities = [assign_source_ids(list(p), "0123456789abcdef", "p0001") for p in itertools.permutations(records)]
    assert all(r == identities[0] for r in identities)
    assert identities[0]["items"]["c"]["review_reason_codes"] == ["UNPLACED"]
    duplicate = {**records[0], "ref": "d"}
    ambiguous = assign_source_ids(records + [duplicate], "0123456789abcdef", "p0001")
    assert not ambiguous["stable"]
    assert len({i["source_id"] for i in ambiguous["items"].values()}) == 4


def test_sorting_does_not_reclassify_margin_body():
    b = block("TỔNG QUAN DOANH NGHIỆP", [40, 20, 500, 40], page=2)
    ReadingOrderSorter(classify_furniture=False).sort_blocks([b])
    assert b.block_type == "paragraph"


def test_table_unit_continuation_and_section_are_never_suppressed():
    doc = document([[block("Đơn vị tính: triệu đồng", [40, 10, 400, 25], n),
                     block("Bảng 1 (tiếp theo)", [40, 30, 400, 45], n, block_type="header"),
                     block("1. TỔNG QUAN", [40, 50, 400, 65], n)] for n in range(1, 5)])
    resolve_document_layout(doc)
    for page in doc.pages:
        assert all(b.visibility == "visible" for b in page.blocks)
        assert page.blocks[0].protected_from_furniture
        assert page.blocks[1].protected_from_furniture


def test_header_outside_old_margin_and_years_and_short_context():
    doc = document([[block("BÁO CÁO THƯỜNG NIÊN 2025", [40, 95, 400, 108], n)] for n in range(1, 4)])
    resolve_document_layout(doc)
    assert all(p.blocks[0].block_type == "running_header" for p in doc.pages)
    doc.pages = doc.pages[:2]
    resolve_document_layout(doc)
    assert all(p.blocks[0].visibility == "visible" for p in doc.pages)
    assert doc.manifest["furniture"]["context_scope"] == "selected_pages"
    varied = document([[block(f"BÁO CÁO THƯỜNG NIÊN {2024+n}", [40, 95, 400, 108], n)] for n in range(1, 4)])
    DocumentFurnitureResolver().resolve(varied)
    assert all(p.blocks[0].visibility == "visible" for p in varied.pages)


def test_two_subpages_on_one_physical_sheet_are_not_distinct_support():
    doc = document([[block("Repeated report", [40, 20, 200, 35], n)] for n in range(1, 4)])
    for page in doc.pages:
        page.metadata["physical_page_id"] = "sheet-1"
    resolve_document_layout(doc)
    assert all(p.blocks[0].visibility == "visible" for p in doc.pages)


def test_all_exporters_share_atomic_table_flow_and_sources():
    doc = document([[block("Left upper", [40, 100, 260, 120]), block("Left lower", [40, 200, 260, 220]),
                     block("Right upper", [320, 100, 550, 120])]])
    page = doc.pages[0]
    page.tables = [TableStructure(table_id="right-table", page=1, bbox=[320, 150, 550, 190],
                                 num_rows=1, num_cols=1, cells=[TableCell(row_idx=0, col_idx=0, text="Right table")])]
    resolve_document_layout(doc)
    items = page_items(doc.file_hash, page)
    assert [i.block.text if i.block is not None else i.table.table_id for i in items] == [
        "Left upper", "Left lower", "Right upper", "right-table"]
    ids = {b.text: b.source_id for b in page.blocks}
    md = MarkdownExporter.export_page_markdown(page)
    doc_md = render_document_md(doc, build_structure(document_items(doc)), "none")
    for output in (md, doc_md):
        assert output.index("Left lower") < output.index("Right upper") < output.index("Right table")
    extraction = build_extraction_json(doc, document_items(doc), "cpu")
    assert [i["id"] for i in extraction["pages"][0]["items"]] == page.metadata["ordered_item_ids"]
    page.blocks.reverse()
    resolve_document_layout(doc)
    assert ids == {b.text: b.source_id for b in page.blocks}


def test_graph_cycle_repair_does_not_remove_unrelated_soft_edge():
    nodes = ["a", "b", "c"]
    ordered, reasons = solve_order(nodes, [Edge("a", "b"), Edge("b", "a", False, .8), Edge("b", "c", False, .1)])
    assert ordered == nodes
    assert reasons[0]["removed_edge"] == ["b", "a"]
    _, reasons = solve_order(nodes, [Edge("a", "b"), Edge("b", "a")])
    assert reasons[0]["hard_conflict"]
    assert solve_order(nodes, [Edge("c", "a", relation="caption_of")])[0] == nodes


def test_invalid_flow_is_rejected_instead_of_silently_losing_items():
    doc = document([[block("one", [40, 100, 200, 120])]])
    resolve_document_layout(doc)
    doc.pages[0].metadata["ordered_item_ids"] = []
    with pytest.raises(ValueError, match="ordered_item_ids"):
        page_items(doc.file_hash, doc.pages[0])


def test_unplaced_text_remains_exportable_without_inventing_a_bbox():
    doc = document([[block("Unplaced evidence text", [])]])
    resolve_document_layout(doc)
    structure = build_structure(document_items(doc))
    extraction = build_extraction_json(doc, document_items(doc), "cpu")
    item = extraction["pages"][0]["items"][0]
    assert item["bbox"] == [] and "UNPLACED" in item["review_reason_codes"]
    assert validate_contract_set(extraction, build_document_json(doc, structure, "cpu"),
                                 build_structure_json(doc, structure)) == []
    assert "Unplaced evidence text" in render_document_md(doc, structure, "none")


def test_frozen_text_ids_survive_derived_tables_and_missing_text_is_restored():
    doc = document([[block("Doanh thu 1.234", [40, 100, 250, 120]),
                     block("Lợi nhuận 567", [40, 130, 250, 150])]])
    page = doc.pages[0]
    lock_text_evidence(page.blocks, doc.file_hash, 1, page.metadata)
    source_ids = {b.text: b.source_id for b in page.blocks}
    # Simulate a table stage that removed all text but retained only one line.
    page.blocks = []
    page.tables = [TableStructure(table_id="table", page=1, bbox=[30, 90, 270, 160], num_rows=1, num_cols=1,
                                 cells=[TableCell(row_idx=0, col_idx=0, text="Doanh thu 1.234")])]
    resolve_document_layout(doc)
    assert [(b.text, b.source_id) for b in page.blocks] == [("Lợi nhuận 567", source_ids["Lợi nhuận 567"])]
    assert page.tables[0].provenance["derived_from_source_ids"] == [source_ids["Doanh thu 1.234"]]
    extraction = build_extraction_json(doc, document_items(doc), "cpu")
    assert validate_extraction(extraction) == []
    resolve_document_layout(doc)
    assert page.blocks[0].source_id == source_ids["Lợi nhuận 567"]
    assert len(page.tables[0].provenance["derived_from_source_ids"]) == 1
    extraction["pages"][0]["source_dispositions"].clear()
    assert any("disposition" in error for error in validate_extraction(extraction))


def test_validator_rejects_changed_flow_duplicate_source_and_protected_suppression():
    doc = document([[block("Some body text", [40, 100, 200, 120]), block("Other text", [40, 150, 200, 170])]])
    resolve_document_layout(doc)
    extraction = build_extraction_json(doc, document_items(doc), "cpu")
    assert validate_extraction(extraction) == []
    page = extraction["pages"][0]
    page["items"][1]["source_id"] = page["items"][0]["source_id"]
    page["items"][0].update(visibility="suppressed", protected_from_furniture=True, role_final="running_header",
                             role_status="ACCEPTED", support_page_ids=["p1", "p2", "p3"])
    errors = validate_extraction(extraction)
    assert any("source_id" in error for error in errors)
    assert any("suppression" in error for error in errors)


def test_background_image_does_not_block_newspaper_multi_column_gutters():
    # 3 columns of text: col0: [0, 50, 60, 200], col1: [80, 50, 140, 200], col2: [160, 50, 220, 200]
    nodes = [
        LayoutItem("bg_img", [0.0, 0.0, 230.0, 300.0], "", "image", "i"),
        LayoutItem("c0_1", [0.0, 50.0, 60.0, 100.0]),
        LayoutItem("c0_2", [0.0, 110.0, 60.0, 160.0]),
        LayoutItem("c1_1", [80.0, 50.0, 140.0, 100.0]),
        LayoutItem("c1_2", [80.0, 110.0, 140.0, 160.0]),
        LayoutItem("c2_1", [160.0, 50.0, 220.0, 100.0]),
        LayoutItem("c2_2", [160.0, 110.0, 220.0, 160.0]),
    ]
    result = infer_region_tree(nodes)
    assert result.layout_family == "newspaper_candidate"
    assert result.tree["type"] == "column_group"
    assert len(result.tree["children"]) == 3
    assert result.ordered_ids[0] == "bg_img"
    assert len(result.ordered_ids) == 7
    assert result.tree.get("background_item_ids") == ["bg_img"]

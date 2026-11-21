"""Table absorption requires distinct exact text occurrences at the source location."""
import unicodedata

import pytest

from ocr_contracts.page import ExtractedPage
from ocr_contracts.geometry import VERTICAL_LINE_ORIENTATION_METHOD
from ocr_contracts.page_type import PageType
from ocr_contracts.quality import PageQualityReport
from ocr_contracts.table import TableCell, TableStructure
from ocr_contracts.text_block import TextBlock
from ocr_document.resolve_document_layout import _reconcile_evidence
from ocr_document.source_identity import lock_text_evidence


def page_with_removed_sources(sources, cells):
    blocks = [TextBlock(text=text, text_raw=text, page=1, bbox=box, confidence=.99, quality_score=.99)
              for text, box in sources]
    page = ExtractedPage(page_num=1, width=400, height=500, page_type=PageType.MIXED,
                         blocks=blocks, quality=PageQualityReport(page=1, page_type="SCANNED"))
    lock_text_evidence(blocks, "test-hash", 1, page.metadata)
    page.tables = [TableStructure(table_id="t", page=1, bbox=[0, 0, 400, 300],
                                 num_rows=max(cell.row_idx for cell in cells) + 1, num_cols=2,
                                 cells=cells)]
    page.blocks = []
    return page


def statuses(page):
    return [item["status"] for item in page.metadata["source_dispositions"].values()]


def test_one_table_occurrence_cannot_replace_two_source_occurrences():
    page = page_with_removed_sources([("120", [10, 10, 40, 20]), ("120", [10, 60, 40, 70])],
                                    [TableCell(row_idx=0, col_idx=0, text="120")])
    _reconcile_evidence(page)
    assert statuses(page).count("REPLACED_BY_TABLE") == 1
    assert statuses(page).count("KEPT_FOR_REVIEW") == 1
    assert len(page.tables[0].provenance["derived_from_source_ids"]) == 1
    assert [block.text_raw for block in page.blocks] == ["120"]


def test_equal_text_in_a_different_cell_location_cannot_erase_source():
    page = page_with_removed_sources([("120", [10, 10, 40, 20])], [
        TableCell(row_idx=0, col_idx=0, text="130", bbox=[0, 0, 100, 40]),
        TableCell(row_idx=1, col_idx=0, text="120", bbox=[0, 50, 100, 90])])
    _reconcile_evidence(page)
    assert statuses(page) == ["KEPT_FOR_REVIEW"]
    assert page.blocks[0].text == "120"


def test_spatially_matched_duplicate_values_map_to_distinct_cells():
    page = page_with_removed_sources([("120", [10, 10, 40, 20]), ("120", [10, 60, 40, 70])], [
        TableCell(row_idx=0, col_idx=0, text="120", bbox=[0, 0, 100, 40]),
        TableCell(row_idx=1, col_idx=0, text="120", bbox=[0, 50, 100, 90])])
    _reconcile_evidence(page)
    assert statuses(page) == ["REPLACED_BY_TABLE", "REPLACED_BY_TABLE"]
    assert page.blocks == []
    assert len(page.tables[0].provenance["derived_from_source_ids"]) == 2
    mappings = list(page.tables[0].provenance["source_occurrence_mapping"].values())
    assert mappings[0][0]["cell_index"] != mappings[1][0]["cell_index"]


@pytest.mark.parametrize("source,cell", [
    ("(120)", "120"), ("1.200", "1,200"), ("120", "1200"),
    ("USD", "usd"), ("Báo cáo", "báo cáo"), ("120", "-120"),
])
def test_semantic_changes_and_partial_numbers_are_not_exact_replacement(source, cell):
    page = page_with_removed_sources([(source, [10, 10, 100, 20])],
                                    [TableCell(row_idx=0, col_idx=0, text=cell)])
    _reconcile_evidence(page)
    assert statuses(page) == ["KEPT_FOR_REVIEW"]
    assert page.blocks[0].text_raw == source


def test_nfc_whitespace_and_a_line_split_across_cells_in_one_row_are_supported():
    text = unicodedata.normalize("NFD", "Doanh thu") + "   1.234"
    page = page_with_removed_sources([(text, [10, 10, 180, 20])], [
        TableCell(row_idx=0, col_idx=0, text="Doanh thu", bbox=[0, 0, 100, 40]),
        TableCell(row_idx=0, col_idx=1, text="1.234", bbox=[100, 0, 200, 40])])
    _reconcile_evidence(page)
    assert statuses(page) == ["REPLACED_BY_TABLE"]
    _reconcile_evidence(page)
    assert statuses(page) == ["REPLACED_BY_TABLE"]
    assert len(page.tables[0].provenance["derived_from_source_ids"]) == 1


def test_without_cell_boxes_different_rows_cannot_form_a_source_line():
    page = page_with_removed_sources([("Doanh thu 120", [10, 10, 180, 20])], [
        TableCell(row_idx=0, col_idx=0, text="Doanh thu"),
        TableCell(row_idx=1, col_idx=0, text="120")])
    _reconcile_evidence(page)
    assert statuses(page) == ["KEPT_FOR_REVIEW"]


def test_ambiguous_cells_without_geometry_keep_source_for_review():
    page = page_with_removed_sources([("120", [10, 10, 40, 20])], [
        TableCell(row_idx=0, col_idx=0, text="120"),
        TableCell(row_idx=1, col_idx=0, text="120")])
    _reconcile_evidence(page)
    assert statuses(page) == ["KEPT_FOR_REVIEW"]


def test_overlapping_phrases_cannot_reuse_consumed_table_tokens():
    page = page_with_removed_sources([("Doanh thu 120", [10, 10, 180, 20]), ("120", [100, 10, 130, 20])],
                                    [TableCell(row_idx=0, col_idx=0, text="Doanh thu 120")])
    _reconcile_evidence(page)
    assert statuses(page) == ["REPLACED_BY_TABLE", "KEPT_FOR_REVIEW"]


def test_changing_table_text_revokes_previous_absorption_claim():
    page = page_with_removed_sources([("120", [10, 10, 40, 20])],
                                    [TableCell(row_idx=0, col_idx=0, text="120")])
    _reconcile_evidence(page)
    assert statuses(page) == ["REPLACED_BY_TABLE"]
    page.tables[0].cells[0].text = "130"
    _reconcile_evidence(page)
    assert statuses(page) == ["KEPT_FOR_REVIEW"]
    assert page.blocks[0].text_raw == "120"
    assert page.tables[0].provenance["derived_from_source_ids"] == []
    assert page.tables[0].provenance["source_occurrence_mapping"] == {}


def test_no_frozen_source_evidence_leaves_table_provenance_unchanged():
    page = page_with_removed_sources([("120", [10, 10, 40, 20])],
                                    [TableCell(row_idx=0, col_idx=0, text="120")])
    page.metadata.clear()
    before = dict(page.tables[0].provenance)
    _reconcile_evidence(page)
    assert page.tables[0].provenance == before


@pytest.mark.parametrize(("cell_box", "placed", "status"), [
    ([0, 0, 50, 100], True, "REPLACED_BY_TABLE"),
    ([0, 0, 50, 100], False, "KEPT_FOR_REVIEW"),
    ([0, 0, 50, 60], True, "KEPT_FOR_REVIEW"),
    ([], True, "KEPT_FOR_REVIEW"),
])
def test_vertical_line_requires_placement_and_one_cell_to_contain_the_whole_source(cell_box, placed, status):
    page = page_with_removed_sources([("Số (020)", [15, 10, 26, 90])],
                                    [TableCell(row_idx=0, col_idx=0, text="Số (020)", bbox=cell_box)])
    source = page.metadata["text_evidence"]["items"][0]
    source["recognition_provenance"] = {"method": VERTICAL_LINE_ORIENTATION_METHOD, "rotation_clockwise_degrees": 90}
    if placed:
        page.metadata["ocr_structure_bridge"] = {"orientation_placements": [
            {"source_id": source["source_id"], "status": "PLACED_ORIENTATION_HYPOTHESIS"}
        ]}
    _reconcile_evidence(page)
    assert statuses(page) == [status]
    if status == "KEPT_FOR_REVIEW":
        assert page.blocks[0].text_raw == "Số (020)" and page.blocks[0].bbox == source["bbox"]


def test_placed_line_float32_rounding_boundary_maps_without_relaxing_outside_cells():
    from ocr_document.contract_schema import validate_extraction
    from ocr_document.resolve_document_layout import _cell_contains_placed_line

    # Straddles the 131.625 half-cent boundary by 3e-5pt (inverse rotation float32 drift).
    placed = [131.62497, 362.5472, 139.63496, 407.6430]
    cell = [131.62500, 362.5470, 139.63500, 407.64299]
    assert _cell_contains_placed_line(placed, cell) is True
    # Exported extraction.json rounds cell boxes to 0.01pt; contract_schema matches it.
    exported_cell = [round(v, 2) for v in cell]
    assert _cell_contains_placed_line(placed, exported_cell, eps=0.01) is True
    # Genuine outside displacement must still abstain.
    assert _cell_contains_placed_line([131.50, 362.55, 139.63, 407.64], cell) is False
    assert _cell_contains_placed_line([131.50, 362.55, 139.63, 407.64], exported_cell, eps=0.01) is False
    assert callable(validate_extraction)

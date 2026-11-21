from types import SimpleNamespace

import pymupdf
import pytest

from ocr_structure.docling_client import DoclingStructureError
from ocr_structure.region_views import RegionStructureExtractor


def table(name, box, cell_box=None):
    return SimpleNamespace(table_id=name, bbox=box, provenance={'table_orientation': 'rot_0'},
                           cells=[SimpleNamespace(bbox=cell_box or box)])


def payload(tables):
    return dict(tables=tables, formulas=[], metadata=dict(status='success', errors=[]))


def source():
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((30, 100), 'LOAN 120', rotate=90)
    return doc, page


def test_model_sees_upright_panel_and_cells_return_to_original_coordinates():
    doc, page = source()
    class Model:
        options = {'on_error': 'fallback'}
        calls = 0
        def extract_page(self, view, number):
            self.calls += 1
            if self.calls == 1:
                # The primary model includes notes around the actual table;
                # its rectangle exceeds the upright model's table rectangle.
                return payload([table('partial', [11, 11, 49, 129]), table('outside', [150, 20, 180, 60])])
            assert tuple(view.get_text('dict')['blocks'][0]['lines'][0]['dir']) == (1., 0.)
            assert (view.rect.width, view.rect.height) == (120, 40)
            return payload([table('loan', [10, 5, 110, 35], [20, 10, 100, 20])])
    result = RegionStructureExtractor(Model(), [dict(id='panel', bbox=[10, 10, 50, 130],
                         clockwise_degrees=90, method='visible_geometry')]).extract_page(page, 1)
    mapped = result['tables'][-1]
    assert mapped.bbox == [15, 20, 45, 120]
    assert mapped.cells[0].bbox == [20, 30, 30, 110]
    assert mapped.provenance['table_orientation'] == 'rot_90'
    assert [t.table_id for t in result['tables']] == ['outside', 'p1_view1_loan']
    assert result['metadata']['superseded_primary_tables'][0]['table_id'] == 'partial'
    doc.close()


def test_failed_panel_keeps_primary_structure_and_error():
    doc, page = source()
    class Model:
        options = {'on_error': 'fallback'}
        calls = 0
        def extract_page(self, view, number):
            self.calls += 1
            if self.calls == 1:
                return payload([table('primary', [10, 10, 40, 120])])
            raise DoclingStructureError('fixture model failure')
    result = RegionStructureExtractor(Model(), [dict(id='panel', bbox=[10, 10, 50, 130],
                         clockwise_degrees=90, method='visible_geometry')]).extract_page(page, 1)
    assert result['tables'][0].table_id == 'primary'
    assert 'fixture model failure' in result['metadata']['errors'][0]
    doc.close()


def test_ocr_bridge_inserts_rotated_90_lines_in_upright_reading_order():
    from ocr_contracts.geometry import VERTICAL_LINE_ORIENTATION_METHOD
    from ocr_contracts.page import ExtractedPage
    from ocr_contracts.page_type import PageType
    from ocr_contracts.quality import PageQualityReport
    from ocr_contracts.text_block import TextBlock
    from ocr_structure.ocr_bridge import ocr_bridge

    # Line 2 has smaller y0 (682 vs 694) so unrotated (y0, x0) order puts it
    # first, but in 90-CW upright view smaller x0 (953) is above larger x0 (964).
    line2 = TextBlock(
        text="lượng sạch ninh thuận (i)", text_raw="lượng sạch ninh thuận (i)", page=1,
        bbox=[963.93, 682.07, 972.56, 786.92], source="primary_ocr", source_id="doc:p0001:b000001",
        recognition_provenance={"method": VERTICAL_LINE_ORIENTATION_METHOD, "rotation_clockwise_degrees": 90},
    )
    line1 = TextBlock(
        text="Công ty Cổ phần Năng", text_raw="Công ty Cổ phần Năng", page=1,
        bbox=[953.56, 693.81, 963.20, 786.73], source="primary_ocr", source_id="doc:p0001:b000002",
        recognition_provenance={"method": VERTICAL_LINE_ORIENTATION_METHOD, "rotation_clockwise_degrees": 90},
    )
    extracted = ExtractedPage(
        page_num=1, width=1191.0, height=842.0, page_type=PageType.MIXED,
        blocks=[line2, line1], quality=PageQualityReport(page=1, page_type="MIXED"),
    )
    placements = []
    with pymupdf.open() as src:
        src_page = src.new_page(width=1191.0, height=842.0)
        with ocr_bridge(src_page, extracted, 72, orientation_placements=placements) as bridge:
            text = " ".join(bridge[0].get_text().split())
            assert text.index("Công ty Cổ phần Năng") < text.index("lượng sạch ninh thuận (i)")
    assert len(placements) == 2
    assert all(p.get("placed_text_bbox") for p in placements)
    assert {p["source_id"] for p in placements} == {"doc:p0001:b000001", "doc:p0001:b000002"}

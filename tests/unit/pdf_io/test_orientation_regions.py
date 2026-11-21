"""Use visible directions as geometry, never hidden text as OCR content."""
import pymupdf
from ocr_pdf_io.orientation_regions import visible_orientation_geometry


def add_region(page,hidden=False):
    for i in range(8):
        page.insert_text((60+12*i,260),'Doanh thu 120',fontsize=9,rotate=90,render_mode=3 if hidden else 0)


def test_mixed_page_keeps_upright_panel_outside_rotated_region():
    with pymupdf.open() as doc:
        page=doc.new_page(width=500,height=300);add_region(page)
        page.insert_text((300,30),'OTHER PANEL ordinary text',fontsize=10)
        result=visible_orientation_geometry(page)
        assert not result['upright'] and not result['native_text_copied']
        region,=result['regions']
        assert region['clockwise_degrees']==90 and region['bbox'][2]<250
        assert 'Doanh' not in str(result)


def test_invisible_text_does_not_authorize_a_region_turn():
    with pymupdf.open() as doc:
        page=doc.new_page(width=500,height=300);add_region(page,hidden=True)
        result=visible_orientation_geometry(page)
        assert not result['regions'] and not result['upright']


def test_display_rotation_is_composed_with_native_writing_direction():
    with pymupdf.open() as doc:
        page=doc.new_page(width=500,height=300);add_region(page);page.set_rotation(90)
        result=visible_orientation_geometry(page)
        assert result['upright'] and not result['regions']

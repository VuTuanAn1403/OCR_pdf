from types import SimpleNamespace

from src.ocr.domain.models.region import BoundingBox, ContentType, Region
from src.ocr.infrastructure.export.markdown_exporter import MarkdownExporter


def test_footer_does_not_hide_preserved_page_image():
    image = Region(
        region_id="p41_r2", content_type=ContentType.IMAGE, status="PRESERVED",
        bbox=BoundingBox(x0=0, y0=0, x1=600, y1=840),
    )
    footer = SimpleNamespace(bbox=[200, 750, 400, 780])
    page = SimpleNamespace(regions=[image], blocks=[footer], tables=[])
    assert MarkdownExporter.image_regions_to_preserve(page) == [image]


def test_large_embedded_image_remains_available_after_ocr():
    image = Region(
        region_id="p1_scan", content_type=ContentType.IMAGE, status="EXTRACTED",
        bbox=BoundingBox(x0=0, y0=0, x1=500, y1=700),
    )
    recognized = SimpleNamespace(bbox=[50, 80, 250, 100])
    page = SimpleNamespace(width=600, height=800, regions=[image], blocks=[recognized], tables=[])
    assert MarkdownExporter.image_regions_to_preserve(page) == [image]


def test_off_page_image_tile_is_not_exported_as_broken_link():
    image = Region(
        region_id="p3_r17", content_type=ContentType.IMAGE, status="PRESERVED",
        bbox=BoundingBox(x0=-265, y0=-2004, x1=131, y1=-1391),
    )
    page = SimpleNamespace(width=595, height=842, regions=[image], blocks=[], tables=[])
    assert MarkdownExporter.image_regions_to_preserve(page) == []

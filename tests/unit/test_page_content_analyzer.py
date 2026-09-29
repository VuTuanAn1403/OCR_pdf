"""
V3.1.2 Phase A – Unit tests for PageContentAnalyzer

Tests the core Content Composition decomposition logic without
requiring a real PDF file or OCR engine.
"""

import pytest
from unittest.mock import MagicMock, patch
import cv2
import numpy as np

from src.ocr.domain.models.region import ContentType, BoundingBox, Region
from src.ocr.infrastructure.layout.page_content_analyzer import PageContentAnalyzer


@pytest.fixture
def analyzer():
    return PageContentAnalyzer()


def _make_mock_page(
    width=595.0,
    height=842.0,
    text_blocks=None,
    images=None,
    drawings=None,
):
    """Create a mock pymupdf.Page for testing."""
    page = MagicMock()
    page.rect = MagicMock()
    page.rect.width = width
    page.rect.height = height
    page.rect.x0 = 0.0
    page.rect.y0 = 0.0

    # text blocks: list of (x0, y0, x1, y1, text, block_no, block_type)
    if text_blocks is None:
        text_blocks = []
    page.get_text = MagicMock(side_effect=lambda fmt: text_blocks if fmt == "blocks" else "")

    # drawings
    page.get_drawings = MagicMock(return_value=drawings or [])

    return page


def _make_meta(
    page_num=1,
    width=595.0,
    height=842.0,
    char_count=500,
    image_rects=None,
    native_extraction_failed=False,
):
    page_area = width * height
    return {
        "page_num": page_num,
        "width": width,
        "height": height,
        "page_area": round(page_area, 2),
        "char_count": char_count,
        "image_rects": image_rects or [],
        "native_extraction_failed": native_extraction_failed,
        "image_count": len(image_rects or []),
        "image_coverage_ratio": 0.0,
    }


class TestPureTextPage:
    """Test Case A: Pure text page → all TEXT regions."""

    def test_text_only_produces_text_regions(self, analyzer):
        text_blocks = [
            (50.0, 50.0, 545.0, 80.0, "Dòng 1", 0, 0),
            (50.0, 90.0, 545.0, 120.0, "Dòng 2", 1, 0),
            (50.0, 130.0, 545.0, 160.0, "Dòng 3", 2, 0),
        ]
        meta = _make_meta(char_count=200)
        page = _make_mock_page(text_blocks=text_blocks)

        regions = analyzer.analyze(meta, page)

        assert len(regions) >= 3
        for r in regions:
            assert r.content_type == ContentType.TEXT
            assert r.source == "native"
            assert r.confidence == 1.0

    def test_text_regions_have_correct_bbox(self, analyzer):
        text_blocks = [
            (10.0, 20.0, 300.0, 40.0, "Hello", 0, 0),
        ]
        meta = _make_meta()
        page = _make_mock_page(text_blocks=text_blocks)

        regions = analyzer.analyze(meta, page)
        assert len(regions) == 1
        r = regions[0]
        assert r.bbox.x0 == 10.0
        assert r.bbox.y0 == 20.0
        assert r.bbox.x1 == 300.0
        assert r.bbox.y1 == 40.0


class TestTextPlusImagePage:
    """Test Case B: Text + image page → TEXT + IMAGE regions."""

    def test_text_plus_image_creates_both_types(self, analyzer):
        text_blocks = [
            (50.0, 50.0, 545.0, 200.0, "Native text paragraph", 0, 0),
            (50.0, 500.0, 545.0, 600.0, "More text after image", 1, 0),
        ]
        # Image in the middle of the page (large enough to matter)
        image_rects = [[100.0, 220.0, 450.0, 480.0]]
        meta = _make_meta(char_count=300, image_rects=image_rects)
        page = _make_mock_page(text_blocks=text_blocks)

        regions = analyzer.analyze(meta, page)

        content_types = {r.content_type for r in regions}
        assert ContentType.TEXT in content_types
        assert ContentType.IMAGE in content_types or ContentType.IMAGE_TABLE in content_types

    def test_small_image_ignored(self, analyzer):
        """Images smaller than MIN_IMAGE_AREA_PT2 should not create regions."""
        text_blocks = [
            (50.0, 50.0, 545.0, 200.0, "Some text", 0, 0),
        ]
        # Tiny image (20x20 = 400pt², below 2500pt² threshold)
        image_rects = [[10.0, 10.0, 30.0, 30.0]]
        meta = _make_meta(char_count=100, image_rects=image_rects)
        page = _make_mock_page(text_blocks=text_blocks)

        regions = analyzer.analyze(meta, page)
        image_regions = [r for r in regions if r.content_type in (ContentType.IMAGE, ContentType.IMAGE_TABLE)]
        assert len(image_regions) == 0


class TestUnknownRegionPreservation:
    """Test Case C: Empty page → UNKNOWN region preserved."""

    def test_empty_page_creates_unknown_region(self, analyzer):
        meta = _make_meta(char_count=0, native_extraction_failed=True)
        page = _make_mock_page(text_blocks=[])

        regions = analyzer.analyze(meta, page)

        assert len(regions) >= 1
        unknown_regions = [r for r in regions if r.content_type == ContentType.UNKNOWN]
        assert len(unknown_regions) == 1
        assert unknown_regions[0].confidence == 0.0

    def test_unknown_region_covers_full_page(self, analyzer):
        meta = _make_meta(char_count=0, native_extraction_failed=True, width=595.0, height=842.0)
        page = _make_mock_page(text_blocks=[], width=595.0, height=842.0)

        regions = analyzer.analyze(meta, page)
        unknown = [r for r in regions if r.content_type == ContentType.UNKNOWN][0]
        assert unknown.bbox.x0 == 0.0
        assert unknown.bbox.y0 == 0.0
        assert unknown.bbox.x1 == 595.0
        assert unknown.bbox.y1 == 842.0


class TestReadingOrder:
    """Test Case D: Regions have correct reading order."""

    def test_regions_sorted_top_to_bottom(self, analyzer):
        text_blocks = [
            (50.0, 300.0, 545.0, 330.0, "Bottom text", 0, 0),
            (50.0, 50.0, 545.0, 80.0, "Top text", 1, 0),
        ]
        meta = _make_meta(char_count=200)
        page = _make_mock_page(text_blocks=text_blocks)

        regions = analyzer.analyze(meta, page)
        y_positions = [r.bbox.y0 for r in regions]
        assert y_positions == sorted(y_positions)


class TestBoundingBoxHelpers:
    """Test BoundingBox utility methods added for V3.1.2."""

    def test_contains_point_inside(self):
        bb = BoundingBox(x0=10, y0=20, x1=100, y1=80)
        assert bb.contains_point(50, 50) is True

    def test_contains_point_outside(self):
        bb = BoundingBox(x0=10, y0=20, x1=100, y1=80)
        assert bb.contains_point(200, 50) is False

    def test_overlap_ratio_no_overlap(self):
        bb1 = BoundingBox(x0=0, y0=0, x1=50, y1=50)
        bb2 = BoundingBox(x0=100, y0=100, x1=150, y1=150)
        assert bb1.overlap_ratio(bb2) == 0.0

    def test_overlap_ratio_full_overlap(self):
        bb1 = BoundingBox(x0=0, y0=0, x1=100, y1=100)
        bb2 = BoundingBox(x0=10, y0=10, x1=50, y1=50)
        ratio = bb1.overlap_ratio(bb2)
        assert ratio == pytest.approx(1.0, abs=0.01)

    def test_overlap_ratio_partial(self):
        bb1 = BoundingBox(x0=0, y0=0, x1=100, y1=100)
        bb2 = BoundingBox(x0=50, y0=50, x1=150, y1=150)
        ratio = bb1.overlap_ratio(bb2)
        assert 0.0 < ratio < 1.0


class TestRegionModel:
    """Test V3.1.2 Region model fields."""

    def test_region_has_content_type(self):
        r = Region(
            region_id="p1_r1",
            bbox=BoundingBox(x0=0, y0=0, x1=100, y1=100),
            content_type=ContentType.IMAGE_TABLE,
            source="image",
        )
        assert r.content_type == ContentType.IMAGE_TABLE
        assert r.source == "image"
        assert r.rotation == 0.0
        assert r.reading_order == -1
        assert r.parent_region is None

    def test_region_backward_compat(self):
        """V3.1.1 fields still work."""
        r = Region(
            region_id="p1_r1",
            bbox=BoundingBox(x0=0, y0=0, x1=100, y1=100),
            region_type="text",
            confidence=0.95,
        )
        assert r.region_type == "text"
        assert r.content_type == ContentType.TEXT  # default


class TestV32TwoUpSegmentation:
    def test_native_two_up_groups_left_and_right_text(self, analyzer):
        text_blocks = [
            (40.0, 50.0, 250.0, 75.0, "Left 1", 0, 0),
            (40.0, 90.0, 250.0, 115.0, "Left 2", 1, 0),
            (345.0, 50.0, 555.0, 75.0, "Right 1", 2, 0),
            (345.0, 90.0, 555.0, 115.0, "Right 2", 3, 0),
        ]
        meta = _make_meta(width=595.0, height=400.0, char_count=100)
        page = _make_mock_page(width=595.0, height=400.0, text_blocks=text_blocks)

        regions = analyzer.analyze(meta, page)

        assert meta["layout_type"] == "TWO_UP"
        assert len(regions) == 2
        assert regions[0].bbox.x1 <= regions[1].bbox.x0
        assert all(r.region_type == "two_up" for r in regions)

    def test_rendered_two_up_requires_gutter_and_splits(self, analyzer):
        image = np.full((200, 600, 3), 255, dtype=np.uint8)
        cv2.rectangle(image, (20, 20), (270, 180), (0, 0, 0), -1)
        cv2.rectangle(image, (330, 20), (580, 180), (0, 0, 0), -1)
        meta = _make_meta(width=600.0, height=200.0, char_count=0, image_rects=[[0, 0, 600, 200]])
        page = _make_mock_page(width=600.0, height=200.0, text_blocks=[])
        base_regions = analyzer.analyze(meta, page)

        regions = analyzer.refine_with_rendered_image(meta, base_regions, image)

        assert meta["layout_type"] == "TWO_UP"
        assert len(regions) == 2
        assert regions[0].bbox.x1 <= regions[1].bbox.x0

    def test_rendered_page_without_gutter_is_preserved(self, analyzer):
        image = np.full((200, 600, 3), 255, dtype=np.uint8)
        cv2.rectangle(image, (20, 20), (580, 180), (0, 0, 0), -1)
        meta = _make_meta(width=600.0, height=200.0, char_count=0, image_rects=[[0, 0, 600, 200]])
        page = _make_mock_page(width=600.0, height=200.0, text_blocks=[])
        base_regions = analyzer.analyze(meta, page)

        regions = analyzer.refine_with_rendered_image(meta, base_regions, image)

        assert "layout_type" not in meta
        assert len(regions) == len(base_regions)

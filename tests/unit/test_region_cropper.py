"""
V3.1.2 Phase B – Unit tests for RegionCropper
"""

import pytest
from unittest.mock import MagicMock
import numpy as np

from src.ocr.domain.models.region import BoundingBox
from src.ocr.infrastructure.pdf.region_cropper import RegionCropper


class TestRegionCropperCoordinates:
    def test_map_crop_bbox_to_page_basic(self):
        # Cropped image is 200x100 pixels
        # Crop rect on page is [50.0, 100.0, 150.0, 150.0] (width 100pt, height 50pt)
        # Bbox in crop is [20, 10, 100, 50] (pixels)
        crop_bbox = [20.0, 10.0, 100.0, 50.0]
        crop_rect = [50.0, 100.0, 150.0, 150.0]
        crop_img_w = 200
        crop_img_h = 100

        # scale_x = 100 / 200 = 0.5 pt/px
        # scale_y = 50 / 100 = 0.5 pt/px
        # expected x0 = 50 + 20*0.5 = 60.0
        # expected y0 = 100 + 10*0.5 = 105.0
        # expected x1 = 50 + 100*0.5 = 100.0
        # expected y1 = 100 + 50*0.5 = 125.0
        page_bbox = RegionCropper.map_crop_bbox_to_page(
            crop_bbox, crop_rect, crop_img_w, crop_img_h
        )
        assert page_bbox == [60.0, 105.0, 100.0, 125.0]

    def test_map_crop_bbox_zero_size_safeguard(self):
        crop_bbox = [10.0, 10.0, 50.0, 50.0]
        crop_rect = [100.0, 100.0, 200.0, 200.0]
        # Zero dimensions shouldn't throw ZeroDivisionError
        result = RegionCropper.map_crop_bbox_to_page(crop_bbox, crop_rect, 0, 0)
        assert result == crop_rect


class TestRegionCropperIoU:
    def test_no_overlap(self):
        box1 = [0.0, 0.0, 10.0, 10.0]
        box2 = [20.0, 20.0, 30.0, 30.0]
        assert RegionCropper.compute_iou(box1, box2) == 0.0
        assert RegionCropper.compute_intersection_over_min(box1, box2) == 0.0

    def test_exact_overlap(self):
        box1 = [10.0, 10.0, 50.0, 50.0]
        box2 = [10.0, 10.0, 50.0, 50.0]
        assert pytest.approx(RegionCropper.compute_iou(box1, box2)) == 1.0
        assert pytest.approx(RegionCropper.compute_intersection_over_min(box1, box2)) == 1.0

    def test_partial_overlap(self):
        box1 = [0.0, 0.0, 10.0, 10.0]   # area = 100
        box2 = [5.0, 0.0, 15.0, 10.0]   # area = 100
        # intersection = 5 * 10 = 50
        # union = 100 + 100 - 50 = 150
        # iou = 50 / 150 = 1/3
        iou = RegionCropper.compute_iou(box1, box2)
        assert pytest.approx(iou, rel=1e-3) == 1.0 / 3.0

    def test_contained_box(self):
        outer = [0.0, 0.0, 100.0, 100.0]  # area = 10000
        inner = [20.0, 20.0, 40.0, 40.0]   # area = 400
        # intersection = 400
        # intersection_over_min = 400 / 400 = 1.0
        ratio = RegionCropper.compute_intersection_over_min(outer, inner)
        assert pytest.approx(ratio) == 1.0


class TestCropPageRegionMock:
    def test_crop_invalid_bbox_returns_empty(self):
        mock_page = MagicMock()
        mock_rect = MagicMock()
        mock_rect.width = 500
        mock_rect.height = 700
        mock_page.rect = mock_rect

        # x1 <= x0
        bb = BoundingBox(x0=100.0, y0=50.0, x1=50.0, y1=100.0)
        img, rect = RegionCropper.crop_page_region(mock_page, bb)
        assert img.shape == (0, 0, 3)
        assert rect == [0.0, 0.0, 0.0, 0.0]

    def test_crop_normal_calls_pixmap(self):
        mock_page = MagicMock()
        mock_rect = MagicMock()
        mock_rect.width = 500
        mock_rect.height = 700
        mock_page.rect = mock_rect

        # Mock pixmap
        mock_pix = MagicMock()
        mock_pix.h = 100
        mock_pix.w = 100
        mock_pix.n = 3
        mock_pix.samples = np.zeros((100, 100, 3), dtype=np.uint8).tobytes()
        mock_page.get_pixmap.return_value = mock_pix

        bb = BoundingBox(x0=50.0, y0=50.0, x1=150.0, y1=150.0)
        img, actual_rect = RegionCropper.crop_page_region(mock_page, bb, dpi=180, margin=2.0)

        assert mock_page.get_pixmap.called
        assert actual_rect == [48.0, 48.0, 152.0, 152.0]
        assert img.shape == (100, 100, 3)

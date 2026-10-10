"""
Unit tests for V3.2.1 Two-Up and Mixed Orientation Fixes:
- Mixed-source Two-Up detection (TU-03, TU-04)
- Adjacent image Two-Up detection (TU-02)
- Glyph noise detection (_is_glyph_noise)
- Orientation scoring favoring upright Vietnamese text
- Fallback triggered even when 0-degree has glyph noise
"""

import pytest
from unittest.mock import MagicMock
import numpy as np

from src.ocr.domain.models.region import BoundingBox, ContentType, Region
from src.ocr.infrastructure.layout.page_content_analyzer import PageContentAnalyzer
from src.ocr.application.process_page import ProcessPageUseCase


class TestMixedTwoUpDetection:
    def test_tu03_detection(self):
        """TU-03: Left half has native text blocks, Right half has substantial image."""
        analyzer = PageContentAnalyzer()
        page_w = 1190.55
        page_h = 841.89
        meta = {
            "page_num": 64,
            "width": page_w,
            "height": page_h,
            "page_area": page_w * page_h,
        }

        # Left native text blocks (x in [50, 450], y in [100, 700])
        text_bboxes = [
            BoundingBox(x0=50.0, y0=100.0 + i * 40.0, x1=400.0, y1=130.0 + i * 40.0)
            for i in range(10)
        ]
        # Right substantial image (x in [595, 1190], area ~ 45%)
        image_bboxes = [
            BoundingBox(x0=595.0, y0=35.0, x1=1190.0, y1=792.0)
        ]

        subtype = analyzer._detect_mixed_source_two_up(text_bboxes, image_bboxes, meta)
        assert subtype == "TU-03"

    def test_tu04_detection(self):
        """TU-04: Left half has substantial image, Right half has native text blocks."""
        analyzer = PageContentAnalyzer()
        page_w = 1190.55
        page_h = 841.89
        meta = {
            "page_num": 68,
            "width": page_w,
            "height": page_h,
            "page_area": page_w * page_h,
        }

        # Left substantial image (x in [0, 595], area ~ 45%)
        image_bboxes = [
            BoundingBox(x0=0.0, y0=35.0, x1=595.0, y1=792.0)
        ]
        # Right native text blocks (x in [650, 1100], y in [100, 700])
        text_bboxes = [
            BoundingBox(x0=650.0, y0=100.0 + i * 40.0, x1=1050.0, y1=130.0 + i * 40.0)
            for i in range(10)
        ]

        subtype = analyzer._detect_mixed_source_two_up(text_bboxes, image_bboxes, meta)
        assert subtype == "TU-04"

    def test_tu02_adjacent_images(self):
        """TU-02: Both left and right halves have substantial images."""
        analyzer = PageContentAnalyzer()
        page_w = 1190.55
        page_h = 841.89
        meta = {
            "page_num": 63,
            "width": page_w,
            "height": page_h,
            "page_area": page_w * page_h,
        }

        image_bboxes = [
            BoundingBox(x0=0.0, y0=35.0, x1=595.0, y1=792.0),
            BoundingBox(x0=595.0, y0=35.0, x1=1190.0, y1=792.0),
        ]

        is_tu02 = analyzer._detect_adjacent_image_two_up(image_bboxes, meta)
        assert is_tu02 is True


class TestGlyphNoiseDetection:
    def test_glyph_noise_detected(self):
        """Random vertical fragments from unrotated landscape scan must be detected as noise."""
        noise_results = [
            {"text": "L", "confidence": 0.98},
            {"text": "s", "confidence": 0.99},
            {"text": "e", "confidence": 0.95},
            {"text": "lễ", "confidence": 0.94},
            {"text": "H", "confidence": 0.90},
            {"text": "3", "confidence": 0.85},
            {"text": "và", "confidence": 0.88},
        ]
        assert ProcessPageUseCase._is_glyph_noise(noise_results) is True

    def test_clean_text_not_noise(self):
        """Legitimate text blocks must not be flagged as noise."""
        clean_results = [
            {"text": "BÁO CÁO TÀI CHÍNH HỢP NHẤT", "confidence": 0.95},
            {"text": "Cho năm tài chính kết thúc ngày 31/12/2022", "confidence": 0.93},
            {"text": "Đơn vị tính: VND", "confidence": 0.92},
            {"text": "Phải thu ngắn hạn của khách hàng", "confidence": 0.96},
            {"text": "Công ty TNHH Kinh doanh và Thương mại", "confidence": 0.94},
        ]
        assert ProcessPageUseCase._is_glyph_noise(clean_results) is False


class TestOrientationScoring:
    def test_upright_beats_noise_and_upside_down(self):
        """Upright Vietnamese text must score significantly higher than noise or upside-down text."""
        upright_results = [
            {"text": "BÁO CÁO TÀI CHÍNH HỢP NHẤT", "confidence": 0.95},
            {"text": "Cho năm tài chính kết thúc ngày 31/12/2022", "confidence": 0.93},
            {"text": "Công ty Cổ phần Bia Rượu Nước giải khát Hà Nội", "confidence": 0.94},
            {"text": "BẢN THUYẾT MINH BÁO CÁO TÀI CHÍNH", "confidence": 0.96},
            {"text": "Chi phí sản xuất kinh doanh dở dang", "confidence": 0.92},
            {"text": "Phải thu của khách hàng ngắn hạn", "confidence": 0.95},
        ]

        upside_down_results = [
            {"text": "LYHN TỎH HNJHO IVI OYIOYS", "confidence": 0.90},
            {"text": "7207/71/LE KEMU SNPP 1974 QUY", "confidence": 0.88},
            {"text": "IỘN VH LYHY TYI HỌ NN HÓNH VII NYHI", "confidence": 0.85},
            {"text": "TSE LVT LSUT 000'000 058'61", "confidence": 0.82},
        ]

        noise_results = [
            {"text": "L", "confidence": 0.98},
            {"text": "s", "confidence": 0.99},
            {"text": "e", "confidence": 0.95},
            {"text": "H", "confidence": 0.90},
        ]

        score_upright = ProcessPageUseCase._orientation_candidate_score(upright_results)
        score_upside_down = ProcessPageUseCase._orientation_candidate_score(upside_down_results)
        score_noise = ProcessPageUseCase._orientation_candidate_score(noise_results)

        assert score_upright > score_upside_down + 15.0
        assert score_upright > score_noise + 20.0


class TestOrientationFallbackOnNoise:
    def test_fallback_does_not_early_exit_on_noise(self):
        """When 0 deg produces glyph noise, fallback must proceed to evaluate rotations."""
        uc = ProcessPageUseCase.__new__(ProcessPageUseCase)
        uc.config = {"orientation_fallback_enabled": True}
        uc.ocr_engine = MagicMock()

        # Call 0 (0 deg): glyph noise ("L", "s", "e")
        # Call 1 (90 CW): clean text ("BÁO CÁO TÀI CHÍNH HỢP NHẤT...")
        # Call 2 (90 CCW): empty
        # Call 3 (180 deg): empty
        uc.ocr_engine.predict_image.side_effect = [
            [{"bbox": [10, 10, 20, 30], "text": "L", "confidence": 0.95},
             {"bbox": [10, 40, 20, 60], "text": "s", "confidence": 0.95}],
            [{"bbox": [20, 30, 250, 60], "text": "BÁO CÁO TÀI CHÍNH HỢP NHẤT", "confidence": 0.95},
             {"bbox": [20, 70, 280, 100], "text": "Cho năm tài chính kết thúc", "confidence": 0.94}],
            [],
            [],
        ]

        img = np.zeros((100, 200, 3), dtype=np.uint8)
        # Even with evaluate_all_orientations=False, noise must NOT exit early
        results, label = uc._predict_with_orientation_fallback(img, enabled=True, evaluate_all_orientations=False)

        assert label == "rotate_90_cw"
        assert len(results) == 2
        assert "BÁO CÁO TÀI CHÍNH HỢP NHẤT" in results[0]["text"]

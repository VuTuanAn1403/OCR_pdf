"""
V3.1.2 Phase E & F – Unit tests for Region Orientation and Hybrid 2-Up
"""

import pytest
from unittest.mock import MagicMock
import numpy as np

from src.ocr.application.process_page import ProcessPageUseCase


@pytest.fixture
def mock_use_case():
    mock_doc = MagicMock()
    config = {
        "profile": "balanced",
        "render_dpi": 180,
        "orientation_fallback_enabled": True,
    }
    uc = ProcessPageUseCase.__new__(ProcessPageUseCase)
    uc.config = config
    uc.ocr_engine = MagicMock()
    return uc


class TestOrientation180:
    def test_rotate_180_mapping(self, mock_use_case):
        """180-degree inverted image is detected and mapped back to upright space."""
        # 100 x 300 (height x width)
        image = np.zeros((100, 300, 3), dtype=np.uint8)

        # Call 0 (0 deg): empty
        # Call 1 (90 CW): empty
        # Call 2 (90 CCW): empty
        # Call 3 (180 deg): finds text
        mock_use_case.ocr_engine.predict_image.side_effect = [
            [],  # 0 deg
            [],  # 90 CW
            [],  # 90 CCW
            [{"bbox": [20.0, 30.0, 100.0, 70.0], "text": "Upside down text", "confidence": 0.92}],  # 180 deg
        ]

        results, orient = mock_use_case._predict_with_orientation_fallback(image, enabled=True)

        assert orient == "rotate_180"
        assert len(results) == 1
        # Inverted back:
        # width = 300, height = 100
        # mapped x0 = 300 - 100 = 200.0
        # mapped y0 = 100 - 70 = 30.0
        # mapped x1 = 300 - 20 = 280.0
        # mapped y1 = 100 - 30 = 70.0
        assert results[0]["bbox"] == [200.0, 30.0, 280.0, 70.0]

    def test_rotate_180_is_attempted_for_portrait_pages(self, mock_use_case):
        image = np.zeros((300, 200, 3), dtype=np.uint8)
        mock_use_case.ocr_engine.predict_image.side_effect = [
            [], [], [],
            [{"bbox": [20.0, 30.0, 100.0, 70.0], "text": "Portrait", "confidence": 0.92}],
        ]

        results, orient = mock_use_case._predict_with_orientation_fallback(image, enabled=True)

        assert orient == "rotate_180"
        assert results[0]["bbox"] == [100.0, 230.0, 180.0, 270.0]

    def test_upside_down_horizontal_noise_triggers_180_rotation(self, mock_use_case):
        """When 0 deg text is horizontal but has no Vietnamese words (< 12%), ROTATE_180 is evaluated."""
        image = np.zeros((200, 300, 3), dtype=np.uint8)
        # 0 deg: horizontal pseudo-words from upside down letters (vn_ratio = 0.0)
        # 90 CW: empty
        # 90 CCW: empty
        # 180: genuine Vietnamese financial report line (vn_ratio = 1.0)
        mock_use_case.ocr_engine.predict_image.side_effect = [
            [{"bbox": [10.0, 20.0, 200.0, 40.0], "text": "NJIL IHI EIN 13614", "confidence": 0.80}],
            [],
            [],
            [{"bbox": [10.0, 20.0, 200.0, 40.0], "text": "Báo cáo tài chính năm 2022", "confidence": 0.95}],
        ]

        results, orient = mock_use_case._predict_with_orientation_fallback(image, enabled=True)
        assert orient == "rotate_180"
        assert results[0]["text"] == "Báo cáo tài chính năm 2022"

    def test_clean_vietnamese_text_does_not_falsely_rotate_180(self, mock_use_case):
        """When 0 deg has valid Vietnamese text (vn_ratio >= 12%), it early-exits at 0 deg."""
        image = np.zeros((200, 300, 3), dtype=np.uint8)
        mock_use_case.ocr_engine.predict_image.side_effect = [
            [{"bbox": [10.0, 20.0, 200.0, 40.0], "text": "Báo cáo kết quả hoạt động kinh doanh", "confidence": 0.95}],
        ]

        results, orient = mock_use_case._predict_with_orientation_fallback(image, enabled=True)
        assert orient is None
        assert results[0]["text"] == "Báo cáo kết quả hoạt động kinh doanh"


class TestHybrid2Up:
    def test_hybrid_2up_splits_and_maps(self, mock_use_case):
        """Hybrid 2-Up page (left half 0 deg, right half 90 deg CW) is split and merged."""
        # 200 x 600 (height x width, aspect = 3.0 > 1.25)
        image = np.zeros((200, 600, 3), dtype=np.uint8)
        image[:, :300, 0] = 1  # Left half flag
        image[:, 300:, 0] = 2  # Right half flag

        def mock_predict(img):
            h, w = img.shape[:2]
            flag = img[0, 0, 0]
            # Left half (flag=1) at 0 deg (h=200, w=300)
            if flag == 1 and (h, w) == (200, 300):
                return [{"bbox": [20.0, 50.0, 150.0, 80.0], "text": "Left page", "confidence": 0.95}]
            # Right half (flag=2): at 0 deg returns empty, at 90 CW (h=300, w=200) returns text
            elif (h, w) == (300, 200):
                return [{"bbox": [10.0, 20.0, 180.0, 60.0], "text": "Right rotated page", "confidence": 0.90}]
            return []

        mock_use_case.ocr_engine.predict_image.side_effect = mock_predict

        results, orient = mock_use_case._predict_hybrid_2up(image, enabled=True)

        assert results is not None
        assert "hybrid_2up" in orient
        assert len(results) == 2

        # First result (left half): x within [0, 300]
        assert results[0]["text"] == "Left page"
        assert results[0]["bbox"] == [20.0, 50.0, 150.0, 80.0]

        # Second result (right half): x within [300, 600]
        assert results[1]["text"] == "Right rotated page"
        assert results[1]["bbox"][0] >= 300.0
        assert results[1]["bbox"][2] <= 600.0

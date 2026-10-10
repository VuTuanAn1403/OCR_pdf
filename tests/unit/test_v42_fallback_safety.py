from src.ocr.infrastructure.ocr.fallback_engine import FallbackEngine
import numpy as np


def test_address_correction_is_eligible_for_regional_retry():
    original = "- Địa chi/Address: Ấp 9, Xã Tân Thạch, Huyện Tỉnh Bến Tre."
    corrected = "- Địa chi/Address: Ấp 9, Xã Tân Thạch, Huyện Châu Thành, Tỉnh Bến Tre."
    assert FallbackEngine._safe_replacement(original, corrected)


def test_regional_retry_rejects_added_numbers_and_repeated_words():
    assert not FallbackEngine._safe_replacement(
        "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM",
        "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM 1.0",
    )
    assert not FallbackEngine._safe_replacement(
        "Xuất, nhập khấu thủy sản; Bán buôn thủy sản.",
        "Xuất, nhập khẩu thủy sản thủy sản; Bán buôn thủy sản.",
    )


def test_regional_retry_rejects_clause_loss():
    assert not FallbackEngine._safe_replacement(
        "Quá trình hình thành và phát triển/Establishment and development process (ngày thành lập.",
        "(ngày thành lập, thời điểm niêm yết)",
    )


def test_numeric_disagreement_requires_review_without_replacing_source():
    class Engine:
        def predict_image(self, image):
            return [{"text": "Doanh thu 1.234", "confidence": 0.98}]

    fallback = FallbackEngine(ocr_engine=Engine())
    result = fallback.reocr_crop(
        np.zeros((20, 100, 3), dtype=np.uint8), "Doanh thu 1.235", 0.70
    )
    assert result["text"] == "Doanh thu 1.235"
    assert result["improved"] is False
    assert result["needs_review"] is True

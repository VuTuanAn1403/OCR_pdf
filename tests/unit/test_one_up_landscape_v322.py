import pytest
from typing import List, Dict, Any

from src.ocr.application.process_page import ProcessPageUseCase
from src.ocr.infrastructure.layout.document_layout_engine import DocumentLayoutEngine, TABLE_HEADER_KEYWORDS
from src.ocr.infrastructure.export.manifest_exporter import PIPELINE_VERSION


class TestOneUpLandscapeV322:
    def test_pipeline_version_v322(self):
        assert PIPELINE_VERSION in ("3.2.2", "4.0.0", "4.2.0", "4.3.0")

    def test_is_glyph_noise_detects_sliced_table_scans(self):
        # 88 single character slices ('I', '1', 'l') + 7 margin stamp words
        noise_results = []
        for c in ["I", "1", "l", "E", "T", "H", "F"] * 12:
            noise_results.append({"text": c, "confidence": 0.85, "bbox": [10, 20, 20, 100]})
        for w in ["CÔNG", "TNHH", "KIỂM", "TOÁN", "PHÍA", "NAM", "STPH"]:
            noise_results.append({"text": w, "confidence": 0.90, "bbox": [550, 100, 580, 200]})

        assert len(noise_results) >= 80
        # Must detect as glyph noise so that orientation fallback triggers
        assert ProcessPageUseCase._is_glyph_noise(noise_results) is True

    def test_is_glyph_noise_preserves_legitimate_short_text(self):
        # Genuine text with normal word lengths
        genuine_results = [
            {"text": "BÁO CÁO THƯỜNG NIÊN 2022", "confidence": 0.98, "bbox": [50, 50, 300, 70]},
            {"text": "CÔNG TY CỔ PHẦN TẬP ĐOÀN SAO MAI", "confidence": 0.99, "bbox": [50, 80, 400, 100]},
            {"text": "Địa chỉ: 326 Hùng Vương, Phường Mỹ Long", "confidence": 0.95, "bbox": [50, 110, 420, 130]},
            {"text": "I. TỔNG QUAN VỀ DOANH NGHIỆP", "confidence": 0.97, "bbox": [50, 140, 350, 160]},
            {"text": "1. Thông tin chung", "confidence": 0.95, "bbox": [70, 170, 250, 190]},
        ]
        assert ProcessPageUseCase._is_glyph_noise(genuine_results) is False

    def test_orientation_candidate_score_has_no_fixed_direction_bonus(self):
        # Text evidence, rather than a fixed angle preference, decides rotation.
        cw_results = [
            {"text": "BẢN THUYẾT MINH BÁO CÁO TÀI CHÍNH HỢP NHẤT", "confidence": 0.95, "bbox": [10, 10, 200, 30]},
            {"text": "13. VAY VÀ NỢ THUÊ TÀI CHÍNH (tiếp theo)", "confidence": 0.95, "bbox": [10, 40, 200, 60]},
            {"text": "a) Vay ngắn hạn", "confidence": 0.95, "bbox": [10, 70, 100, 90]},
            {"text": "Số đầu năm", "confidence": 0.95, "bbox": [100, 70, 150, 90]},
            {"text": "Số cuối năm", "confidence": 0.95, "bbox": [160, 70, 210, 90]},
            {"text": "Ngân hàng Thương mại Cổ phần Á Châu", "confidence": 0.95, "bbox": [10, 100, 200, 120]},
        ]
        score_cw = ProcessPageUseCase._orientation_candidate_score(cw_results, label="rotate_90_cw")
        score_ccw = ProcessPageUseCase._orientation_candidate_score(cw_results, label="rotate_90_ccw")
        assert score_cw == score_ccw

    def test_financial_table_keywords_present(self):
        # Accented and unaccented financial keywords must be present
        for kw in [
            "số đầu năm", "so dau nam",
            "số cuối năm", "so cuoi nam",
            "trong năm", "trong kỳ",
            "tăng", "giảm", "tang", "giam",
            "vay ngắn hạn", "vay ngan han",
            "giá trị ghi sổ", "gia tri ghi so",
            "dự phòng", "du phong"
        ]:
            assert kw in TABLE_HEADER_KEYWORDS

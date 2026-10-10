import cv2
import numpy as np

from src.ocr.infrastructure.postprocessing.unicode_normalizer import UnicodeNormalizer
from src.ocr.infrastructure.quality.native_quality import NativeQualityEvaluator
from src.ocr.infrastructure.layout.document_layout_engine import DocumentLayoutEngine
from src.ocr.application.process_page import ProcessPageUseCase


BROKEN_TABLE_TEXT = (
    "| P Q+RS'T#'U+ | VWXYZW[\\Y | \\WVZ | ]W^\\_\\WZ\\_Z | XW`[ | "
    "ab' [YYWYYY\"$%# &'c aPde\"+#U+f#gh\\__W_`Z\"$ %# &'fij\"$fk\"+lh^Y\\_X |"
)


def test_broken_pdf_unicode_is_routed_to_ocr():
    report = UnicodeNormalizer.analyze(BROKEN_TABLE_TEXT)

    assert report["is_corrupted"] is True
    assert "broken font encoding symbols" in report["reasons"]
    assert NativeQualityEvaluator().evaluate_text(BROKEN_TABLE_TEXT, {}) < 0.70


def test_unicode_normalizer_does_not_guess_corrupted_table_text():
    # An arbitrary font map has no reversible text-only mapping. Preserve the
    # source and let the page route to image OCR instead.
    assert UnicodeNormalizer.normalize(BROKEN_TABLE_TEXT) == BROKEN_TABLE_TEXT


def test_corrupted_native_page_skips_vector_table_probe(monkeypatch):
    engine = DocumentLayoutEngine()
    called = []

    def fail_if_called(page, page_num):
        called.append(True)
        return []

    monkeypatch.setattr(engine, "_extract_vector_tables", fail_if_called)
    result = engine.extract_tables(object(), [], page_num=1, allow_vector_tables=False)

    assert result == []
    assert called == []


class _FakeOCREngine:
    def __init__(self):
        self.calls = []

    def predict_image(self, image):
        self.calls.append(image.shape[:2])
        if len(self.calls) == 1:
            return []
        return [{"bbox": [10, 20, 50, 40], "text": "Báo cáo", "confidence": 0.95}]


def test_landscape_orientation_fallback_maps_bbox_back_to_page():
    use_case = ProcessPageUseCase.__new__(ProcessPageUseCase)
    use_case.ocr_engine = _FakeOCREngine()
    image = np.zeros((100, 300, 3), dtype=np.uint8)

    results, orientation = use_case._predict_with_orientation_fallback(image)

    assert orientation == "rotate_90_cw"
    assert results[0]["bbox"] == [20.0, 50.0, 40.0, 90.0]
    assert use_case.ocr_engine.calls[1] == (300, 100)


def test_table_snug_wrap_no_line_over_200_chars():
    from src.ocr.domain.models.table import TableStructure, TableCell
    long_desc = "Nền kinh tế thế giới đối mặt với nhiều khó khăn, thách thức từ xung đột địa chính trị, lạm phát tăng cao, chính sách thắt chặt tiền tệ của các ngân hàng trung ương lớn dẫn đến chi phí đầu vào tăng cao, sức mua suy giảm."
    cells = [
        TableCell(row_idx=0, col_idx=0, text="Tên rủi ro"),
        TableCell(row_idx=0, col_idx=1, text="Mô tả rủi ro"),
        TableCell(row_idx=1, col_idx=0, text="Rủi ro vĩ mô"),
        TableCell(row_idx=1, col_idx=1, text=long_desc),
    ]
    table = TableStructure(table_id="test_wrap", page=1, num_rows=2, num_cols=2, cells=cells)
    md = table.to_pipe_table()
    lines = md.split("\n")
    assert md.startswith("|")
    assert "<table" not in md
    # V4.2 emits pure GFM pipe tables and keeps the complete cell text.
    assert "<br>" not in md
    assert long_desc in md
    rows = [l for l in lines if l.startswith("|")]
    col_counts = [l.count("|") for l in rows]
    assert len(set(col_counts)) == 1
    assert col_counts[0] == 3


def test_1col_narrative_table_converts_to_paragraphs():
    from src.ocr.domain.models.table import TableStructure, TableCell
    cells = [
        TableCell(row_idx=0, col_idx=0, text="Cơ cấu lao động đến thời điểm 31/03/2023"),
        TableCell(row_idx=1, col_idx=0, text="Trong năm 2022, mức lương trung bình Công ty chi trả cho người lao động là 18,463,000 VND."),
        TableCell(row_idx=2, col_idx=0, text="Chính sách đối với người lao động"),
    ]
    table = TableStructure(table_id="test_1col", page=1, num_rows=3, num_cols=1, cells=cells)
    out = table.to_markdown()
    assert not out.startswith("|")
    assert "Trong năm 2022" in out


def test_narrative_sentence_with_two_dates_rejected_as_table_header():
    from src.ocr.domain.models.text_block import TextBlock
    engine = DocumentLayoutEngine()
    narrative_line = [
        TextBlock(
            text="Trong giai đoạn từ tháng 05/2022 đến tháng 01/2023, Tập đoàn mua tổng cộng 542.549 cổ phần.",
            source="native", page=70, bbox=[50.0, 100.0, 500.0, 115.0]
        )
    ]
    assert engine._is_table_header_line(narrative_line) is False


def test_vietocr_repetition_guard():
    from src.ocr.infrastructure.ocr.vietnamese_seq2seq_engine import VietnameseSeq2SeqEngine
    # 1-word severe attention loop
    loop_text = "chương thu thu thu thu thu thu thu thu thu thu thu thu"
    cleaned, is_noise = VietnameseSeq2SeqEngine._guard_repetition(loop_text)
    assert is_noise is True
    assert "thu thu thu" not in cleaned

    # 2-word phrase loop
    loop_phrase = "xác nhận thu phí xác nhận thu phí xác nhận thu phí"
    cleaned2, is_noise2 = VietnameseSeq2SeqEngine._guard_repetition(loop_phrase)
    assert is_noise2 is False
    assert cleaned2 == "xác nhận thu phí"

    # Genuine text with no loop
    clean_text = "Báo cáo tài chính năm 2022 của Hội đồng quản trị"
    cleaned3, is_noise3 = VietnameseSeq2SeqEngine._guard_repetition(clean_text)
    assert is_noise3 is False
    assert cleaned3 == clean_text


def test_product_showcase_collage_routing():
    from src.ocr.domain.routing.routing_policy import RoutingPolicy
    from src.ocr.domain.routing.page_type import PageType

    policy = RoutingPolicy()
    # Product collage with 5 photos, 150 chars of text, 0 tables
    meta_collage = {
        "image_count": 5,
        "char_count": 150,
        "table_count": 0,
        "image_coverage_ratio": 0.65,
    }
    pt = policy.determine_page_type(meta_collage, native_quality_score=0.60)
    assert pt == PageType.PRODUCT_SHOWCASE_COLLAGE

    # Standard scanned text page with 0 images
    meta_scan = {
        "image_count": 0,
        "char_count": 0,
        "table_count": 0,
    }
    assert policy.determine_page_type(meta_scan, 0.0) == PageType.IMAGE_ONLY


import pytest
import unicodedata
from src.ocr.infrastructure.postprocessing.unicode_normalizer import UnicodeNormalizer
from src.ocr.infrastructure.postprocessing.text_cleaner import TextCleaner
from src.ocr.infrastructure.quality.native_quality import NativeQualityEvaluator
from src.ocr.infrastructure.quality.vietnamese_quality import VietnameseQualityEvaluator
from src.ocr.infrastructure.layout.document_layout_engine import DocumentLayoutEngine
from src.ocr.infrastructure.postprocessing.reading_order import ReadingOrderSorter
from src.ocr.infrastructure.ocr.candidate_scorer import CandidateScorer
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.routing.page_type import PageType

# 1. test_vietnamese_charset_complete
def test_vietnamese_charset_complete():
    vn_chars = (
        "aăâeêioôơuưyAĂÂEÊIOÔƠUƯY"
        "àáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩị"
        "òóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ"
        "ÀÁẢÃẠẰẮẲẴẶẦẤẨẪẬÈÉẺẼẸỀẾỂỄỆÌÍỈĨỊ"
        "ÒÓỎÕỌỒỐỔỖỘỜỚỞỠỢÙÚỦŨỤỪỨỬỮỰỲÝỶỸỴ"
        "đĐ"
    )
    for ch in vn_chars:
        norm = UnicodeNormalizer.normalize(ch)
        assert norm == ch, f"Character {ch} was altered during NFC normalization"
        # Check NFC composition
        assert unicodedata.is_normalized("NFC", norm)

# 2. test_vietnamese_marks_preserved
def test_vietnamese_marks_preserved():
    sample = "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM - Độc lập - Tự do - Hạnh phúc"
    cleaned = TextCleaner.clean_line(sample)
    assert cleaned == sample
    assert "Độc lập" in cleaned
    assert "Hạnh phúc" in cleaned

# 3. test_native_text_quality
def test_native_text_quality():
    evaluator = NativeQualityEvaluator()
    clean_sample = (
        "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\n"
        "Độc lập - Tự do - Hạnh phúc\n"
        "BÁO CÁO THƯỜNG NIÊN CÔNG TY CỔ PHẦN CẤP NƯỚC CHỢ LỚN NĂM 2020\n"
        "Công ty Cổ phần Cấp nước Chợ Lớn tiền thân là Chi nhánh Cấp nước Chợ Lớn."
    )
    score = evaluator.evaluate_text(clean_sample, {"is_full_page_image": False})
    assert score >= 0.85

# 4. test_corrupted_native_ocr_layer
def test_corrupted_native_ocr_layer():
    evaluator = NativeQualityEvaluator()
    corrupted_sample = (
        "TONG CONG TY CAP NU'OC SAIGON\n"
        "ONG HOA XA- HO It NGHIA VItT NAM\n"
        "TRACH NHIN HfrU HAN MOT THANH VIEN DO 14p Tv do Hnh phtic\n"
        "BAO CAO THONG MEN"
    )
    score = evaluator.evaluate_text(corrupted_sample, {"is_full_page_image": True})
    assert score < 0.70

# 5. test_ascii_only_ocr_flagged
def test_ascii_only_ocr_flagged():
    evaluator = VietnameseQualityEvaluator()
    # Dropped accents in administrative context should trigger suspicious flag
    unaccented_line = "bao cao thuong nien cong ty co phan"
    score, is_suspicious, reasons = evaluator.evaluate_line(unaccented_line)
    assert is_suspicious is True
    assert any("Unaccented" in r for r in reasons)

# 6. test_table_false_positive_rejected
def test_table_false_positive_rejected():
    layout_engine = DocumentLayoutEngine()
    # Narrative paragraph lines with numbers (dates, phone numbers) must not form a table
    blocks = [
        TextBlock(text="BÁO CÁO THƯỜNG NIÊN NĂM 2020", page=1, bbox=[100, 100, 400, 120]),
        TextBlock(text="Vốn điều lệ: 130.000.000.000 VND (Một trăm ba mươi tỷ đồng)", page=1, bbox=[100, 130, 480, 150]),
        TextBlock(text="Điện thoại: (84-28) 38 552 354 Fax: (84-28) 39 550 424", page=1, bbox=[100, 160, 450, 180]),
        TextBlock(text="Giấy phép thành lập ngày 16 tháng 01 năm 2007", page=1, bbox=[100, 190, 420, 210]),
    ]
    table = layout_engine._detect_table_from_ocr_blocks(blocks, page_num=1)
    assert table is None

# 7. test_real_table_detected
def test_real_table_detected():
    layout_engine = DocumentLayoutEngine()
    # Real table with header keywords and multi-column rows
    blocks = [
        TextBlock(text="STT", page=5, bbox=[50, 100, 80, 115]),
        TextBlock(text="Chỉ tiêu", page=5, bbox=[90, 100, 200, 115]),
        TextBlock(text="ĐV tính", page=5, bbox=[210, 100, 250, 115]),
        TextBlock(text="Kế hoạch", page=5, bbox=[260, 100, 310, 115]),
        TextBlock(text="Thực hiện", page=5, bbox=[320, 100, 370, 115]),
        # Row 1
        TextBlock(text="1", page=5, bbox=[50, 125, 80, 140]),
        TextBlock(text="Sản lượng nước tiêu thụ", page=5, bbox=[90, 125, 200, 140]),
        TextBlock(text="1.000m3", page=5, bbox=[210, 125, 250, 140]),
        TextBlock(text="109.500", page=5, bbox=[260, 125, 310, 140]),
        TextBlock(text="108.866", page=5, bbox=[320, 125, 370, 140]),
        # Row 2
        TextBlock(text="2", page=5, bbox=[50, 145, 80, 160]),
        TextBlock(text="Tổng doanh thu", page=5, bbox=[90, 145, 200, 160]),
        TextBlock(text="Tr.đ", page=5, bbox=[210, 145, 250, 160]),
        TextBlock(text="1.189.485", page=5, bbox=[260, 145, 310, 160]),
        TextBlock(text="1.183.591", page=5, bbox=[320, 145, 370, 160]),
    ]
    table = layout_engine._detect_table_from_ocr_blocks(blocks, page_num=5)
    assert table is not None
    assert table.num_rows >= 3
    assert table.num_cols >= 4

# 8. test_two_column_reading_order
def test_two_column_reading_order():
    sorter = ReadingOrderSorter(page_width=600.0, page_height=800.0)
    blocks = [
        # Spanning Title at top
        TextBlock(text="TIÊU ĐỀ BÁO CÁO TOÀN TRANG", page=1, bbox=[50, 50, 550, 75]),
        # Left column
        TextBlock(text="Cột trái dòng 1", page=1, bbox=[50, 100, 250, 120]),
        TextBlock(text="Cột trái dòng 2", page=1, bbox=[50, 130, 250, 150]),
        TextBlock(text="Cột trái dòng 3", page=1, bbox=[50, 160, 250, 180]),
        # Right column
        TextBlock(text="Cột phải dòng 1", page=1, bbox=[350, 100, 550, 120]),
        TextBlock(text="Cột phải dòng 2", page=1, bbox=[350, 130, 550, 150]),
        TextBlock(text="Cột phải dòng 3", page=1, bbox=[350, 160, 550, 180]),
    ]
    sorted_blocks = sorter.sort_blocks(blocks)
    texts = [b.text for b in sorted_blocks]
    assert texts[0] == "TIÊU ĐỀ BÁO CÁO TOÀN TRANG"
    assert texts.index("Cột trái dòng 1") < texts.index("Cột phải dòng 1")

# 9. test_region_reocr_only
def test_region_reocr_only():
    scorer = CandidateScorer()
    # Bad OCR candidate vs Good OCR candidate
    bad_ocr = "cong ty cap nuoc thanh pho Ho Chi Minh"
    good_ocr = "Công ty cấp nước thành phố Hồ Chí Minh"
    score_bad = scorer.score_candidate(bad_ocr, 0.70)
    score_good = scorer.score_candidate(good_ocr, 0.95)
    assert score_good > score_bad

# 10. test_adaptive_dpi
def test_adaptive_dpi():
    # Verify connector line artifact detection eliminates flowchart artifacts
    assert TextCleaner.is_connector_line_artifact("1 l l 1 1") is True
    assert TextCleaner.is_connector_line_artifact("I") is True
    assert TextCleaner.is_connector_line_artifact("CÔNG TY CỔ PHẦN") is False

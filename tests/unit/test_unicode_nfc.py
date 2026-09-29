import unicodedata
import pytest
from src.ocr.infrastructure.postprocessing.unicode_normalizer import UnicodeNormalizer

def test_unicode_nfc_normalization():
    # Decomposed Vietnamese (NFD) string
    decomposed = unicodedata.normalize("NFD", "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM")
    assert unicodedata.is_normalized("NFC", decomposed) is False

    normalized = UnicodeNormalizer.normalize(decomposed)
    assert unicodedata.is_normalized("NFC", normalized) is True
    assert normalized == "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM"

def test_diacritics_preservation():
    test_str = "ă â ê ô ơ ư đ Á À Ả Ã Ạ Ắ Ằ Ẳ Ẵ Ặ Ế Ề Ể Ễ Ệ Ố Ồ Ổ Ỗ Ộ Ứ Ừ Ử Ữ Ự Ý Ỳ Ỷ Ỹ Ỵ"
    norm = UnicodeNormalizer.normalize(test_str)
    assert norm == test_str

def test_special_spaces_and_hyphens_cleaned():
    raw = "Báo\u00a0cáo\u200b thường\u00adniên"
    norm = UnicodeNormalizer.normalize(raw)
    assert norm == "Báo cáo thườngniên"

def test_vietnamese_font_cmap_control_characters():
    # \x17, \x19, \x1d, \x07, \x04 recovery
    raw_lss = "Tham gia, phối hợp với Ban \x17iều hành trong việc thực hiện kiểm tra, giám sát, \x17ánh giá hoạt \x17ộng SXKD tại các \x17ơn vị."
    expected_lss = "Tham gia, phối hợp với Ban Điều hành trong việc thực hiện kiểm tra, giám sát, đánh giá hoạt động SXKD tại các đơn vị."
    assert UnicodeNormalizer.normalize(raw_lss) == expected_lss

    raw_medium = "\x19iều hành và \x19ồng quản trị"
    assert UnicodeNormalizer.normalize(raw_medium) == "Điều hành và Đồng quản trị"

    raw_mig = "Tăng vốn \x04iều lệ, chuyển \x04ổi mô hình với số vốn \x07iều lệ 300 tỷ \x07ồng."
    expected_mig = "Tăng vốn điều lệ, chuyển đổi mô hình với số vốn điều lệ 300 tỷ đồng."
    assert UnicodeNormalizer.normalize(raw_mig) == expected_mig

def test_indesign_typography_corruption():
    raw_quote = "Bất „ộng sản và hoạt „ộng tại các khu công nghiệp."
    assert UnicodeNormalizer.normalize(raw_quote) == "Bất động sản và hoạt động tại các khu công nghiệp."

    raw_oe = "Œường bộ, Œường sắt, Œường biển"
    assert UnicodeNormalizer.normalize(raw_oe) == "Đường bộ, Đường sắt, Đường biển"

def test_context_aware_missing_d_recovery():
    raw_context = "Quy ịnh của tập oàn về thẩm ịnh bất ộng sản và đánh giá hoạt ộng."
    expected = "Quy định của tập đoàn về thẩm định bất động sản và đánh giá hoạt động."
    assert UnicodeNormalizer.normalize(raw_context) == expected

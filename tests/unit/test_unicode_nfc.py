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

import pytest
from src.ocr.infrastructure.quality.native_quality import NativeQualityEvaluator

def test_clean_native_text_scores_high():
    evaluator = NativeQualityEvaluator()
    clean_text = (
        "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\n"
        "Độc lập - Tự do - Hạnh phúc\n"
        "BÁO CÁO THƯỜNG NIÊN\n"
        "CÔNG TY CỔ PHẦN CẤP NƯỚC CHỢ LỚN\n"
        "Năm 2020 báo cáo tình hình hoạt động kinh doanh và tài chính."
    )
    meta = {"is_full_page_image": False}
    score = evaluator.evaluate_text(clean_text, meta)
    assert score >= 0.90, f"Expected high score for clean text, got {score}"

def test_corrupted_clw_native_text_scores_low():
    evaluator = NativeQualityEvaluator()
    corrupted_text = (
        "TONG CONG TY CAP NU'OC SAIGON \n"
        "ONG HOA XA- HO \n"
        "TRACH NHIN HfrU HAN MOT THANH VIEN  DO 14p  Tv do  Hnh phtic \n"
        "CONG TY CO PHAN CAP NU& CHO LON \n"
        "BAO CAO THONG MEN \n"
        "NAM 2020 \n"
        "(Theo Thong tu. so 96/2020/TT-BTC ngay 16 thang 11 nam 2020 czia B Tai chinh \n"
        "&tong den co/1g bO thong tin tren thi truemg chieng khocin)"
    )
    meta = {"is_full_page_image": True}
    score = evaluator.evaluate_text(corrupted_text, meta)
    assert score < 0.70, f"Expected low score for corrupted CLW layer, got {score}"

def test_empty_or_too_short_text():
    evaluator = NativeQualityEvaluator()
    assert evaluator.evaluate_text("", {}) == 0.0
    assert evaluator.evaluate_text("   \n\t", {}) == 0.0

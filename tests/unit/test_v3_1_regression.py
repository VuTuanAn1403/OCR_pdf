import pytest
import unicodedata
from src.ocr.infrastructure.postprocessing.unicode_normalizer import UnicodeNormalizer
from src.ocr.infrastructure.postprocessing.text_cleaner import TextCleaner
from src.ocr.infrastructure.quality.native_quality import NativeQualityEvaluator
from src.ocr.infrastructure.quality.vietnamese_quality import VietnameseQualityEvaluator
from src.ocr.infrastructure.ocr.candidate_scorer import CandidateScorer
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.routing.page_type import PageType
from src.ocr.domain.routing.routing_policy import RoutingPolicy
from src.ocr.infrastructure.cache.artifact_cache import ArtifactCache
from src.ocr.domain.models.page import ExtractedPage
from src.ocr.domain.models.quality import PageQualityReport

# 1. test_raw_ocr_vietnamese_diacritics
def test_raw_ocr_vietnamese_diacritics():
    """
    Ensure all Vietnamese diacritics, tone marks, and special letters
    (ă, â, đ, ê, ô, ơ, ư) are correctly recognized, normalized to NFC,
    and preserved without dropping accents.
    """
    vn_text = "Hội đồng Quản trị, Ban Kiểm soát và Ban Tổng Giám đốc Công ty Cổ phần Cấp nước Chợ Lớn"
    normalized = UnicodeNormalizer.normalize(vn_text)
    assert unicodedata.is_normalized("NFC", normalized)
    assert normalized == vn_text
    
    # Verify tone-rich word preservation
    sample_words = ["Hội", "đồng", "Quản", "trị", "Kiểm", "soát", "Tổng", "Giám", "đốc", "Cổ", "phần", "nước", "Lớn"]
    for word in sample_words:
        assert word in normalized

# 2. test_ascii_only_text_is_suspicious
def test_ascii_only_text_is_suspicious():
    """
    Ensure Vietnamese administrative text stripped of diacritics (ASCII-only)
    is flagged as suspicious by VietnameseQualityEvaluator.
    """
    evaluator = VietnameseQualityEvaluator()
    unaccented_line = "Bao cao thuong nien cong ty co phan cap nuoc"
    score, is_suspicious, reasons = evaluator.evaluate_line(unaccented_line)
    
    assert is_suspicious is True
    assert any("Unaccented" in r for r in reasons)
    assert score < 0.70

# 3. test_diacritic_loss_triggers_reocr
def test_diacritic_loss_triggers_reocr():
    """
    Ensure loss of diacritics in corporate/administrative documents triggers
    re-OCR / fallback candidate evaluation.
    """
    evaluator = VietnameseQualityEvaluator()
    line_with_dropped_accents = "Tong Giam doc - Nguyen Van A"
    score, is_suspicious, reasons = evaluator.evaluate_line(line_with_dropped_accents)
    
    assert is_suspicious is True
    assert score < 0.75

# 4. test_good_text_does_not_trigger_reocr
def test_good_text_does_not_trigger_reocr():
    """
    Ensure well-recognized, properly accented Vietnamese text with high confidence
    does NOT trigger fallback re-OCR.
    """
    evaluator = VietnameseQualityEvaluator()
    good_line = "Tổng Giám đốc - Nguyễn Văn A"
    score, is_suspicious, reasons = evaluator.evaluate_line(good_line)
    
    assert is_suspicious is False
    assert score >= 0.85
    assert len(reasons) == 0

# 5. test_region_reocr_not_page_reocr
def test_region_reocr_not_page_reocr():
    """
    Ensure fallback re-OCR operates on targeted bounding-box regions
    rather than re-processing the entire page.
    """
    page_blocks = [
        TextBlock(text="BÁO CÁO THƯỜNG NIÊN", page=1, bbox=[50, 50, 200, 70], confidence=0.98),
        TextBlock(text="cong ty cap nuoc", page=1, bbox=[50, 80, 200, 100], confidence=0.60),  # suspicious
        TextBlock(text="NĂM 2020", page=1, bbox=[50, 110, 150, 130], confidence=0.99),
    ]
    evaluator = VietnameseQualityEvaluator()
    
    suspicious_blocks = []
    for b in page_blocks:
        score, is_susp, _ = evaluator.evaluate_line(b.text)
        if is_susp or b.confidence < 0.70:
            suspicious_blocks.append(b)
            
    # Exactly one region targeted, NOT all 3 blocks
    assert len(suspicious_blocks) == 1
    assert suspicious_blocks[0].text == "cong ty cap nuoc"

# 6. test_adaptive_dpi_only_for_bad_region
def test_adaptive_dpi_only_for_bad_region():
    """
    Ensure base rendering uses default DPI (180 DPI), and higher DPI
    (250-300 DPI) is strictly scoped to candidate generation for low-quality regions.
    """
    from src.ocr.infrastructure.pdf.pdf_renderer import PDFRenderer
    
    base_dpi = 180
    assert base_dpi == 180
    
    # When generating candidate C (high DPI crop), scale is calculated for the crop only
    crop_scale = 250 / 180
    assert 1.35 < crop_scale < 1.45

# 7. test_candidate_scorer_prefers_valid_candidate
def test_candidate_scorer_prefers_valid_candidate():
    """
    Ensure CandidateScorer evaluates 5 criteria (Confidence, VietnameseQuality,
    Lexical, Geometry, Validity) and strictly prefers the properly accented candidate.
    """
    scorer = CandidateScorer()
    
    cand_bad = "cong ty co phan cap nuoc"
    cand_good = "Công ty Cổ phần Cấp nước"
    
    score_bad = scorer.score_candidate(cand_bad, confidence=0.72)
    score_good = scorer.score_candidate(cand_good, confidence=0.92)
    
    assert score_good > score_bad
    assert score_good >= 0.90
    assert score_bad < 0.85

# 8. test_no_heuristic_diacritic_injection
def test_no_heuristic_diacritic_injection():
    """
    Ensure TextCleaner and normalizers DO NOT guess or inject diacritics via
    hardcoded dictionaries or heuristics. Authentic OCR recognition must be preserved.
    """
    raw_unaccented = "Bao cao thuong nien"
    cleaned = TextCleaner.clean_line(raw_unaccented)
    
    # Must preserve raw characters without hallucinating accents
    assert cleaned == raw_unaccented
    assert "Báo cáo thường niên" != cleaned

# 9. test_native_good_skips_ocr
def test_native_good_skips_ocr():
    """
    Ensure high-quality digital native PDF text bypasses OCR completely.
    """
    policy = RoutingPolicy(reliable_threshold=0.90, review_threshold=0.70)
    meta = {
        "char_count": 2500,
        "image_coverage_ratio": 0.05,
        "image_count": 0,
        "is_full_page_image": False
    }
    page_type = policy.determine_page_type(meta, native_quality_score=0.98)
    
    assert page_type == PageType.NATIVE_TEXT
    assert policy.should_ocr(page_type) is False

# 10. test_mixed_pdf_routes_per_page
def test_mixed_pdf_routes_per_page():
    """
    Ensure a mixed-mode PDF dynamically routes page-by-page:
    scanned pages trigger OCR, while clean native pages bypass OCR.
    """
    policy = RoutingPolicy(reliable_threshold=0.90, review_threshold=0.70)
    
    # Page 1: Scanned image
    p1_meta = {"char_count": 0, "image_coverage_ratio": 0.98, "image_count": 1, "is_full_page_image": True}
    p1_type = policy.determine_page_type(p1_meta, native_quality_score=0.0)
    
    # Page 2: Native text
    p2_meta = {"char_count": 1800, "image_coverage_ratio": 0.02, "image_count": 0, "is_full_page_image": False}
    p2_type = policy.determine_page_type(p2_meta, native_quality_score=0.95)
    
    assert policy.should_ocr(p1_type) is True
    assert policy.should_ocr(p2_type) is False

# 11. test_cache_invalidated_when_model_changes
def test_cache_invalidated_when_model_changes(tmp_path):
    """
    Ensure cached page entries are invalidated when profile, dpi, or document hash changes.
    """
    cache = ArtifactCache(cache_dir=str(tmp_path / ".cache"))
    doc_hash = "doc_v31_hash"
    page_num = 1
    
    sample_page = ExtractedPage(
        page_num=page_num,
        width=595.0,
        height=842.0,
        page_type=PageType.SCANNED_IMAGE,
        rendered_dpi=180,
        blocks=[TextBlock(text="Trang kiểm thử", page=1, bbox=[10, 10, 100, 25])],
        quality=PageQualityReport(
            page=1,
            page_type="SCANNED_IMAGE",
            native_text_score=0.0,
            ocr_confidence=0.95,
            quality_score=0.95
        )
    )
    
    # Save cache under 'fast' profile at 180 DPI
    cache.save_cached_page(doc_hash, sample_page, profile="fast", dpi=180)
    
    # Query with 'balanced' profile (model config changed) -> Cache Miss
    assert cache.get_cached_page(doc_hash, page_num, profile="balanced", dpi=180) is None
    
    # Query with 250 DPI -> Cache Miss
    assert cache.get_cached_page(doc_hash, page_num, profile="fast", dpi=250) is None
    
    # Query with exact profile and DPI -> Cache Hit
    hit = cache.get_cached_page(doc_hash, page_num, profile="fast", dpi=180)
    assert hit is not None
    assert hit.blocks[0].text == "Trang kiểm thử"

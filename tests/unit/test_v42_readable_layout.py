from src.ocr.application.process_page import ProcessPageUseCase


def test_readable_unaccented_page_can_skip_rotation():
    lines = [{"text": "Annual report and financial statement", "confidence": 0.95} for _ in range(25)]
    assert ProcessPageUseCase._is_readable_ocr_layout(lines)


def test_sparse_or_low_confidence_page_keeps_orientation_search():
    sparse = [{"text": "fragment", "confidence": 0.99} for _ in range(10)]
    uncertain = [{"text": "Annual report and financial statement", "confidence": 0.50} for _ in range(25)]
    assert not ProcessPageUseCase._is_readable_ocr_layout(sparse)
    assert not ProcessPageUseCase._is_readable_ocr_layout(uncertain)

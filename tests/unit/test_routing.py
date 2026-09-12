import pytest
from src.ocr.domain.routing.page_type import PageType
from src.ocr.domain.routing.routing_policy import RoutingPolicy

def test_routing_native_clean():
    policy = RoutingPolicy(reliable_threshold=0.90, review_threshold=0.70)
    meta = {
        "char_count": 1500,
        "image_coverage_ratio": 0.05,
        "image_count": 1,
        "is_full_page_image": False
    }
    page_type = policy.determine_page_type(meta, native_quality_score=0.96)
    assert page_type == PageType.NATIVE_TEXT
    assert policy.should_ocr(page_type) is False

def test_routing_corrupted_ocr_layer():
    policy = RoutingPolicy(reliable_threshold=0.90, review_threshold=0.70)
    meta = {
        "char_count": 2300,
        "image_coverage_ratio": 0.90,
        "image_count": 1,
        "is_full_page_image": True
    }
    page_type = policy.determine_page_type(meta, native_quality_score=0.45)
    assert page_type == PageType.NATIVE_TEXT_LOW_QUALITY
    assert policy.should_ocr(page_type) is True

def test_routing_scanned_no_text():
    policy = RoutingPolicy(reliable_threshold=0.90, review_threshold=0.70)
    meta = {
        "char_count": 5,
        "image_coverage_ratio": 0.95,
        "image_count": 1,
        "is_full_page_image": True
    }
    page_type = policy.determine_page_type(meta, native_quality_score=0.0)
    assert page_type == PageType.SCANNED_IMAGE
    assert policy.should_ocr(page_type) is True

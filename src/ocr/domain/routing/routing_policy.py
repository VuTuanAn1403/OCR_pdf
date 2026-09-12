from typing import Dict, Any
from src.ocr.domain.routing.page_type import PageType

class RoutingPolicy:
    def __init__(self, reliable_threshold: float = 0.90, review_threshold: float = 0.70):
        self.reliable_threshold = reliable_threshold
        self.review_threshold = review_threshold

    def determine_page_type(self, meta: Dict[str, Any], native_quality_score: float) -> PageType:
        """
        Determines the PageType based on page geometry, image coverage, and native text quality score.
        """
        char_count = meta.get("char_count", 0)
        image_coverage = meta.get("image_coverage_ratio", 0.0)
        image_count = meta.get("image_count", 0)
        is_full_page_image = meta.get("is_full_page_image", False)
        table_count = meta.get("table_count", 0)

        # Scanned page with no or almost no text
        if char_count < 30:
            if image_count > 0:
                return PageType.SCANNED_IMAGE
            return PageType.IMAGE_ONLY

        # High quality native text
        if native_quality_score >= self.reliable_threshold:
            if is_full_page_image:
                # Could be a clean OCR layer or native text over a background image
                return PageType.NATIVE_TEXT
            if image_coverage > 0.40 and image_count > 0:
                return PageType.MIXED
            return PageType.NATIVE_TEXT

        # Corrupted / low-quality native text layer (e.g. old OCR with broken accents like in CLW)
        if native_quality_score < self.review_threshold:
            return PageType.NATIVE_TEXT_LOW_QUALITY

        # In-between: review range (0.70 <= score < 0.90)
        if image_coverage > 0.50 or is_full_page_image:
            return PageType.NATIVE_TEXT_LOW_QUALITY
        return PageType.MIXED

    def should_ocr(self, page_type: PageType) -> bool:
        """
        Returns True if the page should be processed by an OCR engine.
        """
        return page_type in (
            PageType.SCANNED_IMAGE,
            PageType.NATIVE_TEXT_LOW_QUALITY,
            PageType.IMAGE_ONLY,
            PageType.MIXED
        )

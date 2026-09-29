"""
V3.1.2 Phase B – Unit tests for Mixed Page Content Composition

Tests:
1. Native text + image region: native text is extracted directly, image region is OCR'd.
2. Coordinate mapping preserves PDF point space.
3. Duplicate suppression: OCR text overlapping native clean text is suppressed (spec §17).
4. Full-page background image with clean native text skips OCR (spec §18).
5. EvidenceBlock intermediate representation is populated with provenance.
"""

import pytest
from unittest.mock import MagicMock, patch
import numpy as np

from src.ocr.domain.models.region import Region, BoundingBox, ContentType
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.models.evidence_block import EvidenceBlock
from src.ocr.domain.routing.page_type import PageType
from src.ocr.application.process_page import ProcessPageUseCase


@pytest.fixture
def mock_use_case():
    mock_doc = MagicMock()
    mock_doc.__len__.return_value = 5
    config = {
        "profile": "balanced",
        "render_dpi": 180,
        "batch_size": 16,
        "native_review_threshold": 0.70,
        "fallback_enabled": False,
        "orientation_fallback_enabled": False,
    }
    with patch("src.ocr.application.process_page.PDFInspector"), \
         patch("src.ocr.application.process_page.PDFRenderer"), \
         patch("src.ocr.application.process_page.NativeTextExtractor"), \
         patch("src.ocr.application.process_page.VietnameseSeq2SeqEngine"), \
         patch("src.ocr.application.process_page.OCRQualityEvaluator"), \
         patch("src.ocr.application.process_page.DocumentLayoutEngine"), \
         patch("src.ocr.application.process_page.PageContentAnalyzer"), \
         patch("src.ocr.application.process_page.FallbackEngine"), \
         patch("src.ocr.application.process_page.ReocrRegionUseCase"):
        uc = ProcessPageUseCase(mock_doc, config, "test_hash")
    return uc


class TestExtractImageRegions:
    def test_image_region_ocr_extracted_and_mapped(self, mock_use_case):
        """Image region containing text is cropped and OCR'd with coordinates mapped."""
        mock_page = MagicMock()
        mock_page.rect = MagicMock(width=600.0, height=800.0)

        region = Region(
            region_id="p1_r1",
            bbox=BoundingBox(x0=50.0, y0=400.0, x1=350.0, y1=600.0),  # 300x200 pt
            content_type=ContentType.IMAGE,
            source="image",
        )
        native_blocks = [
            TextBlock(
                text="Native paragraph above image",
                bbox=[50.0, 50.0, 550.0, 150.0],
                source="native",
                page=1,
            )
        ]

        # Mock RegionCropper
        fake_crop = np.zeros((200, 300, 3), dtype=np.uint8)
        with patch(
            "src.ocr.infrastructure.pdf.region_cropper.RegionCropper.crop_page_region",
            return_value=(fake_crop, [48.0, 398.0, 352.0, 602.0])
        ):
            # Mock OCR results
            mock_use_case.ocr_engine.predict_image.return_value = [
                {
                    "bbox": [10.0, 10.0, 200.0, 40.0],
                    "text": "Báo cáo tài chính quý 4",
                    "confidence": 0.95,
                }
            ]
            mock_use_case.ocr_quality.evaluate_ocr_result.return_value = (0.95, False, [])

            extracted = mock_use_case._extract_image_regions(
                mock_page, 1, [region], native_blocks, 180
            )

            assert len(extracted) == 1
            b = extracted[0]
            assert b.source == "image_ocr"
            assert "Báo cáo tài chính quý 4" in b.text
            # Coordinates should be mapped to page point space around [50, 400]
            assert b.bbox[0] >= 48.0
            assert b.bbox[1] >= 398.0

    def test_duplicate_ocr_overlapping_native_is_suppressed(self, mock_use_case):
        """If OCR detects text that overlaps clean native text, OCR is suppressed (spec §17)."""
        mock_page = MagicMock()
        mock_page.rect = MagicMock(width=600.0, height=800.0)

        region = Region(
            region_id="p1_r1",
            bbox=BoundingBox(x0=50.0, y0=50.0, x1=550.0, y1=200.0),
            content_type=ContentType.IMAGE,
        )
        # Native block at the exact same location
        native_blocks = [
            TextBlock(
                text="Clean native text",
                bbox=[50.0, 50.0, 550.0, 200.0],
                source="native",
                page=1,
            )
        ]

        fake_crop = np.zeros((150, 500, 3), dtype=np.uint8)
        with patch(
            "src.ocr.infrastructure.pdf.region_cropper.RegionCropper.crop_page_region",
            return_value=(fake_crop, [48.0, 48.0, 552.0, 202.0])
        ):
            mock_use_case.ocr_engine.predict_image.return_value = [
                {
                    "bbox": [10.0, 10.0, 450.0, 50.0],
                    "text": "Clean native text OCR duplicate",
                    "confidence": 0.90,
                }
            ]
            mock_use_case.ocr_quality.evaluate_ocr_result.return_value = (0.90, False, [])

            extracted = mock_use_case._extract_image_regions(
                mock_page, 1, [region], native_blocks, 180
            )
            # Should be suppressed because it overlaps native block
            assert len(extracted) == 0

    def test_full_page_background_image_skips_ocr(self, mock_use_case):
        """An image already covered by clean native text needs no duplicate OCR."""
        mock_page = MagicMock()
        mock_page.rect = MagicMock(width=600.0, height=800.0)

        full_page_region = Region(
            region_id="p1_bg",
            bbox=BoundingBox(x0=10.0, y0=10.0, x1=590.0, y1=790.0),  # ~95% page area
            content_type=ContentType.IMAGE,
        )
        native_blocks = [TextBlock(text="Page content", bbox=[10, 10, 590, 790], source="native", page=1)]

        extracted = mock_use_case._extract_image_regions(
            mock_page, 1, [full_page_region], native_blocks, 180
        )
        assert len(extracted) == 0
        assert not mock_use_case.ocr_engine.predict_image.called

    def test_full_page_image_with_only_footer_is_ocrd(self, mock_use_case):
        """A sparse native footer must not hide scanned text above it."""
        mock_page = MagicMock()
        mock_page.rect = MagicMock(width=600.0, height=800.0)
        region = Region(
            region_id="p1_scan",
            bbox=BoundingBox(x0=20.0, y0=10.0, x1=580.0, y1=760.0),
            content_type=ContentType.IMAGE,
        )
        footer = TextBlock(text="Page 93", bbox=[450, 770, 550, 790], source="native", page=1)
        fake_crop = np.zeros((750, 560, 3), dtype=np.uint8)
        with patch(
            "src.ocr.infrastructure.pdf.region_cropper.RegionCropper.crop_page_region",
            return_value=(fake_crop, [20.0, 10.0, 580.0, 760.0]),
        ):
            mock_use_case.ocr_engine.predict_image.return_value = [
                {"bbox": [50.0, 50.0, 250.0, 70.0], "text": "Ý kiến của Kiểm toán viên", "confidence": 0.91}
            ]
            mock_use_case.ocr_quality.evaluate_ocr_result.return_value = (0.91, False, [])
            extracted = mock_use_case._extract_image_regions(mock_page, 1, [region], [footer], 180)
        assert len(extracted) == 1
        assert extracted[0].region_id == "p1_scan"


class TestEvidenceBlockPopulated:
    def test_evidence_blocks_in_extracted_page(self, mock_use_case):
        """ExtractedPage carries evidence_blocks with correct provenance."""
        mock_router = MagicMock()
        mock_router.execute.return_value = (PageType.NATIVE_TEXT, 0.98)

        mock_use_case.inspector.inspect_page.return_value = {
            "page_num": 1,
            "width": 600.0,
            "height": 800.0,
            "page_area": 480000.0,
            "char_count": 500,
            "image_rects": [],
            "native_extraction_failed": False,
        }
        mock_use_case.content_analyzer.analyze.return_value = [
            Region(
                region_id="p1_r1",
                bbox=BoundingBox(x0=50.0, y0=50.0, x1=550.0, y1=100.0),
                content_type=ContentType.TEXT,
            )
        ]
        mock_use_case.native_extractor.extract_blocks.return_value = [
            TextBlock(
                text="Chủ tịch Hội đồng Quản trị",
                bbox=[50.0, 50.0, 550.0, 80.0],
                source="native",
                page=1,
            )
        ]
        mock_use_case.layout_engine.extract_tables.return_value = []

        mock_page = MagicMock()
        mock_page.rect = MagicMock(width=600.0, height=800.0)
        mock_use_case.doc.__getitem__.return_value = mock_page

        extracted_page = mock_use_case.execute(1, 1, mock_router, resume=False)

        assert extracted_page.page_type == PageType.NATIVE_TEXT
        assert len(extracted_page.blocks) == 1
        assert len(extracted_page.evidence_blocks) == 1

        ev = extracted_page.evidence_blocks[0]
        assert ev.source == "native"
        assert ev.content_type == ContentType.TEXT
        assert ev.text == "Chủ tịch Hội đồng Quản trị"
        assert ev.reading_order == 1

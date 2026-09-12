import pytest
import shutil
from pathlib import Path
from src.ocr.infrastructure.cache.artifact_cache import ArtifactCache
from src.ocr.domain.models.page import ExtractedPage
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.models.quality import PageQualityReport
from src.ocr.domain.routing.page_type import PageType

def test_cache_save_and_retrieve(tmp_path):
    cache = ArtifactCache(cache_dir=str(tmp_path / ".cache"))
    doc_hash = "testhash123"
    page_num = 1
    profile = "balanced"
    dpi = 180

    page = ExtractedPage(
        page_num=page_num,
        width=595.0,
        height=842.0,
        page_type=PageType.NATIVE_TEXT,
        rendered_dpi=dpi,
        blocks=[TextBlock(text="Xin chào", page=1, bbox=[10, 10, 100, 25])],
        quality=PageQualityReport(
            page=1,
            page_type="NATIVE_TEXT",
            native_text_score=0.98,
            ocr_confidence=1.0,
            quality_score=0.99
        )
    )

    # Initially not in cache
    assert cache.get_cached_page(doc_hash, page_num, profile, dpi) is None

    # Save to cache
    cache.save_cached_page(doc_hash, page, profile, dpi)

    # Retrieve
    restored = cache.get_cached_page(doc_hash, page_num, profile, dpi)
    assert restored is not None
    assert restored.page_num == 1
    assert len(restored.blocks) == 1
    assert restored.blocks[0].text == "Xin chào"

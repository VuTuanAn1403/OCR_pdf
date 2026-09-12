import os
import json
import hashlib
from typing import Optional, Dict, Any
from pathlib import Path
from src.ocr.domain.models.page import ExtractedPage

class ArtifactCache:
    """
    Manages caching and resumption of per-page OCR artifacts.
    Keys on document_hash, page_num, render_dpi, and profile.
    """
    def __init__(self, cache_dir: str = ".cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def compute_file_hash(file_path: str) -> str:
        """
        Computes SHA256 hash of a file.
        """
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()[:16]

    def _get_page_cache_path(
        self,
        doc_hash: str,
        page_num: int,
        profile: str,
        dpi: int
    ) -> Path:
        doc_dir = self.cache_dir / doc_hash
        doc_dir.mkdir(parents=True, exist_ok=True)
        return doc_dir / f"page-{page_num:04d}-{profile}-{dpi}.json"

    def get_cached_page(
        self,
        doc_hash: str,
        page_num: int,
        profile: str,
        dpi: int
    ) -> Optional[ExtractedPage]:
        """
        Retrieves a cached ExtractedPage if it exists and is valid.
        """
        cache_file = self._get_page_cache_path(doc_hash, page_num, profile, dpi)
        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ExtractedPage.model_validate(data)
        except Exception:
            return None

    def save_cached_page(
        self,
        doc_hash: str,
        page: ExtractedPage,
        profile: str,
        dpi: int
    ) -> None:
        """
        Saves an ExtractedPage to cache.
        """
        cache_file = self._get_page_cache_path(doc_hash, page.page_num, profile, dpi)
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(page.model_dump(mode="json"), f, ensure_ascii=False, indent=2)
        except Exception:
            pass

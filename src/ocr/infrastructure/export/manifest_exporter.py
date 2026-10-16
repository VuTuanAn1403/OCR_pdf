from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional
from src.ocr.domain.models.document import ExtractedDocument
from src.ocr.domain.models.schema import PAGE_SCHEMA_VERSION
from src.ocr.runtime.fingerprint import source_fingerprint
from src.ocr.runtime.models import model_fingerprint, model_hashes

PIPELINE_VERSION = "6.3.0.dev1"

class ManifestExporter:
    """
    Generates manifest and summary metadata for document processing runs.
    """
    @classmethod
    def generate_manifest(
        cls,
        doc: ExtractedDocument,
        config_dict: Dict[str, Any],
        config_path: Optional[Path] = None,
        cache_key: Optional[str] = None,
        resume: bool = False,
    ) -> Dict[str, Any]:
        return {
            "document_id": doc.document_id,
            "pipeline_version": PIPELINE_VERSION,
            "page_schema_version": PAGE_SCHEMA_VERSION,
            "source_fingerprint": source_fingerprint(),
            "models_fingerprint": model_fingerprint(config_dict),
            "model_hashes": model_hashes(config_dict),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "file_path": doc.file_path,
            "file_hash": doc.file_hash,
            "profile": doc.profile,
            "config_path": str(config_path) if config_path else None,
            "device": config_dict.get("device", "cpu"),
            "backend_devices": config_dict.get("backend_devices", {}),
            "total_pages": doc.total_pages,
            "pages_processed": len(doc.pages),
            "selected_pages": doc.selected_pages,
            "configuration": config_dict,
            # Pages restored from cache keep an earlier run's timings (H24).
            "cache": {
                "resume": resume,
                "cache_key": cache_key,
                "pages_from_cache": [p.page_num for p in doc.pages if p.quality.from_cache],
            },
            "summary": {
                "pages_native": doc.quality.pages_processed_native,
                "pages_ocr": doc.quality.pages_processed_ocr,
                "pages_mixed": doc.quality.pages_processed_mixed,
                "total_fallback_regions": doc.quality.total_fallback_regions,
                "total_time_seconds": round(doc.quality.total_time_seconds, 2),
                "seconds_per_page": round(doc.quality.total_time_seconds / max(1, len(doc.pages)), 2),
                "startup_seconds": doc.quality.startup_seconds,
            }
        }

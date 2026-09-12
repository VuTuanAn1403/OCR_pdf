from datetime import datetime, timezone
from typing import Dict, Any
from src.ocr.domain.models.document import ExtractedDocument

PIPELINE_VERSION = "3.1.0"

class ManifestExporter:
    """
    Generates manifest and summary metadata for document processing runs.
    """
    @classmethod
    def generate_manifest(cls, doc: ExtractedDocument, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "document_id": doc.document_id,
            "pipeline_version": PIPELINE_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "file_path": doc.file_path,
            "file_hash": doc.file_hash,
            "profile": doc.profile,
            "total_pages": doc.total_pages,
            "pages_processed": len(doc.pages),
            "selected_pages": doc.selected_pages,
            "configuration": config_dict,
            "summary": {
                "pages_native": doc.quality.pages_processed_native,
                "pages_ocr": doc.quality.pages_processed_ocr,
                "pages_mixed": doc.quality.pages_processed_mixed,
                "total_fallback_regions": doc.quality.total_fallback_regions,
                "total_time_seconds": round(doc.quality.total_time_seconds, 2),
                "seconds_per_page": round(doc.quality.total_time_seconds / max(1, len(doc.pages)), 2)
            }
        }

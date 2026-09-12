import json
from pathlib import Path
from typing import Dict, Any
from src.ocr.domain.models.document import ExtractedDocument
from src.ocr.domain.models.page import ExtractedPage

class JSONExporter:
    """
    Exports ExtractedDocument and ExtractedPage to JSON format adhering to the project contract.
    """
    @classmethod
    def export_page_json(cls, page: ExtractedPage) -> Dict[str, Any]:
        return {
            "page_num": page.page_num,
            "width": page.width,
            "height": page.height,
            "page_type": page.page_type.value,
            "rendered_dpi": page.rendered_dpi,
            "blocks": [b.to_dict() for b in page.blocks],
            "tables": [t.model_dump(mode="json") for t in page.tables],
            "quality": page.quality.model_dump(mode="json"),
            "metadata": page.metadata
        }

    @classmethod
    def export_document_json(cls, doc: ExtractedDocument) -> Dict[str, Any]:
        return {
            "document_id": doc.document_id,
            "file_path": doc.file_path,
            "file_hash": doc.file_hash,
            "total_pages": doc.total_pages,
            "selected_pages": doc.selected_pages,
            "profile": doc.profile,
            "quality": doc.quality.model_dump(mode="json"),
            "manifest": doc.manifest,
            "pages": [cls.export_page_json(p) for p in doc.pages]
        }

    @classmethod
    def save_json(cls, data: Any, output_file: Path) -> None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

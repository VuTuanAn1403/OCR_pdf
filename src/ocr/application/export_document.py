import json
from pathlib import Path
from typing import Dict, Any, Optional

from src.ocr.domain.models.document import ExtractedDocument
from src.ocr.infrastructure.export.markdown_exporter import MarkdownExporter
from src.ocr.infrastructure.export.json_exporter import JSONExporter
from src.ocr.infrastructure.export.manifest_exporter import ManifestExporter

class ExportDocumentUseCase:
    """
    Exports ExtractedDocument to the target directory following Section 17 Output contract.
    """
    @classmethod
    def execute(
        cls,
        doc: ExtractedDocument,
        output_dir: Path,
        benchmark_data: Optional[Dict[str, Any]] = None,
        accuracy_data: Optional[Dict[str, Any]] = None
    ) -> Path:
        doc_dir = output_dir / doc.document_id
        pages_dir = doc_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

        # 1. Export per-page artifacts
        for p in doc.pages:
            p_json_path = pages_dir / f"page-{p.page_num:04d}.json"
            p_md_path = pages_dir / f"page-{p.page_num:04d}.md"

            JSONExporter.save_json(JSONExporter.export_page_json(p), p_json_path)
            with open(p_md_path, "w", encoding="utf-8") as f:
                f.write(p.markdown_content)

        # 2. Export full result.md
        full_md = MarkdownExporter.export_document_markdown(doc)
        doc.full_markdown = full_md
        with open(doc_dir / "result.md", "w", encoding="utf-8") as f:
            f.write(full_md)

        # 3. Export result.json
        JSONExporter.save_json(JSONExporter.export_document_json(doc), doc_dir / "result.json")

        # 4. Export quality.json
        JSONExporter.save_json(doc.quality.model_dump(mode="json"), doc_dir / "quality.json")

        # 5. Export manifest.json
        JSONExporter.save_json(doc.manifest, doc_dir / "manifest.json")

        # 6. Export benchmark.json if provided
        if benchmark_data:
            JSONExporter.save_json(benchmark_data, doc_dir / "benchmark.json")

        # 7. Export accuracy.json if provided
        if accuracy_data:
            JSONExporter.save_json(accuracy_data, doc_dir / "accuracy.json")

        return doc_dir

import gc
import hashlib
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional
import pymupdf

from src.ocr.domain.models.document import ExtractedDocument
from src.ocr.infrastructure.export.markdown_exporter import ASSET_MODES, MarkdownExporter
from src.ocr.infrastructure.export.json_exporter import JSONExporter
from src.ocr.infrastructure.export.manifest_exporter import PIPELINE_VERSION
from src.ocr.infrastructure.export.artifact_validator import validate_document_artifacts
from src.ocr.infrastructure.export.completion import clear_completed
from src.ocr.infrastructure.export.document_contracts import write_document_contracts
from src.ocr.infrastructure.export.output_contract import OUTPUT_CONTRACT_VERSION, V6_ARTIFACTS, OUTPUT_MODES
from src.ocr.runtime.atomic_io import atomic_write_bytes, atomic_write_text

logger = logging.getLogger(__name__)

_JPEG_QUALITY = 85


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
        accuracy_data: Optional[Dict[str, Any]] = None,
        assets_mode: str = "png",
        output_mode: str = "legacy",
        document_layout: Optional[Dict[str, Any]] = None,
        keywords_file: Optional[str] = None,
        count_keywords: bool = True,
    ) -> Path:
        if assets_mode not in ASSET_MODES:
            raise ValueError(f"Unknown assets_mode '{assets_mode}'. Use one of: {', '.join(ASSET_MODES)}")
        if output_mode not in OUTPUT_MODES:
            raise ValueError(f"Unknown output_mode {output_mode!r}. Use one of: {', '.join(OUTPUT_MODES)}")
        doc_dir = output_dir / doc.document_id
        pages_dir = doc_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        # The directory is about to be rewritten; an old marker would claim
        # completion of a partially rewritten output.
        clear_completed(doc_dir)
        doc.manifest["assets_mode"] = assets_mode
        doc.manifest["pipeline_version"] = PIPELINE_VERSION
        doc.manifest["output_mode"] = output_mode
        doc.manifest["output_contract"] = {
            "schema_version": OUTPUT_CONTRACT_VERSION,
            "required_artifacts": list(V6_ARTIFACTS),
        }

        cls._export_image_assets(doc, doc_dir, assets_mode)

        from src.ocr.runtime.config import document_layout_settings, furniture_settings
        configured = doc.manifest.get("configuration", {})
        if document_layout is not None:
            requested_layout = document_layout
        elif "document_layout" in configured:
            requested_layout = configured["document_layout"]
        else:
            resolved = doc.manifest.get("document_layout", {})
            requested_layout = resolved.get("settings", {"method": resolved.get("method", "legacy")})
        settings = document_layout_settings({"document_layout": requested_layout})
        from src.ocr.infrastructure.postprocessing.page_association import ALGORITHM_VERSION
        modern = settings["method"] == "region_tree_graph_v1"
        if modern and (doc.manifest.get("document_layout", {}).get("method") != settings["method"]
                       or doc.manifest.get("document_layout", {}).get("settings") != settings
                       or doc.manifest.get("furniture", {}).get("method") != furniture_settings(settings)["method"]
                       or doc.manifest.get("furniture", {}).get("algorithm_version") != ALGORITHM_VERSION):
            from src.ocr.application.resolve_document_layout import resolve_document_layout
            resolve_document_layout(doc, settings)
        if not modern:
            if any("ordered_item_ids" in p.metadata for p in doc.pages):
                raise ValueError("Legacy export requires original page evidence; cannot reuse a finalized region-tree document")
            doc.manifest["document_layout"] = {"method": "legacy", "algorithm_version": "legacy-v6.1", "settings": settings}
            from src.ocr.application.resolve_document_furniture import resolve_document_furniture
            resolve_document_furniture(doc, settings)
            # The selected legacy reading order does not produce a region tree.
            (doc_dir / "layout_order.json").unlink(missing_ok=True)
        else:
            doc.manifest["output_contract"]["schema_version"] = "output-v6.2-layout"
        if modern:
            cls._export_layout_sidecars(doc, doc_dir)
        if doc.manifest.get("furniture", {}).get("method") == "document_context_v3":
            cls._export_furniture_sidecar(doc, doc_dir)
        else:
            (doc_dir / "furniture.json").unlink(missing_ok=True)

        # 1. Export per-page artifacts (suppress running headers/footers in Markdown by default)
        for p in doc.pages:
            # Preserve lossless structure separately while keeping the user's
            # existing pipe-only Markdown contract.
            for table in p.tables:
                if table.engine == "docling_tableformer":
                    html_path = doc_dir / "structures" / "tables" / f"{table.table_id}.html"
                    # Rendered from the cells, which may have been refilled from
                    # native/OCR text after TableFormer ran (C3.5e). Docling's
                    # own HTML stays in provenance["html"] for reference.
                    atomic_write_text(html_path, table.to_html())
                    table.provenance["html_artifact"] = html_path.relative_to(doc_dir).as_posix()
                    table.provenance["html_artifact_source"] = "cells"
            for formula in p.formulas:
                latex_path = doc_dir / "structures" / "formulas" / f"{formula.formula_id}.tex"
                atomic_write_text(latex_path, formula.latex)
                formula.provenance["latex_artifact"] = latex_path.relative_to(doc_dir).as_posix()
            p.markdown_content = MarkdownExporter.export_page_markdown(
                p, include_running_headers_footers=False, assets_mode=assets_mode)
            p_json_path = pages_dir / f"page-{p.page_num:04d}.json"
            p_md_path = pages_dir / f"page-{p.page_num:04d}.md"

            JSONExporter.save_json(JSONExporter.export_page_json(p), p_json_path)
            atomic_write_text(p_md_path, p.markdown_content)

            validation = p.metadata.get("validation_cascade")
            if validation is not None:
                p_val_path = pages_dir / f"page-{p.page_num:04d}-validation.json"
                JSONExporter.save_json(validation, p_val_path)

        # 2. Export full result.md (suppress running headers/footers in Markdown by default)
        full_md = MarkdownExporter.export_document_markdown(
            doc, include_running_headers_footers=False, assets_mode=assets_mode)
        doc.full_markdown = full_md
        atomic_write_text(doc_dir / "result.md", full_md)

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

        # 8. V6 document-level contracts (C2.4, C3.7). Additive: the files above
        # keep their format (D3); this step only reads `doc`.
        write_document_contracts(doc, doc_dir, assets_mode)
        markdown_path = doc_dir / "document.md"
        if markdown_path.is_file():
            doc.manifest["output_contract"]["markdown_sha256"] = hashlib.sha256(markdown_path.read_bytes()).hexdigest()
        JSONExporter.save_json(doc.manifest, doc_dir / "manifest.json")

        validation = validate_document_artifacts(doc_dir, include_keywords=count_keywords)
        JSONExporter.save_json(validation, doc_dir / "validation.json")
        if validation["status"] == "FAIL":
            cause = _structure_error(doc_dir)
            raise ValueError(f"Artifact validation failed: {validation['errors'][:3]}"
                             + (f"; structure step error: {cause}" if cause else ""))

        from src.ocr.application.count_keywords import CountKeywordsUseCase
        if count_keywords:
            CountKeywordsUseCase.execute(doc_dir, doc.document_id, keywords_file)
        return doc_dir

    @staticmethod
    def _export_layout_sidecars(doc, doc_dir):
        JSONExporter.save_json({"schema_version": "layout-order-v1", "pages": [
            {"page": p.page_num, **{key: p.metadata.get(key) for key in (
                "logical_page_id", "physical_page_id", "layout_family", "article_groups", "layout_tree",
                "ordered_item_ids", "ordered_source_ids", "item_assignments", "layout_review", "source_identity")}}
            for p in doc.pages]}, doc_dir / "layout_order.json")

    @staticmethod
    def _export_furniture_sidecar(doc, doc_dir):
        from src.ocr.infrastructure.structure.ids import page_items
        JSONExporter.save_json({"schema_version": "furniture-v1", **doc.manifest.get("furniture", {}),
            "pages": [{"page": p.page_num, "items": [{"id": item.id, **item.block.to_dict()}
                for item in page_items(doc.file_hash, p) if item.block is not None and (
                    item.block.role_reasons or item.block.protected_from_furniture)]} for p in doc.pages]},
            doc_dir / "furniture.json")

    @staticmethod
    def _export_image_assets(doc: ExtractedDocument, doc_dir: Path, assets_mode: str) -> None:
        """Write crops of preserved image regions and record each region's
        `asset` path. With assets_mode "none" nothing is written (ENV-10)."""
        image_pages = {p.page_num: MarkdownExporter.image_regions_to_preserve(p) for p in doc.pages}
        for regions in image_pages.values():
            for region in regions:
                region.asset = None
        if assets_mode == "none" or not any(image_pages.values()):
            return
        images_dir = doc_dir / "images"
        output = "jpeg" if assets_mode == "jpeg" else "png"
        with pymupdf.open(doc.file_path) as source_pdf:
            for p in doc.pages:
                source_page = source_pdf[p.page_num - 1]
                for region in image_pages[p.page_num]:
                    clip = source_page.rect & pymupdf.Rect(*region.bbox.to_list())
                    if clip.is_empty:
                        continue
                    name = MarkdownExporter.image_asset_name(p.page_num, region, assets_mode)
                    if _save_crop(source_page, clip, images_dir / name, output):
                        region.asset = f"images/{name}"
                    else:
                        # The validator reports the missing file as an error.
                        logger.warning("Page %d: could not export image region %s", p.page_num, region.region_id)

    @classmethod
    def _detect_running_headers_and_footers(cls, doc: ExtractedDocument) -> None:
        """The legacy exporter retains its frozen V6.1 furniture policy."""
        from src.ocr.infrastructure.postprocessing.legacy_furniture import resolve_legacy_furniture
        resolve_legacy_furniture(doc)


def _save_crop(page: pymupdf.Page, clip: pymupdf.Rect, path: Path, output: str) -> bool:
    """Render a page crop atomically; retry at lower DPI on memory pressure."""
    for dpi in (180, 96):
        try:
            pix = page.get_pixmap(dpi=dpi, clip=clip, alpha=False)
            data = pix.tobytes(output=output, jpg_quality=_JPEG_QUALITY)
            del pix
            atomic_write_bytes(path, data)
            return True
        except Exception as exc:  # MuPDF raises several unrelated error types
            logger.warning("Rendering %s at %d DPI failed: %s", path.name, dpi, exc)
            gc.collect()
    return False


def _structure_error(doc_dir: Path) -> Optional[str]:
    """The recorded structure-step failure, if any, so a validation error
    caused by it names the real exception."""
    try:
        return json.loads((doc_dir / "structure.json").read_text(encoding="utf-8")).get("error")
    except (OSError, ValueError, AttributeError):
        return None

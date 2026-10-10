"""Validate the PDF-to-Markdown artifact contract after export."""

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List

from src.ocr.domain.models.table import TableStructure
from src.ocr.domain.models.region import Region
from src.ocr.infrastructure.export.markdown_exporter import MarkdownExporter


_IMAGE_LINK = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_PIPE_SEPARATOR = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$")
_HTML_TABLE = re.compile(r"</?(?:table|thead|tbody|tr|td|th)\b", re.IGNORECASE)


def validate_document_artifacts(
    doc_dir: Path, ground_truth_manifest: Path | None = None
) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    counts = {
        "pages_json": 0,
        "pages_markdown": 0,
        "tables_json": 0,
        "pipe_tables_markdown": 0,
        "table_candidates_as_paragraphs": 0,
        "image_links": 0,
        "broken_image_links": 0,
        "evidence_blocks": 0,
        "review_required_lines": 0,
        "large_unread_image_regions": 0,
        "ground_truth_cases": 0,
    }
    root = doc_dir.resolve()
    page_files = sorted(
        path for path in (doc_dir / "pages").glob("page-*.json")
        if re.fullmatch(r"page-\d{4}\.json", path.name)
    )
    manifest_path = doc_dir / "manifest.json"
    if not manifest_path.is_file():
        errors.append("manifest.json is missing")
        expected_pages = []
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_pages = manifest.get("selected_pages") or []
        if len(page_files) != len(expected_pages):
            errors.append(
                f"Page JSON count {len(page_files)} differs from selected pages {len(expected_pages)}"
            )
        found_pages = [int(path.stem.split("-")[1]) for path in page_files]
        if sorted(found_pages) != sorted(expected_pages):
            errors.append(f"Page numbers {found_pages} differ from selected pages {expected_pages}")

    markdown_files: List[Path] = []
    result_path = doc_dir / "result.md"
    if result_path.is_file():
        markdown_files.append(result_path)
    else:
        errors.append("result.md is missing")

    for json_path in page_files:
        counts["pages_json"] += 1
        page_data = json.loads(json_path.read_text(encoding="utf-8"))
        page_num = page_data.get("page_num")
        if page_num != int(json_path.stem.split("-")[1]):
            errors.append(f"{json_path.name}: page_num {page_num} does not match filename")
        markdown_path = json_path.with_suffix(".md")
        if not markdown_path.is_file():
            errors.append(f"Page {page_num}: Markdown is missing")
            continue
        counts["pages_markdown"] += 1
        markdown_files.append(markdown_path)
        markdown = markdown_path.read_text(encoding="utf-8")
        separator_count = sum(bool(_PIPE_SEPARATOR.fullmatch(line)) for line in markdown.splitlines())
        counts["pipe_tables_markdown"] += separator_count
        if "```mermaid" in markdown:
            source_text = " ".join(str(block.get("text", "")) for block in page_data.get("blocks") or []).casefold()
            if not any(title in source_text for title in (
                "mô hình tổ chức", "sơ đồ tổ chức", "cơ cấu tổ chức", "sơ đồ bộ máy",
            )):
                warnings.append(
                    f"Page {page_num}: Mermaid diagram has no explicit title in extracted source text; inspect for false diagram"
                )

        expected_tables = 0
        for table_data in page_data.get("tables") or []:
            counts["tables_json"] += 1
            table = TableStructure.model_validate(table_data)
            rendered = table.to_markdown(format="pipe")
            if table.cells and not rendered:
                errors.append(f"Page {page_num}: table {table.table_id} lost all cell content")
            elif rendered and rendered not in markdown:
                errors.append(f"Page {page_num}: table {table.table_id} is missing from Markdown")
            normalized_rendered = re.sub(
                r"\s+", " ", unicodedata.normalize("NFC", rendered.replace("\\|", "|"))
            ).casefold()
            for cell in table.cells:
                cell_text = re.sub(r"<br\s*/?>", " ", cell.text, flags=re.IGNORECASE)
                normalized_cell = re.sub(
                    r"\s+", " ", unicodedata.normalize("NFC", cell_text)
                ).strip().casefold()
                if normalized_cell and normalized_cell not in normalized_rendered:
                    errors.append(
                        f"Page {page_num}: table {table.table_id} lost cell ({cell.row_idx}, {cell.col_idx})"
                    )
            if any(_PIPE_SEPARATOR.fullmatch(line) for line in rendered.splitlines()):
                expected_tables += 1
            elif rendered:
                counts["table_candidates_as_paragraphs"] += 1
                warnings.append(
                    f"Page {page_num}: table candidate {table.table_id} rendered as paragraphs"
                )
            rows: dict[int, dict[int, str]] = {}
            for cell in table.cells:
                rows.setdefault(cell.row_idx, {})[cell.col_idx] = cell.text.strip()
            for row_number in sorted(rows)[:-1]:
                current = rows[row_number]
                following = rows.get(row_number + 1, {})
                current_numeric = sum(bool(re.search(r"\d{3,}", text)) for col, text in current.items() if col > 0)
                following_numeric = sum(bool(re.search(r"\d{3,}", text)) for col, text in following.items() if col > 0)
                if (len(current.get(0, "")) >= 18 and current_numeric == 0
                        and following_numeric >= 2 and len(following.get(0, "")) < 40):
                    warnings.append(
                        f"Page {page_num}: table {table.table_id} may split a row label from numeric cells"
                    )
        if separator_count != expected_tables:
            errors.append(
                f"Page {page_num}: {separator_count} Pipe Tables, expected {expected_tables}"
            )

        regions = page_data.get("regions") or []
        region_ids = {region.get("region_id") for region in regions}
        evidence_blocks = page_data.get("evidence_blocks") or []
        if len(evidence_blocks) != len(page_data.get("blocks") or []):
            errors.append(f"Page {page_num}: evidence count differs from text block count")
        for region in regions:
            if not region.get("status"):
                errors.append(f"Page {page_num}: region {region.get('region_id')} has no status")
            if region.get("orientation_status") == "UNKNOWN":
                warnings.append(f"Page {page_num}: region {region.get('region_id')} orientation unknown")
            if region.get("status") == "NEEDS_REVIEW" or region.get("needs_fallback"):
                warnings.append(
                    f"Page {page_num}: region {region.get('region_id')} needs review: {region.get('failure_reason')}"
                )
            bbox = region.get("bbox") or {}
            page_area = float(page_data.get("width", 0)) * float(page_data.get("height", 0))
            image_area = max(0.0, float(bbox.get("x1", 0)) - float(bbox.get("x0", 0))) * max(
                0.0, float(bbox.get("y1", 0)) - float(bbox.get("y0", 0))
            )
            if (region.get("status") == "PRESERVED"
                    and region.get("content_type") in ("IMAGE", "IMAGE_TABLE", "FIGURE")
                    and page_area > 0 and image_area / page_area >= 0.25):
                counts["large_unread_image_regions"] += 1
                warnings.append(
                    f"Page {page_num}: large image {region.get('region_id')} was preserved without OCR text; inspect for silent content loss"
                )
            if (
                region.get("content_type") in ("IMAGE", "IMAGE_TABLE", "FIGURE")
                and (region.get("status") == "PRESERVED"
                     or (page_area > 0 and image_area / page_area >= 0.20))
            ):
                asset_name = MarkdownExporter.image_asset_name(
                    page_num, Region.model_validate(region)
                )
                if f"../images/{asset_name}" not in markdown:
                    errors.append(f"Page {page_num}: preserved region {region.get('region_id')} has no Markdown image")
                if not (doc_dir / "images" / asset_name).is_file():
                    errors.append(f"Page {page_num}: preserved region {region.get('region_id')} has no PNG asset")
        for evidence in evidence_blocks:
            counts["evidence_blocks"] += 1
            block_id = evidence.get("block_id")
            if not evidence.get("region_id") or evidence.get("region_id") not in region_ids:
                errors.append(f"Page {page_num}: evidence {block_id} has no valid region")
            if evidence.get("orientation") is None:
                errors.append(f"Page {page_num}: evidence {block_id} has no orientation")
            if not evidence.get("status"):
                errors.append(f"Page {page_num}: evidence {block_id} has no status")

        quality = page_data.get("quality") or {}
        counts["review_required_lines"] += quality.get("review_required_count") or 0
        if quality.get("content_status") in ("IMAGE_PRESERVED_NO_TEXT", "UNRESOLVED_NO_CONTENT"):
            warnings.append(f"Page {page_num}: {quality.get('content_status')}")

    for markdown_path in markdown_files:
        markdown = markdown_path.read_text(encoding="utf-8")
        if any(char in markdown for char in "┌┬┐├┼┤└┴┘"):
            errors.append(f"{markdown_path.relative_to(doc_dir)} contains box drawing tables")
        if _HTML_TABLE.search(markdown):
            errors.append(f"{markdown_path.relative_to(doc_dir)} contains HTML table tags")
        for image_path in _IMAGE_LINK.findall(markdown):
            counts["image_links"] += 1
            target = (markdown_path.parent / image_path).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                counts["broken_image_links"] += 1
                errors.append(f"{markdown_path.relative_to(doc_dir)} has broken image {image_path}")

    if counts["review_required_lines"]:
        warnings.append(f"{counts['review_required_lines']} OCR lines require review")
    if ground_truth_manifest is not None:
        from benchmark.accuracy_first import evaluate_case

        reference = json.loads(Path(ground_truth_manifest).read_text(encoding="utf-8"))
        for case in reference.get("cases", []):
            if Path(case["pdf"]).stem != doc_dir.name or case["page"] not in expected_pages:
                continue
            counts["ground_truth_cases"] += 1
            measured = evaluate_case(case, doc_dir.parent)
            page_num = case["page"]
            if not measured["prediction_exists"]:
                errors.append(f"Page {page_num}: ground truth page output is missing")
                continue
            if measured["orientation_correct"] is False:
                errors.append(f"Page {page_num}: orientation differs from ground truth")
            if measured["missing_anchors"]:
                errors.append(
                    f"Page {page_num}: visible text missing from image: {measured['missing_anchors']}"
                )
            if measured["missing_numeric_values"]:
                errors.append(
                    f"Page {page_num}: numeric values differ from ground truth: {measured['missing_numeric_values']}"
                )
            if measured["table_cell_f1"] is not None and measured["table_cell_f1"] < 0.95:
                errors.append(f"Page {page_num}: table cell association F1 {measured['table_cell_f1']:.2f} < 0.95")
            if (measured["reading_order_accuracy"] is not None
                    and measured["reading_order_accuracy"] < 1.0):
                errors.append(
                    f"Page {page_num}: reading order disagrees with ground truth"
                )
    status = "FAIL" if errors else "NEEDS_REVIEW" if warnings else "PASS"
    return {"status": status, "counts": counts, "errors": errors, "warnings": warnings}

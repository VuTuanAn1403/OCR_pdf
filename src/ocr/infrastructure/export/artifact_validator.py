"""Validate the PDF-to-Markdown artifact contract after export."""

import json
import hashlib
import re
import unicodedata
from zipfile import BadZipFile
from pathlib import Path
from typing import Any, Dict, List

from src.ocr.domain.models.table import TableStructure
from src.ocr.domain.models.region import Region
from src.ocr.infrastructure.export.markdown_exporter import ASSET_MODES, MarkdownExporter
from src.ocr.infrastructure.export.output_contract import OUTPUT_MODES, RESULT_INDEX_SCHEMA
from src.ocr.infrastructure.export.result_reader import load_document_json


_IMAGE_LINK = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_PIPE_SEPARATOR = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$")
_HTML_TABLE = re.compile(r"</?(?:table|thead|tbody|tr|td|th)\b", re.IGNORECASE)


def validate_document_artifacts(
    doc_dir: Path, ground_truth_manifest: Path | None = None, *, include_keywords: bool = True
) -> Dict[str, Any]:
    try:
        result = _validate_document_artifacts(Path(doc_dir), ground_truth_manifest)
        if include_keywords and (Path(doc_dir)/"manifest.json").is_file():
            from src.ocr.infrastructure.export.keyword_validator import validate_keyword_artifacts
            validate_keyword_artifacts(Path(doc_dir), json.loads((Path(doc_dir)/"manifest.json").read_text(encoding="utf-8")))
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError, BadZipFile) as exc:
        return {"status": "FAIL", "counts": {}, "warnings": [],
                "errors": [f"Artifact data cannot be read or is malformed ({type(exc).__name__}): {exc}"]}


def _validate_document_artifacts(
    doc_dir: Path, ground_truth_manifest: Path | None = None
) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    counts = {
        "formulas_json": 0,
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
    assets_mode = "png"
    if not manifest_path.is_file():
        errors.append("manifest.json is missing")
        expected_pages = []
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_pages = manifest.get("selected_pages") or []
        # Outputs written before --assets existed always exported PNG files.
        assets_mode = manifest.get("assets_mode", "png")
        if assets_mode not in ASSET_MODES:
            errors.append(f"manifest.json has unknown assets_mode {assets_mode!r}")
        output_mode = manifest.get("output_mode", "legacy")
        if output_mode not in OUTPUT_MODES:
            errors.append(f"manifest.json has unknown output_mode {output_mode!r}")
        result_json = doc_dir / "result.json"
        # Legacy fixtures may omit result.json; a declared compact output
        # always needs its index and all referenced pages.
        if output_mode == "compact" or result_json.is_file():
            try:
                result = json.loads(result_json.read_text(encoding="utf-8"))
                indexed = isinstance(result, dict) and result.get("schema_version") == RESULT_INDEX_SCHEMA
                if indexed != (output_mode == "compact"):
                    errors.append("result.json schema differs from manifest.json output_mode")
                if output_mode == "compact" or (isinstance(result, dict) and "page_refs" in result):
                    expanded = load_document_json(result_json)
                    if expanded.get("selected_pages") != expected_pages:
                        errors.append("result.json selected_pages differs from manifest.json")
                    for key in ("document_id", "file_hash"):
                        if expanded.get(key) != manifest.get(key):
                            errors.append(f"result.json {key} differs from manifest.json")
            except (OSError, ValueError, TypeError, KeyError) as exc:
                errors.append(f"result.json cannot be read or its page index is invalid: {exc}")
        if len(page_files) != len(expected_pages):
            errors.append(
                f"Page JSON count {len(page_files)} differs from selected pages {len(expected_pages)}"
            )
        found_pages = [int(path.stem.split("-")[1]) for path in page_files]
        if sorted(found_pages) != sorted(expected_pages):
            errors.append(f"Page numbers {found_pages} differ from selected pages {expected_pages}")

    markdown_files: List[Path] = []
    result_path = doc_dir / "result.md"
    result_markdown = ""
    if result_path.is_file():
        markdown_files.append(result_path)
        result_markdown = result_path.read_text(encoding="utf-8")
    else:
        errors.append("result.md is missing")
    # V6 document-level Markdown gets the same table/image checks (C3.7).
    if (doc_dir / "document.md").is_file():
        markdown_files.append(doc_dir / "document.md")

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
        for reason in page_data.get("metadata", {}).get("layout_review", []):
            warnings.append(f"Page {page_num}: layout {reason.get('code')} requires review")
        for block in page_data.get("blocks", []):
            for code in block.get("review_reason_codes", []):
                warnings.append(f"Page {page_num}: source {block.get('source_id')} {code} requires review")
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
            if table.needs_review:
                warnings.append(f"Page {page_num}: table {table.table_id} requires independent review")
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

        for formula in page_data.get("formulas") or []:
            counts["formulas_json"] += 1
            latex = str(formula.get("latex", "")).strip()
            rendered_formula = "$$\n" + latex + "\n$$"
            if not latex or rendered_formula not in markdown:
                errors.append(f"Page {page_num}: formula {formula.get('formula_id')} is missing from Markdown")
            if latex and rendered_formula not in result_markdown:
                errors.append(f"Page {page_num}: formula {formula.get('formula_id')} is missing from result.md")
            if formula.get("status") != "VALIDATED":
                warnings.append(f"Page {page_num}: formula {formula.get('formula_id')} requires independent review")

        backend = page_data.get("metadata", {}).get("docling_structure", {})
        if backend.get("status") == "FAILED_FALLBACK":
            warnings.append(f"Page {page_num}: Docling unavailable or failed; heuristic fallback used")
        elif backend.get("status") == "partial_success" or backend.get("errors"):
            warnings.append(f"Page {page_num}: Docling returned a partial result or conversion errors")

        regions = page_data.get("regions") or []
        region_ids = {region.get("region_id") for region in regions}
        evidence_blocks = page_data.get("evidence_blocks") or []
        if len(evidence_blocks) < len(page_data.get("blocks") or []):
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
            page_w = float(page_data.get("width", 0) or 0)
            page_h = float(page_data.get("height", 0) or 0)
            page_area = page_w * page_h
            rx0 = float(bbox.get("x0", 0))
            ry0 = float(bbox.get("y0", 0))
            rx1 = float(bbox.get("x1", 0))
            ry1 = float(bbox.get("y1", 0))
            # Regions completely outside visible page boundary have no source pixels and are not preserved
            if (
                rx1 <= 0 or ry1 <= 0
                or (page_w > 0 and rx0 >= page_w)
                or (page_h > 0 and ry0 >= page_h)
            ):
                continue
            image_area = max(0.0, rx1 - rx0) * max(0.0, ry1 - ry0)
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
                region_id = region.get("region_id")
                if assets_mode == "none":
                    if region.get("asset") is not None:
                        errors.append(f"Page {page_num}: region {region_id} has an asset although assets_mode is none")
                    if MarkdownExporter.image_omitted_marker(region_id) not in markdown:
                        errors.append(f"Page {page_num}: preserved region {region_id} has no omitted-image note")
                    continue
                asset_name = MarkdownExporter.image_asset_name(
                    page_num, Region.model_validate(region), assets_mode
                )
                if f"../images/{asset_name}" not in markdown:
                    errors.append(f"Page {page_num}: preserved region {region_id} has no Markdown image")
                if not (doc_dir / "images" / asset_name).is_file():
                    errors.append(f"Page {page_num}: preserved region {region_id} has no {assets_mode.upper()} asset")
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

    _validate_document_contracts(doc_dir, errors, warnings, counts)

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


# `{doc_sha16}:p{page:04d}:{kind}{n:04d}`; the document part is not checked
# for hex so synthetic fixtures with short hashes stay valid.
_ELEMENT_ID = re.compile(r"^[^:\s]+:p\d{4}:[btfi]\d{4}$")


def _validate_document_contracts(doc_dir: Path, errors: List[str], warnings: List[str],
                                 counts: Dict[str, Any]) -> None:
    from src.ocr.infrastructure.export.output_contract import OUTPUT_CONTRACT_VERSION, V6_ARTIFACTS
    from src.ocr.infrastructure.export.contract_schema import validate_extraction, validate_contract_set

    manifest = json.loads((doc_dir / "manifest.json").read_text(encoding="utf-8")) if (doc_dir / "manifest.json").is_file() else {}
    declaration = manifest.get("output_contract")
    is_v6 = declaration is not None or any((doc_dir / name).exists() for name in V6_ARTIFACTS)
    counts["output_contract_policy"] = "v6" if is_v6 else "legacy"
    if not is_v6:
        return
    if declaration is not None and (not isinstance(declaration, dict)
            or declaration.get("schema_version") not in (OUTPUT_CONTRACT_VERSION, "output-v6.2-layout")
            or declaration.get("required_artifacts") != list(V6_ARTIFACTS)):
        errors.append("manifest.json has an invalid output_contract declaration")
    missing = [name for name in V6_ARTIFACTS if not (doc_dir / name).is_file()]
    errors.extend(f"V6 required artifact {name} is missing" for name in missing)
    payloads = {}
    for name in V6_ARTIFACTS[:-1]:
        if name in missing:
            continue
        try:
            payloads[name] = json.loads((doc_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{name} cannot be read: {exc}")
    if "extraction.json" in payloads:
        errors.extend(validate_extraction(payloads["extraction.json"]))
    if missing or len(payloads) != 3:
        return
    extraction, structure, document = (payloads[name] for name in V6_ARTIFACTS[:-1])
    modern = manifest.get("document_layout", {}).get("method") == "region_tree_graph_v1"
    expected_schema = "extraction-v2-layout" if modern else "extraction-v1"
    if extraction.get("schema_version") != expected_schema:
        errors.append("extraction.json schema differs from manifest document_layout method")
    if isinstance(declaration, dict) and declaration.get("schema_version") != ("output-v6.2-layout" if modern else OUTPUT_CONTRACT_VERSION):
        errors.append("output_contract schema differs from document_layout method")
    if manifest.get("document_layout", {}).get("method") == "region_tree_graph_v1":
        sidecars = {}
        for name in ("layout_order.json", "furniture.json"):
            try:
                sidecars[name] = json.loads((doc_dir / name).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                errors.append(f"Required layout sidecar {name} cannot be read: {exc}")
        if "layout_order.json" in sidecars:
            declared = sidecars["layout_order.json"].get("pages", [])
            expected = [(p["page"], p.get("ordered_item_ids")) for p in extraction["pages"]]
            if [(p.get("page"), p.get("ordered_item_ids")) for p in declared] != expected:
                errors.append("layout_order.json flow differs from extraction.json")
        if ("furniture.json" in sidecars and sidecars["furniture.json"].get("context_hash")
                != manifest.get("furniture", {}).get("context_hash")):
            errors.append("furniture.json context differs from manifest")
    contract_errors = validate_contract_set(extraction, document, structure)
    errors.extend(contract_errors)
    if contract_errors:
        return
    pages = [page["page"] for page in extraction["pages"]]
    if pages != manifest.get("selected_pages"):
        errors.append("extraction.json pages differ from manifest selected_pages")
    if manifest.get("document_id", document["doc_id"]) != document["doc_id"]:
        errors.append("document.json identity differs from manifest")
    markdown_path = doc_dir / "document.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    if isinstance(declaration, dict):
        expected = declaration.get("markdown_sha256")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            errors.append("manifest.json output_contract is missing a valid Markdown hash")
        elif hashlib.sha256(markdown_path.read_bytes()).hexdigest() != expected:
            errors.append("document.md differs from the exported Markdown contract")
    # Independently check semantic content, including older V6 outputs which
    # did not yet declare a Markdown checksum. Escaping must not hide deletion.
    def content(text):
        return " ".join(re.sub(r"\\([#|>+*\-])", r"\1", unicodedata.normalize("NFC", text)).split())
    body = content(markdown)
    for section in document["sections"]:
        for field in ("title", "title_en"):
            text = section.get(field)
            if text and content(text) not in body:
                errors.append(f"document.md is missing {field} for section {section['section_id']}")
    for chunk in document["chunks"]:
        text = chunk["text_corrected"]
        if chunk["type"] == "list_item" and text.strip():
            text = "- " + text.lstrip()[1:].lstrip()
        if chunk["type"] in ("paragraph", "list_item", "formula") and text and content(text) not in body:
            errors.append(f"document.md is missing content for chunk {chunk['chunk_id']}")
    counts["structure_sections"] = len(document["sections"])
    counts["structure_chunks"] = len(document["chunks"])
    counts["cells_with_coarse_bbox"] = sum(cell.get("bbox_source") == "table_fallback"
            for table in document["tables"] for cell in table["cells"])
    if counts["cells_with_coarse_bbox"]:
        warnings.append(f"{counts['cells_with_coarse_bbox']} table cells have coarse table_fallback coordinates")
    review = sum(1 for chunk in document["chunks"] if chunk.get("needs_review"))
    if review:
        warnings.append(f"{review} document chunks need review")

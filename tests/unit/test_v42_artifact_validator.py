import json
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from src.ocr.infrastructure.export.artifact_validator import validate_document_artifacts
from src.ocr.domain.models.table import TableStructure, TableCell


@pytest.fixture
def doc_dir():
    root = Path.cwd() / "output" / "v42_validation" / "validator_unit"
    root.mkdir(parents=True, exist_ok=True)
    directory = root / f"case-{uuid4().hex}"
    directory.mkdir()
    assert directory.resolve().is_relative_to(Path.cwd().resolve())
    try:
        yield directory
    finally:
        shutil.rmtree(directory)


def _make_minimal_document(root):
    pages = root / "pages"
    pages.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({"selected_pages": [1]}), encoding="utf-8")
    (pages / "page-0001.json").write_text(
        json.dumps({
            "page_num": 1, "blocks": [], "tables": [], "regions": [],
            "evidence_blocks": [], "quality": {"content_status": "EXTRACTED"},
        }),
        encoding="utf-8",
    )
    (pages / "page-0001.md").write_text("# Page 1\n", encoding="utf-8")
    (root / "result.md").write_text("# Page 1\n", encoding="utf-8")
    return pages


def test_validator_accepts_complete_document(doc_dir):
    _make_minimal_document(doc_dir)
    report = validate_document_artifacts(doc_dir)
    assert report["status"] == "PASS"
    assert report["counts"]["pages_markdown"] == 1


def test_validator_flags_untitled_inferred_mermaid_diagram(doc_dir):
    pages = _make_minimal_document(doc_dir)
    markdown = "# Trang 1\n\n### MÔ HÌNH TỔ CHỨC\n\n```mermaid\ngraph TD\nA-->B\n```\n"
    (pages / "page-0001.md").write_text(markdown, encoding="utf-8")
    (doc_dir / "result.md").write_text(markdown, encoding="utf-8")
    report = validate_document_artifacts(doc_dir)
    assert report["status"] == "NEEDS_REVIEW"
    assert any("false diagram" in warning for warning in report["warnings"])


def test_validator_accepts_nfc_rendering_of_decomposed_cell_text(doc_dir):
    pages = _make_minimal_document(doc_dir)
    table = TableStructure(table_id="t1", page=1, num_rows=2, num_cols=2, cells=[
        TableCell(row_idx=0, col_idx=0, text="Khoản mục"),
        TableCell(row_idx=0, col_idx=1, text="Giá trị"),
        TableCell(row_idx=1, col_idx=0, text="Doanh thu đô\u0300ng"),
        TableCell(row_idx=1, col_idx=1, text="1.234"),
    ])
    page_path = pages / "page-0001.json"
    data = json.loads(page_path.read_text(encoding="utf-8"))
    data["tables"] = [table.model_dump(mode="json")]
    page_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    markdown = table.to_markdown(format="pipe")
    (pages / "page-0001.md").write_text(markdown, encoding="utf-8")
    (doc_dir / "result.md").write_text(markdown, encoding="utf-8")
    assert validate_document_artifacts(doc_dir)["status"] == "PASS"


def test_ground_truth_gate_detects_semantic_errors(doc_dir):
    pages = _make_minimal_document(doc_dir)
    (pages / "page-0001.md").write_text("first second\n", encoding="utf-8")
    manifest = doc_dir / "ground_truth.json"
    manifest.write_text(json.dumps({"cases": [{
        "pdf": f"inputs/{doc_dir.name}.pdf", "page": 1,
        "orientation_degrees": 90,
        "text_anchors": ["visible in source"],
        "numeric_values": ["123.456"],
        "reading_order": ["second", "first"],
        "table_cells": [{"row": "Revenue", "column": 1, "text": "123.456"}],
    }]}), encoding="utf-8")
    report = validate_document_artifacts(doc_dir, ground_truth_manifest=manifest)
    assert report["status"] == "FAIL"
    assert report["counts"]["ground_truth_cases"] == 1
    assert any("orientation differs" in error for error in report["errors"])
    assert any("visible text missing" in error for error in report["errors"])
    assert any("numeric values differ" in error for error in report["errors"])
    assert any("table cell association" in error for error in report["errors"])
    assert any("reading order disagrees" in error for error in report["errors"])


def test_validator_rejects_broken_image_link(doc_dir):
    pages = _make_minimal_document(doc_dir)
    (pages / "page-0001.md").write_text("![missing](../images/missing.png)\n", encoding="utf-8")
    report = validate_document_artifacts(doc_dir)
    assert report["status"] == "FAIL"
    assert report["counts"]["broken_image_links"] == 1


def test_validator_rejects_missing_evidence(doc_dir):
    pages = _make_minimal_document(doc_dir)
    page_path = pages / "page-0001.json"
    data = json.loads(page_path.read_text(encoding="utf-8"))
    data["blocks"] = [{"text": "test"}]
    page_path.write_text(json.dumps(data), encoding="utf-8")
    report = validate_document_artifacts(doc_dir)
    assert report["status"] == "FAIL"
    assert any("evidence count" in error for error in report["errors"])


def test_validator_rejects_wrong_selected_page(doc_dir):
    _make_minimal_document(doc_dir)
    (doc_dir / "manifest.json").write_text(json.dumps({"selected_pages": [2]}), encoding="utf-8")
    report = validate_document_artifacts(doc_dir)
    assert report["status"] == "FAIL"


def test_validator_detects_unexported_overlapping_table_cells(doc_dir):
    pages = _make_minimal_document(doc_dir)
    page_path = pages / "page-0001.json"
    data = json.loads(page_path.read_text(encoding="utf-8"))
    data["tables"] = [{
        "table_id": "t1", "page": 1, "num_rows": 1, "num_cols": 1,
        "cells": [
            {"row_idx": 0, "col_idx": 0, "text": "first cell"},
            {"row_idx": 0, "col_idx": 0, "text": "second cell"},
        ],
    }]
    page_path.write_text(json.dumps(data), encoding="utf-8")
    report = validate_document_artifacts(doc_dir)
    assert report["status"] == "FAIL"
    assert any("missing from Markdown" in error for error in report["errors"])


def test_validator_rejects_preserved_image_without_asset(doc_dir):
    pages = _make_minimal_document(doc_dir)
    page_path = pages / "page-0001.json"
    data = json.loads(page_path.read_text(encoding="utf-8"))
    data["regions"] = [{
        "region_id": "p1_r1", "content_type": "IMAGE", "status": "PRESERVED",
        "bbox": {"x0": 0, "y0": 0, "x1": 100, "y1": 100},
        "orientation_status": "UNCERTAIN",
    }]
    page_path.write_text(json.dumps(data), encoding="utf-8")
    report = validate_document_artifacts(doc_dir)
    assert report["status"] == "FAIL"
    assert any("has no PNG asset" in error for error in report["errors"])


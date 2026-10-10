"""Evaluate human-annotated PDF spans and relationships against OCR artifacts.

The manifest is data, so the evaluator contains no document or page exceptions.
CER/WER here are on the annotated visible spans, not full-page transcriptions.
"""

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from benchmark.evaluate import compute_cer, compute_wer


TARGETS = {
    "mean_ocr_confidence": 0.90,
    "numeric_exact_match": 0.99,
    "table_cell_f1": 0.95,
    "orientation_accuracy": 0.99,
    "content_recall": 0.99,
    "silent_content_loss": 0,
}


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value).casefold()).strip()


def normalize_row_label(value: str) -> str:
    """Compare row identity despite OCR accent errors; cell values stay exact."""
    decomposed = unicodedata.normalize("NFD", normalize(value))
    plain = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", plain.replace("đ", "d")).strip()


def _angle_from_metadata(page: dict[str, Any]) -> int:
    label = page.get("metadata", {}).get("orientation_fallback") or ""
    if ":rotate_" in label:
        label = label.split(":", 1)[1].split(";", 1)[0]
    if label == "rotate_90_cw":
        return 90
    if label == "rotate_90_ccw":
        return 270
    if label == "rotate_180":
        return 180
    return 0


def _table_associations(page: dict[str, Any], expected_rows: set[str]) -> set[tuple[str, int, str]]:
    associations = set()
    for table in page.get("tables", []):
        rows: dict[int, dict[int, str]] = {}
        for cell in table.get("cells", []):
            rows.setdefault(cell["row_idx"], {})[cell["col_idx"]] = normalize(cell.get("text", ""))
        for row in rows.values():
            label = row.get(0, "")
            row_key = normalize_row_label(label)
            matched = [expected for expected in expected_rows
                       if len(normalize_row_label(expected)) >= 15
                       and row_key.startswith(normalize_row_label(expected))]
            if not matched:
                continue
            reference_label = max(matched, key=len)
            for column, text in row.items():
                if column > 0 and text:
                    associations.add((reference_label, column, text))
    return associations


def evaluate_case(case: dict[str, Any], prediction_root: Path) -> dict[str, Any]:
    pdf = Path(case["pdf"])
    page_number = int(case["page"])
    page_dir = prediction_root / pdf.stem / "pages"
    page_path = page_dir / f"page-{page_number:04d}.json"
    markdown_path = page_dir / f"page-{page_number:04d}.md"
    exists = page_path.is_file() and markdown_path.is_file()
    page = json.loads(page_path.read_text(encoding="utf-8")) if exists else {}
    markdown = markdown_path.read_text(encoding="utf-8") if exists else ""
    text = normalize(markdown)
    spans = [block.get("text", "") for block in page.get("blocks", [])]
    spans += [cell.get("text", "") for table in page.get("tables", []) for cell in table.get("cells", [])]
    spans += [f"{a} {b}" for a, b in zip(spans, spans[1:])]
    spans = [span for span in spans if normalize(span)]

    anchors = case.get("text_anchors", [])
    missing = [anchor for anchor in anchors if normalize(anchor) not in text]
    span_cer = []
    span_wer = []
    for anchor in anchors:
        if not spans:
            span_cer.append(1.0)
            span_wer.append(1.0)
            continue
        scored = [(compute_cer(anchor, candidate), compute_wer(anchor, candidate)) for candidate in spans]
        cer, wer = min(scored, key=lambda item: (item[0], item[1]))
        span_cer.append(min(1.0, cer))
        span_wer.append(min(1.0, wer))

    numbers = case.get("numeric_values", [])
    missed_numbers = [value for value in numbers if normalize(value) not in text]

    expected_cells = {
        (normalize(cell["row"]), int(cell["column"]), normalize(cell["text"]))
        for cell in case.get("table_cells", [])
    }
    predicted_cells = _table_associations(page, {cell[0] for cell in expected_cells}) if exists else set()
    true_cells = len(expected_cells & predicted_cells)
    cell_precision = true_cells / len(predicted_cells) if predicted_cells else 0.0
    cell_recall = true_cells / len(expected_cells) if expected_cells else 0.0
    cell_f1 = (
        2 * cell_precision * cell_recall / (cell_precision + cell_recall)
        if cell_precision + cell_recall else 0.0
    ) if expected_cells else None

    order = case.get("reading_order", [])
    order_pairs = 0
    correct_pairs = 0
    positions = [text.find(normalize(anchor)) for anchor in order]
    for left in range(len(positions)):
        for right in range(left + 1, len(positions)):
            order_pairs += 1
            if positions[left] >= 0 and positions[right] > positions[left]:
                correct_pairs += 1

    return {
        "pdf": case["pdf"],
        "page": page_number,
        "category": case.get("category"),
        "prediction_exists": exists,
        "expected_orientation": case.get("orientation_degrees"),
        "predicted_orientation": _angle_from_metadata(page) if exists else None,
        "orientation_correct": (
            _angle_from_metadata(page) == case["orientation_degrees"]
            if exists and "orientation_degrees" in case else None
        ),
        "text_anchors": len(anchors),
        "missing_anchors": missing,
        "span_cer": sum(span_cer) / len(span_cer) if span_cer else None,
        "span_wer": sum(span_wer) / len(span_wer) if span_wer else None,
        "numeric_values": len(numbers),
        "missing_numeric_values": missed_numbers,
        "table_cells": len(expected_cells),
        "table_cell_true_positives": true_cells,
        "table_cell_predictions_in_scope": len(predicted_cells),
        "table_cell_f1": cell_f1,
        "reading_order_pairs": order_pairs,
        "reading_order_correct_pairs": correct_pairs,
        "reading_order_accuracy": correct_pairs / order_pairs if order_pairs else None,
        "ocr_confidence": page.get("quality", {}).get("ocr_confidence") if exists else None,
        "recognized_lines": page.get("quality", {}).get("recognized_lines", 0) if exists else 0,
    }


def evaluate(manifest_path: Path, prediction_root: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = [evaluate_case(case, prediction_root) for case in manifest["cases"]]
    anchor_total = sum(case["text_anchors"] for case in cases)
    missing_total = sum(len(case["missing_anchors"]) for case in cases)
    numeric_total = sum(case["numeric_values"] for case in cases)
    missing_numeric = sum(len(case["missing_numeric_values"]) for case in cases)
    cell_total = sum(case["table_cells"] for case in cases)
    cell_true = sum(case["table_cell_true_positives"] for case in cases)
    cell_pred = sum(case["table_cell_predictions_in_scope"] for case in cases)
    cell_precision = cell_true / cell_pred if cell_pred else 0.0
    cell_recall = cell_true / cell_total if cell_total else 0.0
    cell_f1 = (
        2 * cell_precision * cell_recall / (cell_precision + cell_recall)
        if cell_precision + cell_recall else 0.0
    ) if cell_total else None
    orientation_cases = [case for case in cases if case["orientation_correct"] is not None]
    order_pairs = sum(case["reading_order_pairs"] for case in cases)
    correct_pairs = sum(case["reading_order_correct_pairs"] for case in cases)
    confidence_cases = [case for case in cases if case["recognized_lines"] > 0]
    summary = {
        "cases": len(cases),
        "cases_with_predictions": sum(case["prediction_exists"] for case in cases),
        "annotation_scope": manifest["annotation_scope"],
        "span_cer": sum(case["span_cer"] * case["text_anchors"] for case in cases if case["span_cer"] is not None) / anchor_total if anchor_total else None,
        "span_wer": sum(case["span_wer"] * case["text_anchors"] for case in cases if case["span_wer"] is not None) / anchor_total if anchor_total else None,
        "mean_ocr_confidence": sum(case["ocr_confidence"] for case in confidence_cases) / len(confidence_cases) if confidence_cases else None,
        "numeric_exact_match": (numeric_total - missing_numeric) / numeric_total if numeric_total else None,
        "table_cell_f1": cell_f1,
        "orientation_accuracy": sum(case["orientation_correct"] for case in orientation_cases) / len(orientation_cases) if orientation_cases else None,
        "reading_order_accuracy": correct_pairs / order_pairs if order_pairs else None,
        "content_recall": (anchor_total - missing_total) / anchor_total if anchor_total else None,
        "silent_content_loss": missing_total,
        "annotation_counts": {"text_anchors": anchor_total, "numeric_values": numeric_total, "table_cells": cell_total, "orientation_pages": len(orientation_cases), "reading_order_pairs": order_pairs},
    }
    gates = {
        name: value is not None and (value <= target if name == "silent_content_loss" else value >= target)
        for name, target in TARGETS.items()
        for value in [summary[name]]
    }
    return {"prediction_root": str(prediction_root), "summary": summary, "gates": gates, "cases": cases}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("benchmark/accuracy_first_manifest.json"))
    parser.add_argument("--prediction", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.manifest, args.prediction)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "gates": report["gates"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

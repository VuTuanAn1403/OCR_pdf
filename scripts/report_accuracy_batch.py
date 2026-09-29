"""Summarize a complete accuracy-profile batch without claiming unmeasured accuracy."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ocr.infrastructure.export.artifact_validator import validate_document_artifacts


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("inputs"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for pdf in sorted(args.input.glob("*.pdf")):
        with pymupdf.open(pdf) as source:
            expected = len(source)
        doc_dir = args.output / pdf.stem
        manifest_path = doc_dir / "manifest.json"
        row = {"file": pdf.name, "expected_pages": expected, "exported_pages": 0,
               "status": "MISSING", "errors": [], "warnings": []}
        if manifest_path.is_file():
            manifest = read_json(manifest_path)
            row["exported_pages"] = len(list((doc_dir / "pages").glob("page-????.json")))
            if row["exported_pages"] == expected and manifest.get("selected_pages") == list(range(1, expected + 1)):
                result = validate_document_artifacts(doc_dir)
                row.update({"status": result["status"], "errors": result["errors"],
                            "warnings": result["warnings"], "counts": result["counts"]})
            else:
                row["status"] = "INCOMPLETE"
        quality_path = doc_dir / "quality.json"
        if quality_path.is_file():
            reports = read_json(quality_path).get("page_reports", [])
            row["review_lines"] = sum(r.get("review_required_count", 0) for r in reports)
            row["low_confidence_pages"] = [r["page"] for r in reports
                                           if r.get("recognized_lines", 0) and r.get("ocr_confidence", 0) < 0.90]
            row["non_extracted_pages"] = [r["page"] for r in reports
                                          if r.get("content_status") != "EXTRACTED"]
            row["mean_ocr_confidence"] = read_json(quality_path).get("mean_ocr_confidence")
        rows.append(row)

    counts = Counter(row["status"] for row in rows)
    summary = {"input_files": len(rows), "expected_pages": sum(r["expected_pages"] for r in rows),
               "exported_pages": sum(r["exported_pages"] for r in rows),
               "statuses": dict(counts), "ground_truth_accuracy": "NOT_AVAILABLE_FOR_THIS_10_FILE_BATCH"}
    report = {"summary": summary, "files": rows}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "ACCURACY_BATCH_VALIDATION.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Kiểm nghiệm 10 PDF trong inputs", "",
             f"- Hoàn tất artifact: {summary['exported_pages']}/{summary['expected_pages']} trang",
             f"- Trạng thái: {summary['statuses']}",
             "- CER/WER, Numeric Exact Match, Table Cell F1, Orientation Accuracy và Content Recall chưa xác định cho batch này vì chưa có ground truth độc lập.",
             "", "| File | Trang | Validator | Dòng cần review | Trang confidence < 0,90 | Trang chưa trích chữ |",
             "| --- | ---: | --- | ---: | ---: | ---: |"]
    for row in rows:
        lines.append(f"| {row['file']} | {row['exported_pages']}/{row['expected_pages']} | {row['status']} | "
                     f"{row.get('review_lines', 0)} | {len(row.get('low_confidence_pages', []))} | "
                     f"{len(row.get('non_extracted_pages', []))} |")
    lines.extend(["", "## File lỗi hoặc cần xem lại", ""])
    for row in rows:
        if row["status"] != "PASS":
            lines.append(f"- **{row['file']}**: {row['status']}; "
                         f"lỗi {row['errors'][:3]}; cảnh báo {row['warnings'][:3]}.")
    (args.output / "ACCURACY_BATCH_VALIDATION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

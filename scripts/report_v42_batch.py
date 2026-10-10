"""Summarize V4.2 validation artifacts for every PDF in an input directory."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ocr.infrastructure.export.artifact_validator import validate_document_artifacts


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="inputs")
    parser.add_argument("--output", default="output/v42_validation/batch_10")
    args = parser.parse_args()
    input_dir = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for pdf in sorted(input_dir.glob("*.pdf")):
        with pymupdf.open(pdf) as source:
            expected_pages = len(source)
        doc_dir = output_dir / pdf.stem
        row = {
            "file": pdf.name,
            "expected_pages": expected_pages,
            "processed_pages": 0,
            "status": "MISSING",
            "seconds_per_page": None,
            "validation_errors": [],
        }
        manifest_path = doc_dir / "manifest.json"
        if manifest_path.is_file():
            manifest = read_json(manifest_path)
            row["processed_pages"] = manifest.get("pages_processed", 0)
            row["pipeline_version"] = manifest.get("pipeline_version")
            row["status"] = "INCOMPLETE"
            if row["processed_pages"] == expected_pages and manifest.get("selected_pages") == list(range(1, expected_pages + 1)):
                validation = validate_document_artifacts(doc_dir)
                row["status"] = validation["status"]
                row["validation_errors"] = validation["errors"]
                row["counts"] = validation["counts"]
                row["unknown_orientation_regions"] = sum(
                    "orientation unknown" in warning for warning in validation["warnings"]
                )
            benchmark_path = doc_dir / "benchmark.json"
            if benchmark_path.is_file():
                row["seconds_per_page"] = read_json(benchmark_path).get("seconds_per_page")
            quality_path = doc_dir / "quality.json"
            if quality_path.is_file():
                quality = read_json(quality_path)
                reports = quality.get("page_reports") or []
                report_by_page = {p["page"]: p for p in reports}
                row["review_lines"] = sum(p.get("review_required_count", 0) for p in reports)
                row["content_statuses"] = dict(Counter(p.get("content_status", "UNKNOWN") for p in reports))
                row["orientation_fallback_pages"] = [p["page"] for p in reports if p.get("orientation_fallback")]
                row["low_quality_pages"] = [p["page"] for p in reports if p.get("quality_score", 0) < 0.75]
                row["slow_pages"] = [p["page"] for p in reports if p.get("time_taken_seconds", 0) > 15]
                row["recognized_lines"] = sum(p.get("recognized_lines", 0) for p in reports)
                row["stage_seconds"] = {
                    stage: round(sum(p.get("stage_seconds", {}).get(stage, 0) for p in reports), 2)
                    for stage in ("inspect", "render_and_refine", "primary_ocr", "fallback", "table", "postprocess")
                }
                large_preserved_pages = []
                high_score_large_preserved_pages = []
                for page_path in sorted((doc_dir / "pages").glob("page-????.json")):
                    page = read_json(page_path)
                    page_area = page.get("width", 0) * page.get("height", 0)
                    if not page_area:
                        continue
                    large_preserved = any(
                        region.get("status") == "PRESERVED"
                        and region.get("content_type") in ("IMAGE", "IMAGE_TABLE", "FIGURE")
                        and max(0, region["bbox"]["x1"] - region["bbox"]["x0"])
                        * max(0, region["bbox"]["y1"] - region["bbox"]["y0"])
                        / page_area >= 0.25
                        for region in page.get("regions", [])
                    )
                    if large_preserved:
                        number = page["page_num"]
                        large_preserved_pages.append(number)
                        if report_by_page.get(number, {}).get("quality_score", 0) >= 0.95:
                            high_score_large_preserved_pages.append(number)
                row["large_preserved_image_pages"] = large_preserved_pages
                row["high_score_large_preserved_pages"] = high_score_large_preserved_pages
        rows.append(row)

    completed = [r for r in rows if r["status"] in ("PASS", "NEEDS_REVIEW")]
    total_pages = sum(r["expected_pages"] for r in rows)
    processed_pages = sum(r["processed_pages"] for r in completed)
    validation_counts = Counter()
    for row in completed:
        validation_counts.update(row.get("counts", {}))
    report = {
        "summary": {
            "input_files": len(rows),
            "completed_files": len(completed),
            "expected_pages": total_pages,
            "processed_pages": processed_pages,
            "pass_files": sum(r["status"] == "PASS" for r in rows),
            "needs_review_files": sum(r["status"] == "NEEDS_REVIEW" for r in rows),
            "failed_or_missing_files": sum(r["status"] not in ("PASS", "NEEDS_REVIEW") for r in rows),
            "validation_counts": dict(validation_counts),
            "large_preserved_image_pages": sum(len(r.get("large_preserved_image_pages", [])) for r in rows),
            "high_score_large_preserved_pages": sum(len(r.get("high_score_large_preserved_pages", [])) for r in rows),
        },
        "files": rows,
    }
    (output_dir / "V42_10_FILES_TEST_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = report["summary"]
    lines = [
        "# Kiểm nghiệm V4.2 trên toàn bộ PDF trong inputs",
        "",
        f"- File hoàn tất: {summary['completed_files']}/{summary['input_files']}",
        f"- Trang hoàn tất: {summary['processed_pages']}/{summary['expected_pages']}",
        f"- Validator: {summary['pass_files']} PASS, {summary['needs_review_files']} NEEDS_REVIEW, {summary['failed_or_missing_files']} thiếu/lỗi",
        f"- Trang có ảnh PRESERVED chiếm ít nhất 25% diện tích: {summary['large_preserved_image_pages']}; trong đó {summary['high_score_large_preserved_pages']} trang vẫn có quality ≥0,95. Cần xem ảnh để biết có chữ chưa được OCR hay không.",
        "- CER/WER và độ chính xác bảng: chưa xác định nếu chưa có ground truth độc lập.",
        "",
        "| File | Trang | Trạng thái | Giây/trang | Pipe Table | Ảnh lỗi | Vùng hướng UNKNOWN | Dòng cần review | Trang quality <0,75 | Ảnh lớn PRESERVED |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        counts = row.get("counts", {})
        speed = f"{row['seconds_per_page']:.2f}" if row["seconds_per_page"] is not None else "—"
        lines.append(
            f"| {row['file']} | {row['processed_pages']}/{row['expected_pages']} | {row['status']} | {speed} | "
            f"{counts.get('pipe_tables_markdown', 0)} | {counts.get('broken_image_links', 0)} | "
            f"{row.get('unknown_orientation_regions', 0)} | "
            f"{row.get('review_lines', 0)} | {len(row.get('low_quality_pages', []))} | "
            f"{len(row.get('large_preserved_image_pages', []))} |"
        )
    lines += ["", "## Các trang cần xem trước", ""]
    for row in rows:
        if row["status"] not in ("PASS", "NEEDS_REVIEW"):
            lines.append(f"- **{row['file']}**: {row['status']}; {row['validation_errors'][:2]}")
            continue
        low = row.get("low_quality_pages", [])
        orientation = row.get("orientation_fallback_pages", [])
        slow = row.get("slow_pages", [])
        preserved = row.get("large_preserved_image_pages", [])
        high_score_preserved = row.get("high_score_large_preserved_pages", [])
        nontext = {
            key: count for key, count in row.get("content_statuses", {}).items()
            if key != "EXTRACTED"
        }
        if low or orientation or slow or nontext or preserved or row.get("counts", {}).get("table_candidates_as_paragraphs"):
            lines.append(
                f"- **{row['file']}**: quality <0,75 ở trang {low[:15]}"
                f"{'…' if len(low) > 15 else ''}; xoay OCR {orientation[:15]}"
                f"{'…' if len(orientation) > 15 else ''}; trạng thái khác EXTRACTED {nontext}; "
                f"trang >15 giây {slow[:15]}{'…' if len(slow) > 15 else ''}; "
                f"ảnh lớn PRESERVED {preserved[:15]}{'…' if len(preserved) > 15 else ''} "
                f"(quality ≥0,95: {high_score_preserved[:15]}{'…' if len(high_score_preserved) > 15 else ''}); "
                f"ứng viên bảng thành văn xuôi {row.get('counts', {}).get('table_candidates_as_paragraphs', 0)}."
            )
    (output_dir / "V42_10_FILES_TEST_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

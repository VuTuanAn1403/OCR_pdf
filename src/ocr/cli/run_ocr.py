import argparse
import sys
from pathlib import Path
from typing import List, Optional
from tabulate import tabulate

from src.ocr.application.convert_document import ConvertDocumentUseCase
from src.ocr.infrastructure.export.manifest_exporter import PIPELINE_VERSION

def parse_pages_arg(pages_str: Optional[str]) -> Optional[List[int]]:
    if not pages_str:
        return None

    pages = set()
    parts = pages_str.split(",")
    for part in parts:
        part = part.strip()
        if "-" in part:
            sub = part.split("-")
            if len(sub) == 2 and sub[0].isdigit() and sub[1].isdigit():
                start, end = int(sub[0]), int(sub[1])
                for p in range(start, end + 1):
                    pages.add(p)
        elif part.isdigit():
            pages.add(int(part))

    return sorted(list(pages)) if pages else None



def resolve_input_path(input_str: str) -> Path:
    raw_path = Path(input_str)
    if raw_path.is_file():
        return raw_path.resolve()

    filename = raw_path.name
    cwd = Path.cwd()
    project_root = Path(__file__).resolve().parent.parent.parent.parent

    # Candidate paths to inspect in priority order:
    # 1. ./<input>
    # 2. ./examples/<input>
    # 3. ./inputs/<input>
    # 4. Same relative to project root
    candidates = [
        cwd / input_str,
        cwd / "examples" / input_str,
        cwd / "examples" / filename,
        cwd / "inputs" / input_str,
        cwd / "inputs" / filename,
        project_root / input_str,
        project_root / "examples" / input_str,
        project_root / "examples" / filename,
        project_root / "inputs" / input_str,
        project_root / "inputs" / filename,
    ]

    checked_paths = []
    for cand in candidates:
        try:
            resolved_cand = cand.resolve()
        except Exception:
            resolved_cand = cand

        if resolved_cand not in checked_paths:
            checked_paths.append(resolved_cand)
            if resolved_cand.is_file():
                return resolved_cand

    error_lines = [
        f"PDF file not found: '{input_str}'",
        "Checked candidate paths:"
    ]
    for p in checked_paths:
        error_lines.append(f"  - {p}")

    raise FileNotFoundError("\n".join(error_lines))

def main():
    parser = argparse.ArgumentParser(
        description=f"Codex OCR v3.1 (v{PIPELINE_VERSION}) - Speed & Accuracy Hybrid Document Extraction Pipeline"
    )
    parser.add_argument("input", help="Path to input PDF document")
    parser.add_argument("--profile", choices=["fast", "balanced", "accuracy"], default="balanced",
                        help="Processing profile (default: balanced)")
    parser.add_argument("--pages", type=str, default=None,
                        help="Specific pages or ranges (e.g. '1-5' or '1,3,5,11')")
    parser.add_argument("--output", type=str, default="output",
                        help="Output directory (default: 'output')")
    parser.add_argument("--benchmark", action="store_true",
                        help="Run benchmark mode and print timing metrics")
    parser.add_argument("--accuracy-report", action="store_true",
                        help="Evaluate CER, WER, and diacritic accuracy against ground truth")
    parser.add_argument("--ground-truth", type=str, default="benchmark/ground_truth",
                        help="Ground truth directory (default: 'benchmark/ground_truth')")
    parser.add_argument("--resume", action="store_true",
                        help="Resume processing by skipping pages cached in .cache/")
    parser.add_argument("--debug", action="store_true",
                        help="Enable verbose debug logging")

    args = parser.parse_args()

    try:
        pdf_path = resolve_input_path(args.input)
    except FileNotFoundError as e:
        print(f"\n[ERROR] Pipeline failed: {e}", file=sys.stderr)
        sys.exit(1)

    pages_list = parse_pages_arg(args.pages)
    converter = ConvertDocumentUseCase()

    print("=" * 60)
    print(f"CODEX OCR v3.1 HYBRID DOCUMENT EXTRACTION PIPELINE (v{PIPELINE_VERSION})")
    print(f"File: {pdf_path} | Profile: {args.profile}")
    if pages_list:
        print(f"Pages: {pages_list}")
    if args.resume:
        print("Resume: ENABLED (using .cache)")
    print("=" * 60)

    try:
        doc = converter.execute(
            file_path=str(pdf_path),
            profile_name=args.profile,
            pages=pages_list,
            output_dir=args.output,
            resume=args.resume,
            benchmark=args.benchmark,
            accuracy_report=args.accuracy_report,
            ground_truth_dir=args.ground_truth
        )

        print("=" * 60)
        print("EXTRACTION COMPLETED SUCCESSFULLY")
        print(f"Document ID: {doc.document_id}")
        print(f"Total pages processed: {len(doc.pages)}")
        print(f"Total time: {doc.quality.total_time_seconds:.2f}s "
              f"({doc.quality.total_time_seconds / max(1, len(doc.pages)):.2f}s/page)")
        print(f"Pages native: {doc.quality.pages_processed_native} | "
              f"Pages OCR: {doc.quality.pages_processed_ocr} | "
              f"Fallback regions: {doc.quality.total_fallback_regions}")
        print(f"Output written to: {args.output}/{doc.document_id}/")
        print("=" * 60)

        if args.benchmark:
            sec_per_page = doc.quality.total_time_seconds / max(1, len(doc.pages))
            table_data = [
                ["Metric", "Value"],
                ["Total Wall Time (s)", f"{doc.quality.total_time_seconds:.2f}"],
                ["Seconds Per Page", f"{sec_per_page:.2f}"],
                ["Pages Per Minute", f"{(len(doc.pages) / max(0.01, doc.quality.total_time_seconds)) * 60:.2f}"],
                ["Mean OCR Confidence", f"{doc.quality.mean_ocr_confidence:.4f}"],
                ["Mean Quality Score", f"{doc.quality.mean_quality_score:.4f}"],
                ["Primary Lines", f"{doc.quality.metrics.primary_lines}"],
                ["Fallback Lines", f"{doc.quality.metrics.fallback_lines}"],
                ["Fallback Ratio", f"{doc.quality.metrics.fallback_ratio:.2%}"]
            ]
            print("\nBENCHMARK REPORT:")
            print(tabulate(table_data, headers="firstrow", tablefmt="github"))

        if args.accuracy_report and hasattr(doc, "accuracy") and doc.accuracy:
            acc = doc.accuracy
            tab_acc_str = f"{acc.mean_table_accuracy:.2%}" if acc.mean_table_accuracy is not None else "N/A"
            acc_data = [
                ["Metric", "Score"],
                ["Mean CER", f"{acc.mean_cer:.2%}"],
                ["Mean WER", f"{acc.mean_wer:.2%}"],
                ["Mean Diacritic Accuracy", f"{acc.mean_diacritic_accuracy:.2%}"],
                ["Mean Table Accuracy", tab_acc_str],
                ["Mean Reading Order Accuracy", f"{acc.mean_reading_order_accuracy:.2%}"],
                ["Pages Evaluated", f"{acc.pages_evaluated}"]
            ]
            print("\nACCURACY EVALUATION REPORT (vs Ground Truth):")
            print(tabulate(acc_data, headers="firstrow", tablefmt="github"))

    except Exception as e:
        print(f"\n[ERROR] Pipeline failed: {e}", file=sys.stderr)
        if args.debug:
            import traceback
            traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()

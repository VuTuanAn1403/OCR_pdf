import argparse
import sys
from pathlib import Path
from typing import List, Optional
from tabulate import tabulate

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if sys_root not in sys.path:
    sys.path.insert(0, sys_root)

from src.ocr.application.convert_document import ConvertDocumentUseCase
from src.ocr.infrastructure.export.manifest_exporter import PIPELINE_VERSION
from src.ocr.infrastructure.export.markdown_exporter import ASSET_MODES
from src.ocr.infrastructure.export.output_contract import OUTPUT_MODES
from src.ocr.runtime.config import ConfigError
from src.ocr.runtime.device import VALID_DEVICES
from src.ocr.runtime.hardware import preparse_hardware
from src.ocr.runtime.paths import cache_dir, data_dir, output_dir
from src.ocr.runtime.run_manifest import RunRecorder
from src.ocr.runtime.structured_logging import LOG_FORMATS, bind_context, configure_logging, reset_context

def parse_pages_arg(pages_str: Optional[str]) -> Optional[List[int]]:
    if pages_str is None:
        return None

    pages = set()
    parts = pages_str.split(",")
    for part in parts:
        part = part.strip()
        if "-" in part:
            sub = part.split("-")
            if len(sub) == 2 and sub[0].isdigit() and sub[1].isdigit():
                start, end = int(sub[0]), int(sub[1])
                if 1 <= start <= end:
                    pages.update(range(start, end + 1))
                    continue
        elif part.isdigit():
            page = int(part)
            if page >= 1:
                pages.add(page)
                continue
        raise argparse.ArgumentTypeError(
            f"Invalid page selection {pages_str!r}: use positive page numbers or ascending ranges (e.g. 1-5,8)")

    return sorted(pages)



def resolve_input_path(input_str: str) -> Path:
    raw_path = Path(input_str).expanduser()
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
        data_dir() / input_str,
        data_dir() / filename,
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
    try:
        # Before the parser, so its path defaults see the profile's paths.
        hardware = preparse_hardware()
    except ConfigError as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
    parser = argparse.ArgumentParser(
        description=f"PDF-to-Markdown extraction pipeline v{PIPELINE_VERSION}"
    )
    parser.add_argument("input", help="Path to input PDF document")
    parser.add_argument("--hardware", default=None,
                        help="Hardware profile name in config/hardware/ or YAML path (device, threads, batch, paths)")
    # Profile names come from the config file (ENV-13), not a hard-coded list.
    parser.add_argument("--config", default=None,
                        help="Profile YAML path (default: $OCR_CONFIG_PATH or repository config/profiles.yaml)")
    parser.add_argument("--profile", default="balanced",
                        help="Processing profile read from the config file (default: balanced)")
    parser.add_argument("--pages", type=parse_pages_arg, default=None,
                        help="Specific pages or ranges (e.g. '1-5' or '1,3,5,11')")
    parser.add_argument("--output", type=str, default=str(output_dir()),
                        help="Output directory (default: $OCR_OUTPUT_DIR or 'output')")
    parser.add_argument("--cache-dir", type=str, default=str(cache_dir()),
                        help="Page cache directory (default: $OCR_CACHE_DIR or '.cache')")
    parser.add_argument("--device", choices=VALID_DEVICES, default=None,
                        help="Inference device; overrides the profile (default: profile value, else cpu)")
    parser.add_argument("--assets", choices=ASSET_MODES, default="png",
                        help="Image crops for preserved regions: png, jpeg, or none (default: png)")
    parser.add_argument("--output-mode", choices=OUTPUT_MODES, default="legacy",
                        help="legacy embeds pages in result.json; compact stores a versioned page index")
    parser.add_argument("--warmup-docling", action="store_true",
                        help="Load Docling models before the first page so start-up is timed separately")
    parser.add_argument("--benchmark", action="store_true",
                        help="Run benchmark mode and print timing metrics")
    parser.add_argument("--accuracy-report", action="store_true",
                        help="Evaluate CER, WER, and diacritic accuracy against ground truth")
    parser.add_argument("--ground-truth", type=str, default="benchmark/ground_truth",
                        help="Ground truth directory (default: 'benchmark/ground_truth')")
    parser.add_argument("--resume", action="store_true",
                        help="Resume processing by skipping pages in the page cache")
    parser.add_argument("--debug", action="store_true",
                        help="Enable verbose debug logging")
    parser.add_argument("--keywords-file", default=None,
                        help="UTF-8 keyword file, one phrase per line (default: config/keywords.txt)")
    parser.add_argument("--log-format", choices=LOG_FORMATS, default="text",
                        help="text keeps console progress; json writes JSON lines with run/doc/page/stage to stderr")

    args = parser.parse_args()
    configure_logging(args.log_format)

    try:
        converter = ConvertDocumentUseCase(
            config_path=args.config, thread_budget=hardware.threads_per_worker if hardware else None,
            batch_size=hardware.batch_size if hardware else None)
        pdf_path = resolve_input_path(args.input)
        profile_config = converter.resolve_profile(args.profile, args.device or (hardware and hardware.device))
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"\n[ERROR] Pipeline failed: {e}", file=sys.stderr)
        sys.exit(1)

    pages_list = args.pages
    recorder = RunRecorder(
        "run_ocr", config_path=converter.config_path, profile=args.profile,
        profile_config=profile_config, device=profile_config["device"],
        cache_dir=Path(args.cache_dir), resume=args.resume, assets_mode=args.assets,
        hardware_profile=hardware.record() if hardware else None,
        output_mode=args.output_mode,
    )

    print("=" * 60)
    print(f"PDF-TO-MARKDOWN EXTRACTION PIPELINE (v{PIPELINE_VERSION})")
    print(f"File: {pdf_path} | Profile: {args.profile} | Device: {profile_config['device']}")
    print(f"Run: {recorder.run_id}")
    if pages_list:
        print(f"Pages: {pages_list}")
    if args.resume:
        print(f"Resume: ENABLED (cache: {args.cache_dir})")
    print("=" * 60)

    context_token = bind_context(run_id=recorder.run_id)  # ENV-11
    try:
        doc = converter.execute(
            file_path=str(pdf_path),
            profile_name=args.profile,
            pages=pages_list,
            output_dir=args.output,
            resume=args.resume,
            benchmark=args.benchmark,
            accuracy_report=args.accuracy_report,
            ground_truth_dir=args.ground_truth,
            device=profile_config["device"],
            assets_mode=args.assets,
            warmup=args.warmup_docling,
            cache_dir=args.cache_dir,
            output_mode=args.output_mode,
            keywords_file=args.keywords_file,
        )
        recorder.add_document(doc)
        recorder.write(Path(args.output) / doc.document_id / "run_manifest.json")

        print("=" * 60)
        print("EXTRACTION COMPLETED SUCCESSFULLY")
        print(f"Document ID: {doc.document_id}")
        print(f"Total pages processed: {len(doc.pages)}")
        print(f"Total time: {doc.quality.total_time_seconds:.2f}s "
              f"({doc.quality.total_time_seconds / max(1, len(doc.pages)):.2f}s/page)")
        print(f"Pages native: {doc.quality.pages_processed_native} | "
              f"Pages OCR: {doc.quality.pages_processed_ocr} | "
              f"Fallback regions: {doc.quality.total_fallback_regions}")
        if doc.quality.pages_from_cache:
            print(f"Pages restored from cache: {doc.quality.pages_from_cache} "
                  "(their timings come from an earlier run)")
        print(f"Output written to: {args.output}/{doc.document_id}/")
        print("=" * 60)

        if args.benchmark:
            sec_per_page = doc.quality.total_time_seconds / max(1, len(doc.pages))
            mean_q = doc.quality.mean_quality_score
            mean_conf = doc.quality.mean_ocr_confidence
            table_data = [
                ["Metric", "Value"],
                ["Total Wall Time (s)", f"{doc.quality.total_time_seconds:.2f}"],
                ["Start-up (s)", f"{doc.timing.get('startup_seconds', 0.0):.2f}"],
                ["Document Latency incl. Export (s)", f"{doc.timing.get('document_latency_seconds', 0.0):.2f}"],
                ["Seconds Per Page", f"{sec_per_page:.2f}"],
                ["Pages Per Minute", f"{(len(doc.pages) / max(0.01, doc.quality.total_time_seconds)) * 60:.2f}"],
                ["Mean OCR Confidence", f"{mean_conf:.4f}" if mean_conf is not None else "N/A"],
                ["Mean Quality Score", f"{mean_q:.4f}" if mean_q is not None else "n/a"],
                ["Primary Lines", f"{doc.quality.metrics.primary_lines}"],
                ["Recognized Lines", f"{sum(p.quality.recognized_lines for p in doc.pages)}"],
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
        recorder.add_document({"document_id": pdf_path.stem, "file_path": str(pdf_path),
                               "status": "FAILED_PROCESSING", "error": str(e)})
        try:
            recorder.write(Path(args.output) / ".runs" / recorder.run_id / "run_manifest.json")
        except OSError as manifest_error:
            print(f"[WARN] Cannot persist failed-run provenance: {manifest_error}", file=sys.stderr)
        if args.debug:
            import traceback
            traceback.print_exc()
        sys.exit(1)
    finally:
        reset_context(context_token)

if __name__ == "__main__":
    main()

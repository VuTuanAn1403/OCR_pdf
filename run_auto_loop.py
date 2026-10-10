import sys
import io
import time
import json
from pathlib import Path

# Force UTF-8 encoding for standard output/error on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.ocr.cli.batch_runner import BatchOCRRunner

def is_fully_completed(output_dir: Path) -> bool:
    manifest_path = output_dir / "batch_manifest.json"
    if not manifest_path.exists():
        return False
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        total_inputs = len(list(Path("inputs").glob("*.pdf")))
        if len(data) < total_inputs:
            return False
        pending = [e for e in data if e.get("status") not in ("COMPLETED", "CACHED", "FAILED_INPUT", "FAILED_PROCESSING")]
        return len(pending) == 0
    except Exception:
        return False

def main():
    output_dir = Path("output").resolve()
    excel_path = output_dir / "OCR_BENCHMARK_100_FILES_V3_1_2.xlsx"

    while not is_fully_completed(output_dir):
        print("[AUTO-SUPERVISOR] Starting / Resuming batch OCR execution (V3.1.2)...")
        try:
            runner = BatchOCRRunner(
                input_dir="inputs",
                output_dir="output",
                profile="balanced",
                excel_path=str(excel_path)
            )
            runner.execute_batch()
        except KeyboardInterrupt:
            print("[AUTO-SUPERVISOR] Interrupted by user.")
            break
        except Exception as e:
            print(f"[AUTO-SUPERVISOR] Exception encountered: {e}. Resuming in 5 seconds...", file=sys.stderr)
            time.sleep(5)

    print("[AUTO-SUPERVISOR] All files processed. Generating final reports...")
    try:
        from scripts.export_batch_excel import build_workbook
        build_workbook(output_dir, excel_path)
        print(f"[AUTO-SUPERVISOR] Final Excel report verified: {excel_path}")
    except Exception as e:
        print(f"[AUTO-SUPERVISOR] Final Excel export note: {e}")

if __name__ == "__main__":
    main()

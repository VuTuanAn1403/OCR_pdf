"""Re-export completed batch documents from their saved OCR result.json files.

This regenerates Markdown, image assets, and validation without rerunning OCR.
Benchmark and accuracy files are left intact.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ocr.application.export_document import ExportDocumentUseCase
from src.ocr.domain.models.document import ExtractedDocument


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="output/v42_validation/batch_10")
    parser.add_argument("--document", help="Re-export only this document ID")
    args = parser.parse_args()
    output_dir = Path(args.output).resolve()
    failures = []
    results = sorted(output_dir.glob("*/result.json"))
    if args.document:
        results = [path for path in results if path.parent.name == args.document]
    if not results:
        raise SystemExit(f"No completed result.json files found in {output_dir}")

    for result_path in results:
        try:
            doc = ExtractedDocument.model_validate(
                json.loads(result_path.read_text(encoding="utf-8"))
            )
            doc_dir = ExportDocumentUseCase.execute(doc, output_dir)
            status = json.loads((doc_dir / "validation.json").read_text(encoding="utf-8"))["status"]
            print(f"{result_path.parent.name}: {status}", flush=True)
        except Exception as exc:
            failures.append(result_path.parent.name)
            print(f"{result_path.parent.name}: FAIL: {exc}", file=sys.stderr, flush=True)

    if failures:
        raise SystemExit(f"Re-export failed for {len(failures)} document(s): {', '.join(failures)}")


if __name__ == "__main__":
    main()

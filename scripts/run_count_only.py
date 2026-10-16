"""Count keywords in an existing result.md; no OCR or model setup required."""
import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.ocr.application.count_keywords import CountKeywordsUseCase
from src.ocr.runtime.paths import output_dir


def main():
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="Document output directory or document ID inside the output directory")
    parser.add_argument("--keywords-file", help="UTF-8 text file, one keyword per line (default: config/keywords.txt)")
    args = parser.parse_args()
    target = Path(args.target).expanduser()
    if not target.is_dir():
        target = output_dir() / args.target
        if not target.is_dir() and not target.is_absolute():
            target = PROJECT_ROOT / target
    try:
        manifest = target / "manifest.json"
        if manifest.is_file() and json.loads(manifest.read_text(encoding="utf-8")).get("keyword_contract"):
            if args.keywords_file:
                raise ValueError("V6.3 recount uses its frozen dictionary; start a new batch to change keywords")
            from src.ocr.application.recount_document import recount_batch
            result = recount_batch(target.parent)
            print(f"V6.3 batch recounted and published: {result['matrix']}")
            return
        report = CountKeywordsUseCase.execute(target, target.name, args.keywords_file)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"[ERROR] {exc}\n")
    print(f"Keyword report: {(target / 'count_keywords.xlsx').resolve()}")
    print(f"Keywords: {report.total_keywords_searched} | Exact matches: {report.total_matches}")


if __name__ == "__main__":
    main()

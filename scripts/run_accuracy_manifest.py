"""Run a fixed, data-driven set of PDF pages without using the page cache."""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ocr.application.convert_document import ConvertDocumentUseCase


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("benchmark/accuracy_first_manifest.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", default="balanced")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    grouped: dict[str, set[int]] = defaultdict(set)
    for case in manifest["cases"]:
        grouped[case["pdf"]].add(int(case["page"]))
    converter = ConvertDocumentUseCase()
    for pdf, pages in sorted(grouped.items()):
        print(f"{pdf}: pages {sorted(pages)}", flush=True)
        converter.execute(
            file_path=pdf,
            profile_name=args.profile,
            pages=sorted(pages),
            output_dir=str(args.output),
            resume=False,
            benchmark=True,
        )


if __name__ == "__main__":
    main()

"""Run artifact and semantic validation for all documents in a ground-truth manifest."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ocr.infrastructure.export.artifact_validator import validate_document_artifacts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("benchmark/accuracy_first_manifest.json"))
    parser.add_argument("--prediction", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    reference = json.loads(args.manifest.read_text(encoding="utf-8"))
    document_names = sorted({Path(case["pdf"]).stem for case in reference["cases"]})
    results = {
        name: validate_document_artifacts(args.prediction / name, args.manifest)
        for name in document_names
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({name: result["status"] for name, result in results.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()

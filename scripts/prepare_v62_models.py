"""Lock existing model artifacts for GPU integration testing; no model-quality claim."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.ocr.runtime.atomic_io import atomic_write_json
from src.ocr.runtime.gpu_scheduler import sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detector", type=Path, required=True, help="Existing ch_PP-OCRv4_det_infer.onnx checkpoint")
    args = parser.parse_args()
    folder = ROOT / "models/v62"
    folder.mkdir(parents=True, exist_ok=True)
    detector = folder / "ch_PP-OCRv4_det_infer.onnx"
    if args.detector.resolve() != detector.resolve():
        shutil.copyfile(args.detector, detector)
    hub = ROOT / "models/docling/huggingface/hub"
    snapshots = {"layout": ("docling-layout-heron", "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8"),
                 "table": ("docling-models", "fc0f2d45e2218ea24bce5045f58a389aed16dc23"),
                 "formula": ("CodeFormulaV2", "ecedbe111d15c2dc60bfd4a823cbe80127b58af4")}
    files = [detector, *sorted((ROOT / "models/vietocr").glob("*"))]
    layout_path = None
    for kind, (name, revision) in snapshots.items():
        snapshot = hub / f"models--docling-project--{name}/snapshots/{revision}"
        if not snapshot.is_dir():
            raise FileNotFoundError(f"Required pinned model snapshot is missing: {snapshot}")
        if kind == "layout":
            layout_path = snapshot
        files.extend(f for f in snapshot.rglob("*") if f.is_file())
        ref = hub / f"models--docling-project--{name}/refs/{'v2.3.0' if kind == 'table' else 'main'}"
        if ref.read_text().strip() != revision:
            raise ValueError(f"Model cache main revision differs from the lock: {ref}")
        files.append(ref)
    if not (ROOT / "models/vietocr/vgg_seq2seq.pth").is_file():
        raise FileNotFoundError("VietOCR weights are missing")
    record = {"schema_version": "v62-runtime-models-v1", "quality_acceptance": "NOT_EVALUATED",
              "purpose": "GPU integration candidate; model selection on GT remains pending",
              "snapshots": {k: {"repo": f"docling-project/{v[0]}", "revision": v[1]} for k,v in snapshots.items()},
              "text_detector": {"adapter": "onnx-db-map-v1", "model_path": str(detector.relative_to(ROOT)),
                                "sha256": sha256(detector), "scope": "full_page", "max_side": 1536,
                                "gpu_mem_limit_mb": 1024},
              "layout_model": {"adapter": "hf_rtdetr_torch", "model_path": str(layout_path.relative_to(ROOT)),
                               "on_error": "raise", "render_dpi": 144},
              "files": {str(f.relative_to(ROOT).as_posix()): sha256(f) for f in files if f.is_file()}}
    path = folder / "runtime-models.json"
    atomic_write_json(path, record)
    print(f"Locked {len(record['files'])} artifacts: {path}")


if __name__ == "__main__":
    main()

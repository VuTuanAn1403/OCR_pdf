"""V6.2 document GPU pipeline. Keep this launcher free of Torch imports."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

from src.ocr.runtime.config import load_profiles, get_profile
from src.ocr.runtime.gpu_scheduler import ROOT, run_document, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "config/profiles-v62.yaml")
    parser.add_argument("--profile", default="unified_gpu")
    parser.add_argument("--env-root", type=Path, default=ROOT / ".venv-v62")
    parser.add_argument("--output", type=Path, default=ROOT / "output/v62_gpu")
    parser.add_argument("--pages", help="1-based comma-separated pages/ranges")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output-mode", choices=("compact", "legacy"), default="compact")
    parser.add_argument("--assets", choices=("png", "jpeg", "none"), default="png")
    parser.add_argument("--keywords-file", type=Path,
                        help="UTF-8 keyword file (default: config/keywords.txt)")
    args = parser.parse_args()
    from src.ocr.application.count_keywords import CountKeywordsUseCase
    from src.ocr.infrastructure.postprocessing.keyword_counter import KeywordCounter
    try:
        keywords_file = CountKeywordsUseCase.resolve_keywords_file(args.keywords_file)
        KeywordCounter.load_keywords(keywords_file)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    config_path, profiles = load_profiles(args.config)
    config = dict(get_profile(profiles, args.profile))
    if config.get("runtime", {}).get("scheduler") != "document_two_phase_v1":
        parser.error("This command requires a document_two_phase_v1 profile")
    if config.get("device") != "cuda" or not config.get("docling", {}).get("enabled"):
        parser.error("This command requires CUDA and the complete Docling structure stage")
    if not args.input.is_file():
        parser.error(f"Input PDF not found: {args.input}")
    registry_path = (ROOT / config["model_registry"]).resolve()
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    models = {str((ROOT / name).resolve()): digest for name, digest in registry["files"].items()}
    for path, digest in models.items():
        if sha256(path) != digest:
            raise RuntimeError(f"MODEL_LOCK_MISMATCH: {path}")
    models[str(registry_path)] = sha256(registry_path)
    for name in ("text_detector", "layout_model"):
        config[name] = dict(registry[name])
        config[name]["model_path"] = str((ROOT / config[name]["model_path"]).resolve())
    config["docling"]["device"] = "cuda"
    config["backend_devices"] = dict.fromkeys(("ocr_detection", "ocr_recognition", "layout", "docling"), "cuda")
    suffix = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    text_python, docling_python = [str((args.env_root / role / suffix).resolve()) for role in ("text", "docling")]
    for python in (text_python, docling_python):
        if not Path(python).is_file():
            raise FileNotFoundError(f"Run scripts/setup_v62.py first: {python}")
    platform = "windows" if os.name == "nt" else "linux"
    locks = {str(ROOT / "requirements/v62" / f"{platform}-{role}-gpu.lock"): None for role in ("text", "docling")}
    locks = {p: sha256(p) for p in locks}
    config["docling"]["dependency_lock"] = next(p for p in locks if p.endswith("docling-gpu.lock"))
    pages = None
    if args.pages:
        pages = set()
        for part in args.pages.split(","):
            bounds = part.strip().split("-")
            if not 1 <= len(bounds) <= 2 or not all(v.isdigit() and int(v)>0 for v in bounds):
                parser.error("Invalid --pages; use 1-3,8")
            a,b = int(bounds[0]),int(bounds[-1])
            if b<a:
                parser.error("Page ranges must be ascending")
            pages.update(range(a,b+1))
        pages = sorted(pages)
    request = {"pdf": str(args.input.resolve()), "config": config, "config_path": str(config_path),
               "device": "cuda", "text_python": text_python, "docling_python": docling_python,
               "output": str(args.output.resolve()), "model_hashes": models, "lock_hashes": locks,
               "pages": pages, "assets": args.assets, "output_mode": args.output_mode,
               "keywords_file": str(keywords_file),
               "phase_timeout": config["runtime"]["phase_timeout_seconds"],
               "lease_timeout": config["runtime"]["lease_timeout_seconds"]}
    print(json.dumps(run_document(request, resume=args.resume), ensure_ascii=False))


if __name__ == "__main__":
    main()

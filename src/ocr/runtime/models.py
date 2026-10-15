"""Hash the actual model files used by the configured backends (ENV-04/06).

The registry follows persistent model-directory overrides. It hashes weights
and model configs, rather than trusting a stale download manifest. Hash reuse
is keyed by filesystem identity and timestamps, so a replacement is noticed
between documents without rereading large, unchanged weights on every page.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional

from src.ocr.runtime.paths import models_dir

_MODEL_SUFFIXES = {".onnx", ".pth", ".pt", ".bin", ".safetensors", ".model",
                   ".json", ".yaml", ".yml", ".txt", ".tiktoken"}
_IGNORED_PARTS = {".locks", ".cache", "trees", "__pycache__"}


@lru_cache(maxsize=512)
def _hash_file_identity(path: str, size: int, modified_ns: int, changed_ns: int,
                        inode: int) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_file_sha256(path: Path) -> Optional[str]:
    path = Path(path).resolve()
    try:
        stat = path.stat()
        if not path.is_file():
            return None
        return _hash_file_identity(str(path), stat.st_size, stat.st_mtime_ns,
                                   stat.st_ctime_ns, stat.st_ino)
    except FileNotFoundError:
        return None


def _tree_hashes(root: Path, prefix: str) -> dict[str, Optional[str]]:
    hashes = {}
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if (path.is_file() and not _IGNORED_PARTS.intersection(relative.parts)
                    and not path.name.startswith(".")
                    and (path.suffix.lower() in _MODEL_SUFFIXES or "refs" in relative.parts)):
                hashes[f"{prefix}/{relative.as_posix()}"] = model_file_sha256(path)
    return hashes


def _rapidocr_models_dir() -> Optional[Path]:
    # Locate package resources without importing/initalizing an OCR engine.
    try:
        spec = importlib.util.find_spec("rapidocr_onnxruntime")
    except (ImportError, ValueError):
        return None
    return Path(spec.origin).parent / "models" if spec and spec.origin else None


def model_hashes(profile_config: Optional[Mapping[str, Any]] = None) -> dict[str, Optional[str]]:
    hashes: dict[str, Optional[str]] = {}
    root = models_dir()
    if profile_config is None or profile_config.get("primary_engine", "vietocr") == "vietocr":
        vietocr = Path(os.getenv("VIETOCR_MODEL_DIR") or root / "vietocr").expanduser()
        hashes.update(_tree_hashes(vietocr, "vietocr"))
        hashes.setdefault("vietocr/vgg_seq2seq.pth", None)
    rapidocr = _rapidocr_models_dir()
    if rapidocr is not None:
        hashes.update(_tree_hashes(rapidocr, "rapidocr"))
    if profile_config is None or profile_config.get("docling", {}).get("enabled", False):
        hashes.update(_tree_hashes(root / "docling", "docling"))
        hashes.setdefault("docling/model_manifest.json", None)
    layout = (profile_config or {}).get("layout_model")
    if isinstance(layout, Mapping) and layout.get("model_path"):
        hashes["layout/model"] = model_file_sha256(Path(layout["model_path"]).expanduser())
    detector = (profile_config or {}).get("text_detector")
    if isinstance(detector, Mapping) and detector.get("model_path"):
        hashes["text_detector/model"] = model_file_sha256(Path(detector["model_path"]).expanduser())
    return hashes


def model_fingerprint(profile_config: Optional[Mapping[str, Any]] = None) -> str:
    payload = json.dumps(model_hashes(profile_config), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

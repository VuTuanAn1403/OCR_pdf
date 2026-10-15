"""Process workers own PDF/model clients; only artifact paths cross IPC."""
from __future__ import annotations

import os
import time
from pathlib import Path

_CONVERTER = None
_CONVERTER_KEY = None


def initialize_worker(threads: int):
    os.environ["OMP_NUM_THREADS"] = str(threads)
    os.environ["MKL_NUM_THREADS"] = str(threads)
    from src.ocr.runtime.structured_logging import configure_logging, json_logging_enabled
    if json_logging_enabled():  # inherited OCR_LOG_FORMAT; spawned workers start unconfigured
        configure_logging("json")


def convert_job(job: dict) -> dict:
    from src.ocr.runtime.structured_logging import log_context
    with log_context(run_id=job.get("run_id")):
        return _convert_job(job)


def _convert_job(job: dict) -> dict:
    global _CONVERTER, _CONVERTER_KEY
    from multiprocessing.util import Finalize
    from src.ocr.application.convert_document import ConvertDocumentUseCase
    from src.ocr.runtime.fingerprint import source_fingerprint
    from src.ocr.runtime.config import config_hash
    from src.ocr.infrastructure.cache.artifact_cache import ArtifactCache
    import psutil

    started = time.perf_counter()
    if ArtifactCache.compute_file_hash(job["file_path"]) != job["pdf_sha16"]:
        raise RuntimeError("Source PDF changed after scheduling this document")
    if source_fingerprint() != job["source_fingerprint"]:
        raise RuntimeError("Pipeline code changed after scheduling this batch")
    key = (job["config_path"], job["thread_budget"], job.get("batch_size"))
    if key != _CONVERTER_KEY:
        if _CONVERTER is not None:
            _CONVERTER.close()
        _CONVERTER = ConvertDocumentUseCase(config_path=job["config_path"], reuse_docling=True,
                                            thread_budget=job["thread_budget"], batch_size=job.get("batch_size"))
        _CONVERTER_KEY = key
        Finalize(_CONVERTER, _CONVERTER.close, exitpriority=10)
    resolved = _CONVERTER.resolve_profile(job["profile"], job["device"])
    if config_hash(resolved) != job["config_hash"]:
        raise RuntimeError("Profile configuration changed after scheduling this batch")
    doc = _CONVERTER.execute(file_path=job["file_path"], profile_name=job["profile"], device=job["device"],
        output_dir=job["output_dir"], cache_dir=job["cache_dir"], resume=job["resume"],
        assets_mode=job["assets_mode"], benchmark=True, accuracy_report=job["accuracy_report"],
        ground_truth_dir=job["ground_truth_dir"], output_mode=job.get("output_mode", "legacy"),
        keywords_file=job.get("keywords_file"))
    if source_fingerprint() != job["source_fingerprint"]:
        raise RuntimeError("Pipeline code changed while this batch was running")
    if ArtifactCache.compute_file_hash(job["file_path"]) != job["pdf_sha16"]:
        raise RuntimeError("Source PDF changed while this document was running")
    process = psutil.Process()
    rss = 0
    for child in [process, *process.children(recursive=True)]:
        try:
            rss += child.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return {"document_dir": str(Path(job["output_dir"]) / doc.document_id),
            "wall_clock_seconds": time.perf_counter() - started,
            "rss_at_completion_mb": round(rss / 1024**2, 2), "memory_scope": "worker_process_tree_snapshot"}


def load_exported_document(directory: Path):
    import json
    from types import SimpleNamespace
    from src.ocr.domain.models.document import ExtractedDocument
    from src.ocr.infrastructure.export.completion import verify_completed
    from src.ocr.infrastructure.export.result_reader import load_document_json
    directory = Path(directory)
    if not verify_completed(directory):
        raise ValueError("Worker output failed completion verification")
    data = load_document_json(directory / "result.json")
    data["timing"] = data["manifest"].get("timing", {})
    document = ExtractedDocument.model_validate(data)
    accuracy = directory / "accuracy.json"
    if accuracy.is_file():
        document.accuracy = SimpleNamespace(**json.loads(accuracy.read_text(encoding="utf-8")))
    return document

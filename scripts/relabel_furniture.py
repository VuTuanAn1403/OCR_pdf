"""Re-export stored OCR evidence with current furniture labels; no OCR rerun.

Original output is read-only. New artifacts omit image crops, preserve raw
text/coordinates and renew per-document keyword Excel plus the 88-row matrix.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ocr.application.export_document import ExportDocumentUseCase
from src.ocr.domain.models.document import ExtractedDocument
from src.ocr.infrastructure.export.result_reader import load_document_json
from src.ocr.infrastructure.postprocessing.page_association import ALGORITHM_VERSION
from src.ocr.runtime.atomic_io import atomic_write_json
from src.ocr.runtime.fingerprint import source_fingerprint
from scripts.run_batch_ocr_count_keywords import (
    load_raw_keywords, count_keywords_exact, export_single_document_keywords, build_and_save_master_matrix,
)


def relabel(input_dir, output_dir, keywords_file=ROOT/"config/keywords.txt"):
    source, destination = Path(input_dir).resolve(), Path(output_dir).resolve()
    if source == destination or source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError("Input and output directories must be separate, non-nested paths")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Output directory must be empty; original evidence will never be overwritten")
    inputs = sorted(source.glob("*/result.json"))
    if not inputs:
        raise ValueError(f"No per-document result.json found in {source}")
    destination.mkdir(parents=True, exist_ok=True)
    keywords = load_raw_keywords(Path(keywords_file))
    records = [{"index": i, "pdf_name": p.parent.name+".pdf", "relpath": p.parent.name+".pdf",
                "status": "PENDING", "counts": None, "total_matches": 0} for i, p in enumerate(inputs, 1)]
    matrix = destination/f"MA_TRAN_DEM_TU_KHOA_{len(inputs)}_FILES.xlsx"
    receipt = {"algorithm_version": ALGORITHM_VERSION, "ocr_rerun": False,
               "source_directory": str(source), "output_directory": str(destination),
               "started_at_utc": datetime.now(timezone.utc).isoformat(), "source_fingerprint": source_fingerprint(),
               "assets_mode": "none", "documents": records}
    started = time.perf_counter()
    build_and_save_master_matrix(records, keywords, matrix)
    for record, path in zip(records, inputs):
        tick = time.perf_counter()
        try:
            doc = ExtractedDocument.model_validate(load_document_json(path))
            if Path(doc.document_id).name != doc.document_id or "\\" in doc.document_id:
                raise ValueError("Unsafe document_id in source evidence")
            settings = copy.deepcopy(doc.manifest.get("configuration", {}).get("document_layout")
                or doc.manifest.get("document_layout", {}).get("settings") or {"method": "legacy"})
            settings["furniture"] = {**settings.get("furniture", {}), "method": "document_context_v3"}
            doc.manifest["furniture_relabel"] = {
                "algorithm_version": ALGORITHM_VERSION, "ocr_rerun": False, "source_result": str(path),
                "source_result_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "source_fingerprint": receipt["source_fingerprint"], "started_at_utc": receipt["started_at_utc"]}
            out = ExportDocumentUseCase.execute(doc, destination, assets_mode="none",
                                               document_layout=settings, keywords_file=str(keywords_file))
            counts, matches = count_keywords_exact((out/"result.md").read_text(encoding="utf-8"), keywords)
            export_single_document_keywords(doc.document_id, out, keywords, counts, matches)
            record.update(status="SUCCESS", counts=counts, total_matches=matches, total_pages=len(doc.pages),
                          output_directory=str(out), templates=len(doc.manifest["furniture"].get("templates", [])))
        except Exception as exc:
            record.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        record["time_seconds"] = round(time.perf_counter()-tick, 3)
        build_and_save_master_matrix(records, keywords, matrix, time.perf_counter()-started)
        atomic_write_json(destination/"relabel_run.json", receipt)
        print(f"[{record['index']}/{len(records)}] {path.parent.name}: {record['status']} ({record['time_seconds']}s)", flush=True)
    receipt["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(destination/"relabel_run.json", receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT/"output")
    parser.add_argument("--output-dir", type=Path, default=ROOT/"output_finsbd")
    parser.add_argument("--keywords-file", type=Path, default=ROOT/"config/keywords.txt")
    args = parser.parse_args()
    result = relabel(args.input_dir, args.output_dir, args.keywords_file)
    sys.exit(1 if any(r["status"] == "FAILED" for r in result["documents"]) else 0)

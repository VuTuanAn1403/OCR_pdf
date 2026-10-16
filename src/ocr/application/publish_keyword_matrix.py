"""Publish exactly one prepared revision before any subsequent document."""
from src.ocr.infrastructure.export.keyword_v63 import matrix_bytes, verify_matrix
from src.ocr.runtime.atomic_io import atomic_write_bytes
from src.ocr.runtime.gpu_scheduler import sha256
from zipfile import BadZipFile
import json
from pathlib import Path


def publish_keyword_matrix(store, path, snapshot):
    revision, records, expected_hash = store.projection()
    identity = store.get("identity")
    for row in records:
        if row.get("outcome") == "SUCCEEDED":
            report_path=Path(row["output"])/"keyword_counts.json"
            if sha256(report_path) != row["report_sha256"]:
                raise ValueError("Accepted keyword report changed before matrix publication")
            report=json.loads(report_path.read_text(encoding="utf-8"))
            if report["display_counts"] != row["counts"] or report["identity"]["dictionary_sha256"] != snapshot["snapshot_sha256"]:
                raise ValueError("Prepared ledger counts differ from accepted report")
    valid = False
    try:
        verify_matrix(path, identity["batch_id"], revision, records, snapshot)
        actual = sha256(path)
        valid = expected_hash is None or expected_hash == actual
    except (OSError, ValueError, KeyError, StopIteration, BadZipFile):
        pass
    if not valid:
        atomic_write_bytes(path, matrix_bytes(identity["batch_id"], revision, records, snapshot))
        verify_matrix(path, identity["batch_id"], revision, records, snapshot)
        actual = sha256(path)
    store.acknowledge(revision, actual)
    return revision


"""One-document owner; finalizes keyword artifacts after all GPU children exit."""
import argparse
import json
from pathlib import Path
import sys
import psutil

from src.ocr.runtime.atomic_io import atomic_write_json
from src.ocr.domain.models.processing import PublicationError, IdentityMismatch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request",type=Path,required=True)
    parser.add_argument("--response",type=Path,required=True)
    args = parser.parse_args()
    payload = json.loads(args.request.read_text(encoding="utf-8"))
    try:
        from src.ocr.runtime.v63_backend import verify_runtime
        verify_runtime(payload["request"])
        from src.ocr.application.process_document import process_document
        result = process_document(payload["request"],payload["snapshot"],resume=payload["resume"])
        atomic_write_json(args.response,{"result":result})
    except Exception as exc:
        kind = "identity" if isinstance(exc,IdentityMismatch) else "document" if isinstance(exc,TimeoutError) else "infrastructure" if (isinstance(exc,(OSError,PublicationError,psutil.AccessDenied)) or "GPU_PHASE_PROCESS_TREE_SURVIVED" in str(exc)) else "document"
        error = f"{type(exc).__name__}: {exc}"
        atomic_write_json(args.response,{"kind":kind,"error":error,"transient":isinstance(exc,TimeoutError)})
        raise


if __name__ == "__main__":
    main()

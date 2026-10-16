"""V6.3 document finalizer, invoked inside a lifetime-bounded CPU worker."""
import json
from pathlib import Path
from zipfile import BadZipFile

from src.ocr.domain.models.processing import KEYWORD_ARTIFACTS, PublicationError, IdentityMismatch
from src.ocr.infrastructure.postprocessing.keyword_contract import digest, count_report, validate_report
from src.ocr.infrastructure.export.keyword_v63 import export_document, verify_document
from src.ocr.runtime.atomic_io import atomic_write_json
from src.ocr.runtime.gpu_scheduler import sha256


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def ocr_files(output):
    excluded = {*KEYWORD_ARTIFACTS, "COMPLETED.json", "ocr_receipt.json"}
    return {p.relative_to(output).as_posix():sha256(p) for p in sorted(output.rglob('*'))
            if p.is_file() and p.name not in excluded and p.name != "manifest.json" and not p.name.endswith(".tmp")}


def manifest_ocr_hash(output):
    manifest = read(output/"manifest.json")
    manifest.pop("keyword_contract", None)
    return digest(manifest)


def valid_ocr(output, identity):
    try:
        receipt = read(output/"ocr_receipt.json")
        return (receipt["ocr_identity"] == identity and receipt["manifest_ocr_sha256"] == manifest_ocr_hash(output)
                and receipt["files"] == ocr_files(output))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def validate_gpu_source(output):
    manifest = read(output/"manifest.json")
    pages = manifest["selected_pages"]
    if pages != list(range(1, manifest["total_pages"]+1)):
        raise ValueError("PARTIAL_DOCUMENT cannot enter a full-document matrix")
    placement = manifest["gpu_runtime"]["phase_a_placement"]
    if (placement["detector"].get("expected_provider") != "CUDAExecutionProvider"
            or placement["recognizer"].get("device") != "cuda"
            or placement["recognizer"].get("workload", "").startswith("synthetic")):
        raise ValueError("Missing real document GPU placement evidence")
    for n in pages:
        page = read(output/"pages"/f"page-{n:04d}.json")
        audit = page["metadata"].get("gpu_ocr", {})
        if (audit.get("scope") != "all_pages" or audit.get("source_policy") != "gpu_ocr_only_v1"
                or audit.get("detector_device") != "cuda" or audit.get("recognizer_device") != "cuda"
                or audit.get("detector_calls", 0) < 1 or audit.get("workload") != "document"
                or audit.get("status") not in ("OCR_PROCESSED", "BLANK_NO_DETECTIONS")
                or audit.get("status") == "OCR_PROCESSED" and audit.get("recognizer_forward_calls", 0) < 1):
            raise ValueError(f"Page {n} lacks actual full-page GPU OCR evidence")
        if page["metadata"].get("ocr_structure_bridge", {}).get("policy") != "raster-plus-gpu-text-v1":
            raise ValueError(f"Page {n} lacks a source-safe Docling bridge")
        if any(b.get("source") == "native" for b in page["blocks"]):
            raise ValueError(f"Native text in counted output, page {n}")
        if any(b.get("source") == "native" for b in page["metadata"].get("text_evidence", {}).get("items", [])):
            raise ValueError(f"Native evidence in GPU-only page {n}")
        if any(s.get("provenance", {}).get("text_source_policy") != "gpu_ocr_only_v1" for s in [*page.get("tables",[]), *page.get("formulas",[])]):
            raise ValueError(f"Unverified structure text source, page {n}")


def finalize_keywords(output, snapshot, pdf_sha256):
    from src.ocr.infrastructure.export.completion import clear_completed, mark_completed
    from src.ocr.infrastructure.export.artifact_validator import validate_document_artifacts
    output = Path(output)
    clear_completed(output)
    manifest = read(output/"manifest.json")
    manifest["keyword_contract"] = {"schema_version":"keyword-contract-v1", "required_artifacts":list(KEYWORD_ARTIFACTS),
                                    "dictionary":snapshot}
    atomic_write_json(output/"manifest.json",manifest)
    text_path = output/"result.md"
    report_path = output/"keyword_counts.json"
    try:
        seal = read(output/"keyword_receipt.json")
        if seal["report_sha256"] != sha256(report_path):
            raise ValueError("Keyword report hash mismatch")
        report = read(report_path)
        validate_report(report, snapshot, text_path, output.name, pdf_sha256)
    except (OSError, ValueError, KeyError, TypeError):
        report = count_report(output.name, pdf_sha256, text_path, snapshot, manifest["selected_pages"])
        atomic_write_json(report_path,report)
        atomic_write_json(output/"keyword_receipt.json", {"report_sha256":sha256(report_path),"identity":report["identity"]})
    # XLSX failure propagates as infrastructure failure, never a blank-row PDF error.
    try:
        try:
            verify_document(output/"count_keywords.xlsx", report, snapshot)
        except (OSError, ValueError, KeyError, BadZipFile):
            export_document(report,snapshot,output)
    except OSError as exc:
        raise PublicationError(f"Per-document Excel cannot be published: {exc}") from exc
    validate_report(report,snapshot,text_path,output.name,pdf_sha256)
    atomic_write_json(output/"keyword_validation.json", {"schema_version":"keyword-validation-v1", "status":"PASS",
                                                       "report_sha256":sha256(report_path)})
    result = validate_document_artifacts(output)
    if result["status"] == "FAIL":
        raise ValueError(f"Final artifact contract failed: {result['errors'][:3]}")
    mark_completed(output,result["status"])
    return {"outcome":"SUCCEEDED","output":str(output),"report_sha256":sha256(report_path),
            "completion_sha256":sha256(output/"COMPLETED.json"),"validation":result["status"]}


def process_document(request, snapshot, *, resume=False, backend=None):
    from src.ocr.runtime.gpu_scheduler import run_document
    from src.ocr.infrastructure.export.artifact_validator import validate_document_artifacts
    output = Path(request["output"])/Path(request["pdf"]).stem
    identity = request["ocr_identity"]
    if not resume or not valid_ocr(output,identity):
        # Invalidate the old acceptance before a forced/new extraction. If
        # inference fails, a later retry must not certify the previous output
        # through its still-valid document receipt.
        from src.ocr.infrastructure.export.completion import clear_completed
        clear_completed(output)
        (output/"ocr_receipt.json").unlink(missing_ok=True)
        (backend or run_document)({**request,"finalize_document":False}, resume=resume)
        validate_gpu_source(output)
        validation = validate_document_artifacts(output, include_keywords=False)
        if validation["status"] == "FAIL":
            raise ValueError(f"OCR artifact validation failed: {validation['errors'][:3]}")
        atomic_write_json(output/"ocr_receipt.json", {"ocr_identity":identity,"manifest_ocr_sha256":manifest_ocr_hash(output),
                                                     "files":ocr_files(output)})
    validate_gpu_source(output)
    result = finalize_keywords(output,snapshot,request["pdf_sha256"])
    if not valid_ocr(output,identity):
        raise IdentityMismatch("OCR changed during keyword finalization")
    return result

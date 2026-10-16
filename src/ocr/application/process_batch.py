"""V6.3 CPU-only, sequential, revision-gated batch application."""
import json
from pathlib import Path
import unicodedata

from src.ocr.domain.models.processing import IdentityMismatch, PublicationError
from src.ocr.infrastructure.postprocessing.keyword_contract import dictionary_snapshot, digest, validate_report, validate_snapshot
from src.ocr.infrastructure.state.batch_store import BatchStore
from src.ocr.application.publish_keyword_matrix import publish_keyword_matrix
from src.ocr.runtime.atomic_io import atomic_write_json
from src.ocr.runtime.gpu_scheduler import GPULease, sha256


def input_snapshot(path):
    source = Path(path).resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    paths = [source] if source.is_file() else [p for p in source.iterdir() if p.is_file() and p.suffix.casefold()==".pdf"]
    paths.sort(key=lambda p: unicodedata.normalize("NFC", p.name).casefold())
    if not paths or any(p.suffix.casefold() != ".pdf" for p in paths):
        raise ValueError("Input must contain PDF files")
    seen, hashes, records = set(), {}, []
    for p in paths:
        key = unicodedata.normalize("NFC", p.stem).casefold()
        if key in seen:
            raise ValueError(f"Colliding normalized document ID: {p.stem}")
        seen.add(key)
        checksum = None
        try:
            checksum, error = sha256(p), None
            import pymupdf
            with pymupdf.open(p) as document:
                if document.needs_pass:
                    raise ValueError("Encrypted PDF requires authentication")
                total_pages = len(document)
        except Exception as exc:
            error, total_pages = str(exc), None
        records.append({"id":p.stem,"name":p.name,"path":str(p),"size":p.stat().st_size,
                        "pdf_sha256":checksum,"source_error":error,"total_pages":total_pages,
                        "source_review":"REVIEW_SINGLE_PAGE_SOURCE" if total_pages==1 else "NOT_EVALUATED",
                        "duplicate_of":hashes.get(checksum),"counts":None})
        if checksum:
            hashes.setdefault(checksum, p.stem)
    if len(records) > 1048574:
        raise ValueError("Input snapshot exceeds XLSX row capacity")
    return records


def process_batch(input_path, output, config, keywords_path, processor, *, resume=False,
                  retry_failed=False, force_ocr=False, identity_check=lambda: None, max_attempts=2,
                  force_recount=False, frozen_dictionary=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with GPULease(output/"batch.lock", timeout=0):
        snapshot = frozen_dictionary or dictionary_snapshot(keywords_path, config.get("keyword_set_version", "v1"))
        validate_snapshot(snapshot)
        inputs_path = output/"batch_input_manifest.json"
        if inputs_path.exists():
            if not resume:
                raise ValueError("Batch already exists; use --resume or another output")
            saved_inputs = json.loads(inputs_path.read_text(encoding="utf-8"))
            records = saved_inputs["documents"]
        else:
            records = input_snapshot(input_path)
            saved_inputs = {"documents":records,"excluded_subdirectory_pdfs":
                [str(p) for p in Path(input_path).rglob('*') if p.is_file() and p.suffix.casefold()=='.pdf' and p.parent!=Path(input_path)]
                if Path(input_path).is_dir() else []}
        identity = {"input":str(Path(input_path).resolve()),"config":config,"dictionary":snapshot["snapshot_sha256"],
                    "inputs": [{k:r[k] for k in ("id","path","pdf_sha256","size")} for r in records]}
        identity["batch_id"] = digest(identity)
        store = BatchStore(output/"batch_state.sqlite", identity, records)
        try:
            for name, payload in (("batch_input_manifest.json", saved_inputs),
                    ("batch_config.json",config),("keyword_dictionary.snapshot.json",snapshot)):
                path = output/name
                if not path.exists():
                    atomic_write_json(path, payload)
                elif json.loads(path.read_text(encoding="utf-8")) != payload:
                    raise IdentityMismatch(f"Snapshot changed: {name}")
            matrix = output/f"MA_TRAN_DEM_TU_KHOA_{len(records)}_FILES.xlsx"
            identity_check()
            from src.ocr.infrastructure.export.completion import verify_completed
            def accept(record,result):
                # Verify again after inference: a file/model/code edit during
                # the document must never enter the accepted matrix revision.
                identity_check()
                if record["pdf_sha256"] is not None and sha256(record["path"]) != record["pdf_sha256"]:
                    raise IdentityMismatch(f"INPUT_CHANGED: {record['path']}")
                updated={**record,**result,"attempt":record.get("attempt",0)+result.get("attempts",1)}
                if result.get("outcome")=="SUCCEEDED":
                    updated.pop("error", None)
                    directory=Path(result["output"])
                    if directory.resolve() != (output/record["id"]).resolve():
                        raise IdentityMismatch("Worker output directory differs from snapshot")
                    path=directory/"keyword_counts.json"
                    if (sha256(path)!=result["report_sha256"] or not verify_completed(directory)
                            or sha256(directory/"COMPLETED.json")!=result["completion_sha256"]):
                        raise IdentityMismatch("Worker receipt changed before ledger preparation")
                    report=json.loads(path.read_text(encoding="utf-8"))
                    validate_report(report,snapshot,directory/"result.md",record["id"],record["pdf_sha256"])
                    updated.update(counts=report["display_counts"],total_canonical_matches=report["total_canonical_matches"])
                else:
                    # A failed replacement must not carry forward accepted
                    # counts, hashes or validation from an earlier revision.
                    updated.update(counts=None,total_canonical_matches=0,validation=None)
                    for key in ("report_sha256", "completion_sha256"):
                        updated.pop(key, None)
                return updated
            for prior in store.records():
                if prior["pdf_sha256"] is not None and sha256(prior["path"]) != prior["pdf_sha256"]:
                    raise IdentityMismatch(f"INPUT_CHANGED: {prior['path']}")
                if prior.get("outcome")=="SUCCEEDED" and not verify_completed(Path(prior["output"])):
                    repaired=processor(prior,snapshot,output,resume=True,force_ocr=False,max_attempts=max_attempts)
                    store.prepare(accept(prior,repaired),allow_unpublished=True)
            # Reconcile replacement/ack crash windows and missing/tampered XLSX.
            publish_keyword_matrix(store, matrix, snapshot)
            for record in store.records():
                identity_check()
                if not record["source_error"] and sha256(record["path"]) != record["pdf_sha256"]:
                    raise IdentityMismatch(f"INPUT_CHANGED: {record['path']}")
                if record.get("outcome") == "FAILED_DOCUMENT" and not retry_failed:
                    continue
                if record.get("outcome") == "SUCCEEDED" and resume and not force_ocr and not force_recount:
                    prior = Path(record["output"])
                    if (verify_completed(prior) and (prior/"keyword_counts.json").is_file()
                            and sha256(prior/"keyword_counts.json") == record["report_sha256"]):
                        continue
                store.event(record["id"], "OCR_RUNNING")
                if force_recount and record.get("outcome")=="SUCCEEDED":
                    (Path(record["output"])/"keyword_receipt.json").unlink(missing_ok=True)
                try:
                    if record["source_error"]:
                        raise ValueError(record["source_error"])
                    result = processor(record, snapshot, output, resume=resume and not force_ocr,
                                       force_ocr=force_ocr, max_attempts=max_attempts)
                except (OSError, PublicationError, IdentityMismatch):
                    store.event(record["id"], "PUBLISH_BLOCKED")
                    raise
                except Exception as exc:
                    result = {"outcome":"FAILED_DOCUMENT","error":f"{type(exc).__name__}: {exc}","counts":None}
                updated = accept(record,result)
                if updated != record or not record.get("outcome"):
                    store.prepare(updated)
                    publish_keyword_matrix(store, matrix, snapshot)
                store.event(record["id"], "DOCUMENT_COMMITTED")
                print(f"[{len([r for r in store.records() if r.get('outcome')])}/{len(records)}] {record['name']}: {updated['outcome']}", flush=True)
            report = {"batch_id":identity["batch_id"],"published_revision":store.get("published_revision"),
                      "documents":store.records(),"matrix":str(matrix),"quality_acceptance":"NOT_EVALUATED"}
            atomic_write_json(output/"batch_report.json",report)
            return report
        finally:
            try:
                store.export_events(output/"logs/batch.jsonl")
            finally:
                store.close()

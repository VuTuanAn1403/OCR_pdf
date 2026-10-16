"""Prepare locked GPU requests without loading Torch into the batch process."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

from src.ocr.runtime.config import load_profiles, get_profile
from src.ocr.runtime.gpu_scheduler import ROOT, sha256
from src.ocr.runtime.fingerprint import source_fingerprint
from src.ocr.runtime.process_tree import supervised_run
from src.ocr.runtime.atomic_io import atomic_write_json
from src.ocr.infrastructure.postprocessing.keyword_contract import digest
from src.ocr.domain.models.processing import PublicationError, DocumentProcessingError, IdentityMismatch


def prepare_backend(config_path, profile, env_root, *, preflight=True):
    path, profiles = load_profiles(config_path)
    config = copy.deepcopy(get_profile(profiles, profile))
    required = {"device":"cuda","native_first":False,"ocr_scope":"all_pages","ocr_source_policy":"gpu_ocr_only_v1",
                "bridge_policy":"raster-plus-gpu-text-v1"}
    if any(config.get(k) != v for k,v in required.items()) or config.get("runtime",{}).get("scheduler") != "document_two_phase_v1":
        raise ValueError("V6.3 requires CUDA, native_first=false, all_pages and gpu_ocr_only_v1")
    if (not config.get("docling",{}).get("strict_gpu") or not config["docling"].get("verify_placement")
            or not config["docling"].get("enabled") or config["docling"].get("ocr")
            or config.get("two_up_detection_enabled") or config.get("multi_region_detection_enabled")
            or config.get("orientation_fallback_enabled")):
        raise ValueError("V6.3 requires verified Docling GPU and original full-page OCR geometry")
    registry_path = (ROOT/config["model_registry"]).resolve()
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    models = {str((ROOT/name).resolve()):checksum for name,checksum in registry["files"].items()}
    for name, checksum in models.items():
        if sha256(name) != checksum:
            raise IdentityMismatch(f"MODEL_LOCK_MISMATCH: {name}")
    models[str(registry_path)] = sha256(registry_path)
    for name in ("text_detector","layout_model"):
        config[name] = dict(registry[name])
        config[name]["model_path"] = str((ROOT/config[name]["model_path"]).resolve())
    config["docling"]["device"] = "cuda"
    config["backend_devices"] = dict.fromkeys(("ocr_detection","ocr_recognition","layout","docling"),"cuda")
    suffix = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    interpreters = {f"{role}_python":str((Path(env_root)/role/suffix).resolve()) for role in ("text","docling")}
    for python in interpreters.values():
        if not Path(python).is_file():
            raise FileNotFoundError(f"Missing locked GPU environment: {python}")
    platform = "windows" if os.name == "nt" else "linux"
    locks = {str(ROOT/"requirements/v62"/f"{platform}-{role}-gpu.lock"):None for role in ("text","docling")}
    locks = {p:sha256(p) for p in locks}
    config["docling"]["dependency_lock"] = next(p for p in locks if p.endswith("docling-gpu.lock"))
    if preflight:
        from scripts.preflight_v62 import inspect_environment
        from src.ocr.runtime.gpu_scheduler import gpu_snapshot, lease_for_gpu
        with lease_for_gpu(gpu_snapshot("cuda")):
            for role in ("text","docling"):
                probe = inspect_environment(interpreters[f"{role}_python"],"cuda",require_ort=role=="text")
                if probe["status"] == "FAILED":
                    raise RuntimeError(f"GPU preflight failed for {role}: {probe['errors']}")
    return {"config":config,"config_path":str(path),"device":"cuda", **interpreters,
            "model_hashes":models,"lock_hashes":locks,"source_fingerprint":source_fingerprint(),
            "phase_timeout":config["runtime"]["phase_timeout_seconds"],"lease_timeout":config["runtime"]["lease_timeout_seconds"]}


def verify_runtime(backend):
    if source_fingerprint() != backend["source_fingerprint"]:
        raise IdentityMismatch("Source changed after batch snapshot")
    for path, checksum in {**backend["model_hashes"],**backend["lock_hashes"]}.items():
        if sha256(path) != checksum:
            raise IdentityMismatch(f"Runtime input changed: {path}")


class GPUProcessor:
    def __init__(self, backend, *, assets="png", output_mode="compact"):
        self.backend, self.assets, self.output_mode = backend, assets, output_mode

    def __call__(self, item, snapshot, output, *, resume, force_ocr, max_attempts=2):
        request = {**self.backend,"pdf":item["path"],"pdf_sha256":item["pdf_sha256"],"output":str(output),
                   "assets":self.assets,"output_mode":self.output_mode,"finalize_document":False}
        request["ocr_identity"] = digest({k:request[k] for k in ("pdf_sha256","config","model_hashes","lock_hashes",
                                                               "source_fingerprint","assets","output_mode","text_python","docling_python")})
        log_dir = Path(output)/"logs"
        request_path = log_dir/f"{item['id']}.request.json"
        response_path = log_dir/f"{item['id']}.response.json"
        atomic_write_json(request_path,{"request":request,"snapshot":snapshot,"resume":resume and not force_ocr})
        for attempt in range(max_attempts):
            response_path.unlink(missing_ok=True)
            try:
                supervised_run([sys.executable,"-m","src.ocr.runtime.v63_document_worker","--request",str(request_path),
                    "--response",str(response_path)],cwd=ROOT,env={**os.environ,"PYTHONIOENCODING":"utf-8"},
                    log_path=log_dir/f"{item['id']}.log",timeout=request["phase_timeout"]*3)
                return {**json.loads(response_path.read_text(encoding="utf-8"))["result"],"attempts":attempt+1}
            except RuntimeError as exc:
                if not response_path.is_file():
                    raise PublicationError(f"Document worker/tree did not yield a verifiable receipt: {exc}") from exc
                failure = json.loads(response_path.read_text(encoding="utf-8"))
                if failure["kind"] == "infrastructure":
                    raise PublicationError(failure["error"])
                if failure["kind"] == "identity":
                    raise IdentityMismatch(failure["error"])
                if not failure.get("transient") or attempt+1 >= max_attempts:
                    raise DocumentProcessingError(failure["error"])


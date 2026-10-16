"""Document-wide A -> process exit -> B, with a CPU-only coordinating process.

This module deliberately imports neither Torch nor pipeline/model modules.
An OS-backed lease spans both phases and the final export, even on 4 GB GPUs.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from src.ocr.runtime.atomic_io import atomic_write_json
from src.ocr.runtime.process_tree import supervised_run

ROOT = Path(__file__).resolve().parents[3]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class GPULease:
    """Kernel releases the lock after supervisor death; no stale-PID guessing."""
    def __init__(self, path, timeout=60):
        self.path, self.timeout, self.stream = Path(path), timeout, None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        self.stream.seek(0, 2)
        if self.stream.tell() == 0:
            self.stream.write(b"0")
            self.stream.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self.stream.close()
                    raise TimeoutError(f"GPU_LEASE_BUSY: {self.path}")
                time.sleep(.1)

    def __exit__(self, *_):
        self.stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream, fcntl.LOCK_UN)
        self.stream.close()


def gpu_snapshot(device):
    if device == "cpu":
        return {"device": "cpu"}
    cmd = ["nvidia-smi", "--query-gpu=uuid,memory.total,memory.used", "--format=csv,noheader,nounits", "--id=0"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError("nvidia-smi VRAM audit unavailable: " + result.stderr[-500:])
    uuid, total, used = result.stdout.strip().splitlines()[0].split(",")
    return {"device": uuid.strip(), "total_mib": int(total), "used_mib": int(used)}


def validate_policy(device, allow_cpu_benchmark):
    if device not in ("cuda", "cpu"):
        raise ValueError("Two-phase device must be explicit cuda or cpu")
    if (device == "cpu") != bool(allow_cpu_benchmark):
        raise ValueError("CPU requires --device cpu --allow-cpu-benchmark; the flag is invalid with CUDA")


def lease_for_gpu(snapshot, timeout=60):
    directory = Path(os.getenv("OCR_GPU_LEASE_DIR", str(ROOT / ".cache/gpu-leases")))
    name = snapshot["device"].replace("/", "_").replace(":", "_") + ".lock"
    return GPULease(directory / name, timeout=timeout)


def run_document(request, *, resume=False):
    validate_policy(request["device"], request.get("allow_cpu_benchmark", False))
    if "torch" in sys.modules:
        raise RuntimeError("GPU supervisor must start in a fresh interpreter without importing Torch")
    from src.ocr.runtime.fingerprint import source_fingerprint
    snapshot = gpu_snapshot(request["device"])
    identity = {"pdf": sha256(request["pdf"]), "source": source_fingerprint(),
                "config": request["config"], "pages": request.get("pages"),
                "device": request["device"], "python": str(Path(request["text_python"]).absolute()),
                "models": request["model_hashes"], "locks": request["lock_hashes"],
                "protocol": "document-two-phase-v1"}
    generation = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    root = Path(request.get("work_dir", ROOT / ".cache/v62-jobs")) / generation
    root.mkdir(parents=True, exist_ok=True)
    request = {**request, "generation": generation, "identity": identity, "job_dir": str(root)}
    request_path = root / "request.json"
    # The request contains absolute runtime paths, never embedded shell commands.
    audit = {"schema_version": "document-two-phase-v1", "generation": generation,
             "status": "RUNNING", "parent_pid": os.getpid(), "parent_imported_torch": False,
             "device": request["device"], "initial_vram": snapshot, "events": [], "phases": {}}
    audit_path = root / "scheduler.json"
    acquired = False
    try:
        with lease_for_gpu(snapshot, timeout=request.get("lease_timeout", 60)):
            acquired = True
            atomic_write_json(request_path, request)
            for phase in ("text", "structure", "export"):
                receipt = root / f"{phase}.done.json"
                if resume and phase != "export" and committed_receipt(root, phase, generation):
                    audit["phases"][phase] = {"resumed": True, "generation": generation}
                    continue
                if phase != "text" and not valid_receipt(root, "text" if phase == "structure" else "structure", generation):
                    raise RuntimeError("Phase prerequisite checkpoint is missing or changed")
                env = os.environ.copy()
                env.update(PYTHONIOENCODING="utf-8", PYTHONPATH=str(ROOT),
                           OCR_DOCLING_PYTHON=request["docling_python"],
                           CUDA_VISIBLE_DEVICES="" if request["device"] == "cpu" or phase == "export" else "0",
                           TOKENIZERS_PARALLELISM="false")
                env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
                audit["events"].append({"phase": phase, "event": "START", "time_ns": time.time_ns(),
                                        "vram": gpu_snapshot(request["device"])})
                atomic_write_json(audit_path, audit)
                samples = []
                last_sample = [0.]
                def observe(pid, pids):
                    if time.monotonic() - last_sample[0] >= 2:
                        samples.append({"pid": pid, "tree": pids, **gpu_snapshot(request["device"])})
                        last_sample[0] = time.monotonic()
                command = [request["text_python"], "-m", "src.ocr.runtime.gpu_phase_worker",
                           "--request", str(request_path), "--phase", phase]
                (root / f"{phase}.exited.json").unlink(missing_ok=True)
                result = supervised_run(command, cwd=ROOT, env=env, log_path=root / f"{phase}.log",
                                        timeout=request.get("phase_timeout", 14400), observe=observe)
                if not valid_receipt(root, phase, generation):
                    raise RuntimeError(f"Invalid or incomplete {phase} checkpoint")
                atomic_write_json(root / f"{phase}.exited.json", {"receipt_sha256": sha256(receipt), "tree_exited": True})
                audit["phases"][phase] = {**result, "samples": samples}
                audit["events"].append({"phase": phase, "event": "TREE_EXITED", "time_ns": time.time_ns(),
                                        "vram": gpu_snapshot(request["device"])})
                atomic_write_json(audit_path, audit)
            audit["status"] = "COMPLETED"
    except BaseException as exc:
        audit.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if acquired:
            atomic_write_json(audit_path, audit)
    output = Path(request["output"]) / Path(request["pdf"]).stem
    atomic_write_json(output / "gpu_scheduler.json", audit)
    from src.ocr.infrastructure.export.completion import mark_completed
    if request.get("finalize_document", True):
        mark_completed(output, json.loads((output / "validation.json").read_text(encoding="utf-8"))["status"])
    return {"output": str(output), "audit": str(audit_path), "generation": generation}


def committed_receipt(root, phase, generation):
    try:
        ack = json.loads((Path(root) / f"{phase}.exited.json").read_text(encoding="utf-8"))
        return (ack["tree_exited"] is True and valid_receipt(root, phase, generation)
                and ack["receipt_sha256"] == sha256(Path(root) / f"{phase}.done.json"))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def valid_receipt(root, phase, generation):
    try:
        data = json.loads((Path(root) / f"{phase}.done.json").read_text(encoding="utf-8"))
        predecessor = {"structure": "text", "export": "structure"}.get(phase)
        if predecessor and data.get("input_receipt_sha256") != sha256(Path(root) / f"{predecessor}.done.json"):
            return False
        return (data["generation"] == generation and data["phase"] == phase
                and bool(data["files"]) and all(
                    (Path(root) / name).is_file() and sha256(Path(root) / name) == digest
                    for name, digest in data["files"].items()))
    except (OSError, ValueError, KeyError, TypeError):
        return False

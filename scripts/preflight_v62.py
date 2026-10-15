"""Inspect V6.2 readiness without creating a CUDA context in the parent.

This is a readiness report, not a production/GPU acceptance certificate. Model
inference and placement remain NOT_EVALUATED until selected checkpoints exist.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from contextlib import nullcontext

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ocr.runtime.atomic_io import atomic_write_json
from src.ocr.runtime.backend_probe import validate_ort_environment
from src.ocr.runtime.gpu_scheduler import gpu_snapshot, lease_for_gpu

CHILD_PROBE = r'''
import json, sys
from importlib import metadata
device = sys.argv[1]
require_ort = sys.argv[2] == "true"
report = {"device": device, "packages": {}, "errors": []}
for name in ("onnxruntime", "onnxruntime-gpu", "rapidocr-onnxruntime", "torch", "docling-slim"):
    try: report["packages"][name] = metadata.version(name)
    except metadata.PackageNotFoundError: pass
if require_ort:
    try:
        import onnxruntime as ort
        report["available_providers"] = ort.get_available_providers()
    except Exception as exc: report["errors"].append(str(exc))
try:
    import torch
    if device == "cuda":
        if not torch.cuda.is_available(): raise RuntimeError("Torch CUDA unavailable")
        x = torch.ones((16, 16), device="cuda")
        y = x @ x
        torch.cuda.synchronize()
        report["torch_actual_device"] = str(y.device)
        report["gpu_total_gib"] = torch.cuda.get_device_properties(0).total_memory / 1024**3
    else:
        x = torch.ones((16, 16), device="cpu")
        y = x @ x
        report["torch_actual_device"] = str(y.device)
    report["torch_smoke_value"] = float(y[0, 0])
except Exception as exc: report["errors"].append(str(exc))
print(json.dumps(report))
'''


def resolve_benchmark_policy(device, allow_cpu_benchmark):
    if device not in ("cpu", "cuda"):
        raise ValueError("Device must be explicit cpu or cuda")
    if device == "cpu" and not allow_cpu_benchmark:
        raise ValueError("CPU V6.2 requires --device cpu --allow-cpu-benchmark")
    if allow_cpu_benchmark and device != "cpu":
        raise ValueError("--allow-cpu-benchmark is valid only with explicit --device cpu")
    return "cpu_acceptance_benchmark" if device == "cpu" else "gpu_production_readiness"


def inspect_environment(python, device, *, require_ort=True):
    # This process exits before another environment is probed. No Torch import
    # or torch.cuda call is made by the orchestrating interpreter.
    result = subprocess.run([str(python), "-c", CHILD_PROBE, device, str(require_ort).lower()], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=90,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        return {"python": str(python), "status": "FAILED", "errors": [result.stderr[-2000:]]}
    try:
        report = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        return {"python": str(python), "status": "FAILED", "errors": [f"Invalid probe response: {exc}"]}
    try:
        if require_ort or any(name in report["packages"] for name in ("onnxruntime", "onnxruntime-gpu", "rapidocr-onnxruntime")):
            report["ort_environment"] = validate_ort_environment(device, report["packages"])
    except RuntimeError as exc:
        report["errors"].append(str(exc))
    required = "CUDAExecutionProvider" if device == "cuda" else "CPUExecutionProvider"
    if require_ort and required not in report.get("available_providers", []):
        report["errors"].append(f"{required} unavailable")
    report.update(python=str(python), status="FAILED" if report["errors"] else "CAPABILITY_ONLY",
                  model_inference="NOT_EVALUATED", operator_placement="NOT_EVALUATED")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True, choices=("cpu", "cuda"))
    parser.add_argument("--allow-cpu-benchmark", action="store_true")
    parser.add_argument("--env-root", type=Path, help="Probe the isolated V6.2 text/docling environments")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        purpose = resolve_benchmark_policy(args.device, args.allow_cpu_benchmark)
    except ValueError as exc:
        parser.error(str(exc))
    suffix = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    text_python = args.env_root / "text" / suffix if args.env_root else sys.executable
    default_worker = ROOT / ".venv-docling" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    worker = args.env_root / "docling" / suffix if args.env_root else Path(os.getenv("OCR_DOCLING_PYTHON", str(default_worker)))
    lease = lease_for_gpu(gpu_snapshot("cuda")) if args.env_root and args.device == "cuda" else nullcontext()
    with lease:
        reports = {"main": inspect_environment(text_python, args.device)}
        if worker.is_file():
            reports["docling"] = inspect_environment(worker, args.device, require_ort=False)
        else:
            reports["docling"] = {"status": "FAILED", "errors": [f"Missing interpreter: {worker}"]}
    capability_ready = bool(args.env_root) and all(r.get("status") == "CAPABILITY_ONLY" for r in reports.values())
    status = "CAPABILITY_READY" if capability_ready else "NOT_READY"
    report = {"schema_version": "v62-readiness-v1", "run_purpose": purpose,
              "status": status, "parent_cuda_initialized": False,
              "environments": reports,
              "release_gates": {"selected_layout_model": "NOT_EVALUATED",
                                "selected_text_detector": "NOT_EVALUATED",
                                "full_pipeline_gpu_placement": "NOT_EVALUATED",
                                "small_gpu_document_two_phase": "NOT_EVALUATED",
                                "gt_header_footer_reading_order": "NOT_EVALUATED",
                                "cpu_req_01_03": "NOT_EVALUATED"}}
    atomic_write_json(args.output, report)
    print(f"V6.2 readiness: {status}; model/soak/quality gates remain separate; report: {args.output.resolve()}")
    return 0 if capability_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())

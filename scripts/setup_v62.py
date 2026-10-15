"""Create isolated V6.2 CUDA environments from platform/hash-locked packages.

Never modifies .venv or .venv-docling. A repeated invocation verifies the
existing installation; --sync explicitly repairs it from the same locks.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.ocr.runtime.atomic_io import atomic_write_json
from src.ocr.runtime.gpu_scheduler import sha256, gpu_snapshot, lease_for_gpu


def run(command):
    subprocess.run(command, cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default="3.11")
    parser.add_argument("--env-root", type=Path, default=ROOT / ".venv-v62")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--http1-downloads", action="store_true", help="Hash-verified local wheelhouse for slow proxies")
    args = parser.parse_args()
    platform = "windows" if sys.platform == "win32" else "linux"
    env_root = args.env_root.resolve()
    if env_root in {ROOT, ROOT / ".venv", ROOT / ".venv-docling"}:
        parser.error("Use a separate V6.2 environment root")
    report = {"schema_version": "v62-clean-install-v1", "platform": platform,
              "python_target": "3.11", "environments": {}, "status": "RUNNING"}
    report_path = env_root / "installation.json"
    try:
        for role in ("text", "docling"):
            lock = ROOT / "requirements/v62" / f"{platform}-{role}-gpu.lock"
            if not lock.is_file():
                raise RuntimeError(f"Missing platform lock: {lock}")
            folder = env_root / role
            python = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            clean = not folder.exists()
            if not clean and not python.is_file():
                raise RuntimeError(f"Refusing to reuse a non-environment directory: {folder}")
            if clean:
                run(["uv", "venv", "--python", args.python, str(folder)])
            initial = subprocess.run([str(python), "-c", "import sys; from importlib.metadata import distributions; print(sum(str(d.locate_file('')).lower().startswith(sys.prefix.lower()) for d in distributions()))"],
                                     capture_output=True, text=True, check=True)
            if clean or args.sync:
                install_lock = lock
                if args.http1_downloads:
                    from v62_wheelhouse import local_lock
                    install_lock = local_lock(lock, ROOT / ".cache/v62-wheelhouse" / platform)
                run(["uv", "pip", "sync", "--python", str(python), "--require-hashes",
                     "--torch-backend", "cu126", str(install_lock),
                     *(["--offline"] if args.http1_downloads else [])])
            run(["uv", "pip", "check", "--python", str(python)])
            code = """
import sys,json
from importlib.metadata import distributions
p={d.metadata['Name'].lower().replace('_','-'):d.version for d in distributions() if str(d.locate_file('')).lower().startswith(sys.prefix.lower())}
if sys.version_info[:2] != (3,11): raise RuntimeError('V6.2 locks require Python 3.11')
if any(n in p for n in ('onnxruntime','rapidocr-onnxruntime')): raise RuntimeError('Mixed/legacy runtime')
import torch,torchvision
import cv2,numpy
if not torch.cuda.is_available(): raise RuntimeError('Torch CUDA unavailable')
x=torch.ones((16,16),device='cuda'); y=x@x; torch.cuda.synchronize()
print(json.dumps({'python':sys.version,'packages':p,'torch_device':str(y.device),'value':float(y[0,0])}))
"""
            with lease_for_gpu(gpu_snapshot("cuda")):
                result = subprocess.run([str(python), "-c", code], check=True, cwd=ROOT,
                                        capture_output=True, text=True, encoding="utf-8")
            report["environments"][role] = {"clean_created": clean, "initial_package_count": int(initial.stdout.strip()),
                                              "lock_sha256": sha256(lock),
                                              "python": str(python), "probe": json.loads(result.stdout.splitlines()[-1])}
            atomic_write_json(report_path, report)
        report["status"] = "INSTALLED_AND_CUDA_TORCH_VERIFIED"
    except BaseException as exc:
        report.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        atomic_write_json(report_path, report)
    print(f"V6.2 installation: {report_path}")


if __name__ == "__main__":
    main()

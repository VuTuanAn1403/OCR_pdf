"""Linux venv executables must keep their lexical path when launched/cached."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import venv

import pytest

ROOT = Path(__file__).resolve().parents[2]
SUFFIX = "Scripts/python.exe" if os.name == "nt" else "bin/python"


@pytest.fixture
def worker_paths(tmp_path, monkeypatch):
    """Model symlink resolution on every OS, including Windows without symlink privileges."""
    base = tmp_path / "base-python"
    base.write_text("base interpreter", encoding="utf-8")
    env_root = tmp_path / "envs"
    workers = {role: env_root / role / SUFFIX for role in ("text", "docling")}
    for path in workers.values():
        path.parent.mkdir(parents=True)
        path.write_text("venv interpreter", encoding="utf-8")
    original = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **kw:
                        base if self in workers.values() else original(self, *a, **kw))
    return env_root, workers, base


def registry_fixture(root):
    font = root / "assets/fonts/NotoSans-Regular.ttf"
    font.parent.mkdir(parents=True)
    font.write_bytes(b"fixture font")
    registry = root / "models/v62/runtime-models.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"files": {}, "text_detector": {"model_path": "models/detector.onnx"},
                                    "layout_model": {"model_path": "models/layout"}}), encoding="utf-8")
    locks = root / "requirements/v62"
    locks.mkdir(parents=True)
    platform = "windows" if os.name == "nt" else "linux"
    for role in ("text", "docling"):
        (locks / f"{platform}-{role}-gpu.lock").write_text("fixture", encoding="utf-8")


@pytest.mark.parametrize("preflight", [False, True])
def test_v63_preflight_and_requests_keep_worker_venv_paths(tmp_path, monkeypatch, worker_paths, preflight):
    from src.ocr.runtime import v63_backend, gpu_scheduler
    import scripts.preflight_v62 as probes
    env_root, workers, base = worker_paths
    registry_fixture(tmp_path)
    monkeypatch.setattr(v63_backend, "ROOT", tmp_path)
    monkeypatch.setattr(gpu_scheduler, "gpu_snapshot", lambda _: {"device": "fixture"})
    monkeypatch.setattr(gpu_scheduler, "lease_for_gpu", lambda _: nullcontext())
    observed = []

    def inspect(python, *args, **kwargs):
        observed.append(python)
        assert Path(python) != base
        return {"status": "CAPABILITY_ONLY"}

    monkeypatch.setattr(probes, "inspect_environment", inspect)
    font_probes = []
    monkeypatch.setattr(v63_backend.subprocess, "run", lambda command, **kw:
        font_probes.append(command) or SimpleNamespace(returncode=0))
    backend = v63_backend.prepare_backend(ROOT / "config/profiles-v63.yaml", "unified_gpu_full_ocr", env_root, preflight=preflight)
    for role, path in workers.items():
        assert backend[f"{role}_python"] == str(path.absolute())
    assert observed == ([str(workers[r].absolute()) for r in ("text", "docling")] if preflight else [])
    assert len(font_probes) == int(preflight)
    assert not font_probes or font_probes[0][0] == str(workers["text"].absolute())
    assert backend["config"]["bridge_font"] in backend["model_hashes"]


def test_v62_launcher_keeps_worker_venv_paths(tmp_path, monkeypatch, worker_paths):
    from src.ocr.cli import gpu_runner
    env_root, workers, base = worker_paths
    registry_fixture(tmp_path)
    monkeypatch.setattr(gpu_runner, "ROOT", tmp_path)
    pdf = tmp_path / "input.pdf"
    pdf.touch()
    monkeypatch.setattr(sys, "argv", ["gpu_runner", str(pdf), "--env-root", str(env_root),
        "--config", str(ROOT / "config/profiles-v62.yaml"), "--keywords-file", str(ROOT / "config/keywords.txt")])
    requests = []
    monkeypatch.setattr(gpu_runner, "run_document", lambda request, **kwargs: requests.append(request) or {})
    gpu_runner.main()
    assert requests[0]["text_python"] == str(workers["text"].absolute())
    assert requests[0]["docling_python"] == str(workers["docling"].absolute())


def test_docling_cache_distinguishes_envs_with_shared_base_interpreter(monkeypatch, worker_paths):
    from src.ocr.infrastructure.layout.docling_structure import DoclingStructureExtractor
    _, workers, _ = worker_paths
    keys = []
    for path in workers.values():
        monkeypatch.setenv("OCR_DOCLING_PYTHON", str(path))
        keys.append(DoclingStructureExtractor({"enabled": False}).cache_fingerprint())
    assert keys[0] != keys[1]


def test_scheduler_identity_keeps_worker_env_path(tmp_path, monkeypatch, worker_paths):
    from src.ocr.runtime import gpu_scheduler
    _, workers, _ = worker_paths
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"fixture")
    # The scheduler runs in a clean supervisor, unlike this shared pytest process.
    monkeypatch.setattr(gpu_scheduler, "sys", SimpleNamespace(modules={}))
    monkeypatch.setenv("OCR_GPU_LEASE_DIR", str(tmp_path / "leases"))
    monkeypatch.setattr(gpu_scheduler, "supervised_run", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("captured request")))
    request = {"pdf": str(pdf), "config": {}, "device": "cpu", "allow_cpu_benchmark": True,
        "text_python": str(workers["text"]), "docling_python": str(workers["docling"]),
        "model_hashes": {}, "lock_hashes": {}, "output": str(tmp_path / "out"), "work_dir": str(tmp_path / "jobs")}
    with pytest.raises(RuntimeError, match="captured request"):
        gpu_scheduler.run_document(request)
    saved = json.loads(next((tmp_path / "jobs").glob("*/request.json")).read_text(encoding="utf-8"))
    assert saved["identity"]["python"] == str(workers["text"].absolute())


@pytest.mark.skipif(os.name == "nt", reason="Real symlinked Python venv regression runs on Linux CI")
def test_real_linux_venv_package_is_lost_only_when_python_symlink_is_resolved(tmp_path):
    environment = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    python = environment / "bin/python"
    assert python.is_symlink() and python.absolute() != python.resolve()
    child_env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    site = subprocess.check_output([str(python), "-I", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"],
                                   text=True, env=child_env).strip()
    Path(site, "worker_venv_only.py").write_text("VALUE = 'worker packages'\n", encoding="utf-8")
    code = "import worker_venv_only; print(worker_venv_only.VALUE)"
    proper = subprocess.run([str(python.absolute()), "-I", "-c", code], capture_output=True, text=True, env=child_env)
    broken = subprocess.run([str(python.resolve()), "-I", "-c", code], capture_output=True, text=True, env=child_env)
    assert proper.returncode == 0 and "worker packages" in proper.stdout
    assert broken.returncode != 0 and "No module named 'worker_venv_only'" in broken.stderr


def test_repair_script_is_idempotent_and_refuses_existing_batch_ledger(tmp_path):
    from scripts.repair_kaggle_v63 import REPLACEMENTS, repair_checkout
    checkout, output = tmp_path / "repo", tmp_path / "output"
    for name, (old, new) in REPLACEMENTS.items():
        path = checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(old, encoding="utf-8")
    output.mkdir()
    ledger = output / "batch_state.sqlite"
    ledger.touch()
    with pytest.raises(ValueError, match="ledger"):
        repair_checkout(checkout, output)
    assert all((checkout / name).read_text(encoding="utf-8") == pair[0] for name, pair in REPLACEMENTS.items())
    ledger.unlink()
    assert len(repair_checkout(checkout, output)) == 4
    assert repair_checkout(checkout, output) == []

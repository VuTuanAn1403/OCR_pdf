"""Exercise Kaggle orchestration without downloads, CUDA, or a Kaggle account."""
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import types
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = ROOT / "kaggle/ocr_pdf_v63_kaggle.ipynb"


def notebook_cells():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    return notebook, {cell["id"]: "".join(cell["source"]) for cell in notebook["cells"]
                      if cell["cell_type"] == "code"}


def namespace():
    _, cells = notebook_cells()
    scope = {}
    for name in ("parameters", "helpers"):
        exec(compile(cells[name], str(NOTEBOOK) + ":" + name, "exec"), scope)
    return scope, cells


def functions_only(source, scope):
    tree = ast.parse(source)
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    exec(compile(tree, str(NOTEBOOK), "exec"), scope)


def program(name, source):
    return next(ast.literal_eval(node.value) for node in ast.parse(source).body
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets))


def test_clean_notebook_defaults_and_embedded_programs():
    notebook, cells = notebook_cells()
    assert notebook["nbformat"] == 4
    assert notebook["metadata"]["kaggle"]["isInternetEnabled"] is True
    assert notebook["metadata"]["kaggle"]["accelerator"] == "gpu"
    assert len(cells) == 9
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            ast.parse("".join(cell["source"]))
            assert cell["execution_count"] is None and cell["outputs"] == []
    for name, identity in (("DETECTOR_DOWNLOAD_PROGRAM", "models"),
                           ("DOCLING_DOWNLOAD_PROGRAM", "models"), ("VERIFY_PROGRAM", "verify")):
        ast.parse(program(name, cells[identity]))
    scope, _ = namespace()
    assert scope["FILE_LIMIT"] is None and scope["PDF_FILES"] == []
    assert scope["RUN_FULL_BATCH"] is True and scope["RESUME"] is True
    assert "pip install" not in cells["parameters"]


def test_model_config_checkout_bytes_match_registry_with_linux_git_settings():
    registry = json.loads((ROOT / "models/v62/runtime-models.json").read_text(encoding="utf-8"))
    for name in ("models/vietocr/base.yml", "models/vietocr/vgg-seq2seq.yml"):
        data = subprocess.check_output(["git", "-c", "core.autocrlf=false", "cat-file", "--filters", "HEAD:" + name], cwd=ROOT)
        assert hashlib.sha256(data).hexdigest() == registry["files"][name]


@pytest.mark.parametrize("exit_code", [0, 7])
def test_log_keeps_output_and_propagates_command_failure(tmp_path, exit_code):
    scope, _ = namespace()
    log = tmp_path / "command.log"
    command = [sys.executable, "-c", f"print('kaggle-v63-log'); raise SystemExit({exit_code})"]
    if exit_code:
        with pytest.raises(subprocess.CalledProcessError) as error:
            scope["run_logged"](command, log, echo=False)
        assert error.value.returncode == exit_code
    else:
        scope["run_logged"](command, log, echo=False)
    assert "kaggle-v63-log" in log.read_text(encoding="utf-8")


def test_selection_stages_all_53_files_and_normalizes_colliding_stems(tmp_path):
    scope, _ = namespace()
    root = tmp_path / "inputs"
    for index in range(51):
        path = root / f"report-{index:02d}.pdf"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(str(index).encode())
    for folder, name in (("first", "Báo cáo.pdf"), ("second", "Ba\u0301o ca\u0301o.PDF")):
        path = root / folder / name
        path.parent.mkdir()
        path.write_bytes(folder.encode())
    paths = scope["select_pdfs"](root)
    staged, mapping = scope["stage_pdfs"](paths, tmp_path / "runtime")
    assert len(paths) == len(mapping) == len(list(staged.glob("*.pdf"))) == 53
    assert len({row["document_id"].casefold() for row in mapping}) == 53
    repeated = [row for row in mapping if Path(row["source"]).parent.name in {"first", "second"}]
    assert all(row["document_id"].startswith("Báo cáo-") for row in repeated)
    for row in mapping:
        assert scope["sha256_file"](row["staged"]) == row["sha256"]
    assert len(scope["select_pdfs"](root, limit=1)) == 1
    with pytest.raises(ValueError, match="FILE_LIMIT"):
        scope["select_pdfs"](root, limit=0)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")
    with pytest.raises(ValueError, match="INPUT_ROOT"):
        scope["select_pdfs"](root, [str(outside)])


def test_missing_input_fails_before_runtime_installation(tmp_path):
    scope, cells = namespace()
    scope.update(INPUT_ROOT=tmp_path, RUNTIME_ROOT=tmp_path / "runtime", RESULTS_DIR=tmp_path / "results")
    with pytest.raises(FileNotFoundError, match="Add Input"):
        exec(cells["inputs"], scope)
    assert not (tmp_path / "runtime").exists()


def test_changed_pdf_gets_new_selection_and_cannot_overwrite_frozen_manifest(tmp_path):
    scope, _ = namespace()
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"original")
    first, mapping = scope["stage_pdfs"]([pdf], tmp_path / "runtime")
    manifest = tmp_path / "results/selected_inputs.json"
    scope["write_frozen_json"](manifest, mapping)
    scope["write_frozen_json"](manifest, mapping)
    pdf.write_bytes(b"modified")
    second, changed = scope["stage_pdfs"]([pdf], tmp_path / "runtime")
    assert first != second
    with pytest.raises(ValueError, match="RESULTS_DIR"):
        scope["write_frozen_json"](manifest, changed)
    assert json.loads(manifest.read_text(encoding="utf-8")) == mapping


def test_batch_command_uses_only_v63_options_and_recount_preserves_snapshot(tmp_path):
    scope, cells = namespace()
    scope.update(SUPERVISOR_PYTHON=Path(sys.executable), REPO_DIR=ROOT, STAGED_INPUT=tmp_path / "input",
                 BATCH_DIR=tmp_path / "out", GPU_ENV_ROOT=tmp_path / "gpu", KEYWORDS_PATH=ROOT / "config/keywords.txt",
                 RETRY_FAILED=True, FORCE_OCR=True)
    functions_only(cells["batch"], scope)
    command = scope["build_batch_command"]()
    assert command[command.index("--profile") + 1] == "unified_gpu_full_ocr"
    assert command[command.index("--workers") + 1] == "1"
    assert all(flag in command for flag in ("--resume", "--retry-failed", "--force-ocr"))
    assert not set(command) & {"--pages", "--limit", "--hardware", "--threads-per-worker", "--force"}
    scope["RECOUNT_ONLY"] = True
    with pytest.raises(FileNotFoundError, match="recount"):
        scope["build_batch_command"]()
    scope["BATCH_DIR"].mkdir()
    (scope["BATCH_DIR"] / "batch_state.sqlite").touch()
    assert scope["build_batch_command"]() == [str(sys.executable), str(ROOT / "run_batch.py"),
                                               "--output", str(scope["BATCH_DIR"]), "--recount"]


def test_setup_refreshes_failed_checkout_before_batch_and_freezes_it_after_ledger(tmp_path):
    scope, cells = namespace()
    runtime, results = tmp_path / "runtime", tmp_path / "results"
    key = hashlib.sha256((scope["REPO_URL"] + "\n" + scope["REPO_REF"]).encode()).hexdigest()[:12]
    repo = runtime / ("source-" + key)
    (repo / ".git").mkdir(parents=True)
    (repo / "config").mkdir()
    (repo / "config/profiles-v63.yaml").touch()
    (repo / "config/keywords.txt").write_text("fixture", encoding="utf-8")
    python = repo / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    commands, revision = [], ["fixed-commit"]
    scope.update(RUNTIME_ROOT=runtime, RESULTS_DIR=results, LOG_DIR=results / "logs", BATCH_DIR=results / "documents",
        INPUT_MAPPING=[{"document_id": "fixture"}], REFRESH_SOURCE=True, SYNC_GPU_ENVS=True,
        sys=types.SimpleNamespace(platform="linux", executable=sys.executable),
        shutil=types.SimpleNamespace(which=lambda _: sys.executable),
        subprocess=types.SimpleNamespace(check_output=lambda command, **kwargs:
            scope["REPO_URL"] if "get-url" in command else revision[0]),
        run_logged=lambda command, *a, **kw: commands.append([str(value) for value in command]))
    exec(cells["setup"], scope)
    assert any("fetch" in command for command in commands)
    assert any("--ff-only" in command for command in commands)
    assert any("scripts/setup_v62.py" in command and "--sync" in command for command in commands)
    settings = results / "run_settings.json"
    assert json.loads(settings.read_text(encoding="utf-8"))["commit"] == "fixed-commit"
    scope["BATCH_DIR"].mkdir()
    (scope["BATCH_DIR"] / "batch_state.sqlite").touch()
    commands.clear()
    exec(cells["setup"], scope)
    assert not any("fetch" in command or "merge" in command for command in commands)
    revision[0] = "different-commit"
    with pytest.raises(ValueError, match="Snapshot"):
        exec(cells["setup"], scope)
    assert json.loads(settings.read_text(encoding="utf-8"))["commit"] == "fixed-commit"


def completed_fixture_batch(tmp_path):
    # Existing V6.3 fixture backend is explicitly synthetic, never GPU evidence.
    fixture = runpy.run_path(str(ROOT / "tests/unit/test_v63_pipeline.py"))
    source, output = fixture["setup_inputs"](tmp_path, count=2)
    from src.ocr.application.process_batch import process_batch
    result = process_batch(source, output, {}, ROOT / "config/keywords.txt", fixture["processor_factory"]([]))
    return output, result


def verify(output, ids, summary):
    _, cells = notebook_cells()
    return subprocess.run([sys.executable, "-X", "utf8", "-c", program("VERIFY_PROGRAM", cells["verify"]),
                           str(output), json.dumps(ids), str(summary)], cwd=ROOT,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"}, capture_output=True, text=True, encoding="utf-8")


def test_verification_matches_sqlite_receipts_and_88_column_matrix(tmp_path):
    output, result = completed_fixture_batch(tmp_path)
    summary = tmp_path / "summary.json"
    check = verify(output, [row["id"] for row in result["documents"]], summary)
    assert check.returncode == 0, check.stdout + check.stderr
    payload = json.loads(summary.read_text(encoding="utf-8"))
    assert payload["revision"] == 2
    assert payload["display_keywords"] == 88 and payload["canonical_keywords"] == 87
    assert len(payload["documents"]) == 2
    assert payload["quality_acceptance"] == "NOT_EVALUATED"


@pytest.mark.parametrize("damage", ["matrix", "keyword", "revision"])
def test_verification_refuses_damaged_or_unpublished_output(tmp_path, damage):
    output, result = completed_fixture_batch(tmp_path)
    if damage == "matrix":
        Path(result["matrix"]).write_bytes(b"damaged")
    elif damage == "keyword":
        (output / result["documents"][0]["id"] / "keyword_counts.json").write_text("{}", encoding="utf-8")
    else:
        import sqlite3
        with sqlite3.connect(output / "batch_state.sqlite") as connection:
            connection.execute("UPDATE meta SET value='999' WHERE key='prepared_revision'")
    summary = tmp_path / "summary.json"
    check = verify(output, [row["id"] for row in result["documents"]], summary)
    assert check.returncode != 0
    assert not summary.exists()


@pytest.mark.parametrize("cross_volume", [False, True])
def test_zip_is_available_after_earlier_failure_and_excludes_external_runtime(tmp_path, monkeypatch, cross_volume):
    scope, cells = namespace()
    results, runtime = tmp_path / "results", tmp_path / "runtime"
    (results / "logs").mkdir(parents=True)
    (results / "logs/error.log").write_text("earlier failure", encoding="utf-8")
    runtime.mkdir()
    (runtime / "weights.bin").write_bytes(b"model")
    scope.update(RESULTS_DIR=results, BATCH_DIR=results / "documents", ARCHIVE_PATH=tmp_path / "results.zip",
                 RUNTIME_ROOT=runtime)
    if cross_volume:
        def other_drive(*args):
            raise ValueError("path is on a different drive")
        scope["os"] = types.SimpleNamespace(path=types.SimpleNamespace(relpath=other_drive), replace=os.replace)
    package, display = types.ModuleType("IPython"), types.ModuleType("IPython.display")
    links = []
    display.FileLink, display.display = lambda value: value, links.append
    package.display = display
    monkeypatch.setitem(sys.modules, "IPython", package)
    monkeypatch.setitem(sys.modules, "IPython.display", display)
    exec(cells["download"], scope)
    with ZipFile(tmp_path / "results.zip") as archive:
        assert archive.namelist() == ["logs/error.log"]
    assert len(links) == 1 and links[0].endswith("results.zip")
    with pytest.raises(ValueError, match="RESULTS_DIR"):
        scope["bundle_results"](results, results / "recursive.zip")


@pytest.mark.parametrize("damage", [None, "wheel", "detector"])
def test_model_download_checks_wheel_and_detector_hash_before_replacement(tmp_path, monkeypatch, damage):
    _, cells = notebook_cells()
    detector = b"known detector model"
    target = tmp_path / "models/v62/detector.onnx"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing file")
    registry = {"text_detector": {"model_path": "models/v62/detector.onnx",
                                 "sha256": hashlib.sha256(detector).hexdigest()}}
    (target.parent / "runtime-models.json").write_text(json.dumps(registry), encoding="utf-8")
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("rapidocr_onnxruntime/models/ch_PP-OCRv4_det_infer.onnx",
                         b"wrong model" if damage == "detector" else detector)
    payload = buffer.getvalue()
    metadata = {"urls": [{"packagetype": "bdist_wheel", "filename": "fixture.whl", "url": "https://fixture/wheel",
                           "digests": {"sha256": "wrong" if damage == "wheel" else hashlib.sha256(payload).hexdigest()}}]}
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, **kwargs:
                        io.BytesIO(payload if url == "https://fixture/wheel" else json.dumps(metadata).encode()))
    monkeypatch.chdir(tmp_path)
    source = program("DETECTOR_DOWNLOAD_PROGRAM", cells["models"])
    if damage:
        with pytest.raises(ValueError, match="checksum|hash registry"):
            exec(compile(source, "detector-download", "exec"), {})
        assert target.read_bytes() == b"existing file"
    else:
        exec(compile(source, "detector-download", "exec"), {})
        assert target.read_bytes() == detector

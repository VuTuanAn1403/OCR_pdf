"""Docling bridge, isolated from VietOCR's dependency environment.

One reusable worker per document keeps TableFormer/CodeFormula warm. The worker
is killed on timeout; missing dependencies or inference failures remain visible
in page metadata instead of being reported as successful Docling extraction.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
import uuid
from typing import Any

import pymupdf
import psutil
from src.ocr.domain.models.formula import Formula
from src.ocr.domain.models.table import TableCell, TableStructure
from src.ocr.runtime.paths import cache_dir, models_dir, docling_worker_path
from src.ocr.runtime.process_tree import ProcessTree

ROOT = Path(__file__).resolve().parents[4]
SCHEMA_VERSION = "docling-structure-v1"


class DoclingStructureError(RuntimeError):
    pass


def block_is_covered(bbox: list[float], structures: list[Any], threshold: float = 0.8) -> bool:
    """Remove only blocks mostly contained in a successfully reconstructed item."""
    if len(bbox) != 4:
        return False
    x0, y0, x1, y1 = bbox
    area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if not area:
        return False
    for item in structures:
        if len(item.bbox) != 4:
            continue
        a, b, c, d = item.bbox
        intersection = max(0.0, min(x1, c) - max(x0, a)) * max(0.0, min(y1, d) - max(y0, b))
        if intersection / area >= threshold:
            return True
    return False


def native_formula_hints(page: pymupdf.Page) -> list[dict[str, Any]]:
    """Group split math glyphs; hints require a strong math operator.

    This is heuristic candidate detection, not independent verification.
    """
    blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
    hints = []
    for seed_index, seed in enumerate(blocks):
        if not re.search(r"[∫∑√∂]|\\(?:frac|sum|int)\b", seed[4]):
            continue
        if len(seed[4].strip()) > 80 or len(re.findall(r"[A-Za-z]{4,}", seed[4])) >= 3:
            continue
        rect = pymupdf.Rect(seed[:4])
        gap = max(30.0, rect.height)
        included = {seed_index}
        changed = True
        while changed:
            changed = False
            for index, block in enumerate(blocks):
                if index in included:
                    continue
                text = block[4].strip()
                if re.fullmatch(r"\(\d+\)", text):
                    continue
                if len(text) > 80 or len(re.findall(r"[A-Za-z]{4,}", text)) > 1:
                    continue
                other = pymupdf.Rect(block[:4])
                x_gap = max(0, rect.x0 - other.x1, other.x0 - rect.x1)
                y_gap = max(0, rect.y0 - other.y1, other.y0 - rect.y1)
                if x_gap <= gap and y_gap <= 8:
                    rect |= other
                    included.add(index)
                    changed = True
        bbox = list(rect)
        if not any(block_is_covered(bbox, [SimpleBBox(h["bbox"])], 0.8) for h in hints):
            hints.append({"bbox": bbox, "source": "native_math_hint"})
    return hints


class SimpleBBox:
    def __init__(self, bbox):
        self.bbox = bbox


class DoclingStructureExtractor:
    def __init__(self, options: dict[str, Any]):
        self.options = dict(options)
        self.enabled = bool(options.get("enabled", False))
        configured = os.getenv("OCR_DOCLING_PYTHON") or options.get("python_executable")
        default = ROOT / ".venv-docling" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.python = Path(configured) if configured else default
        self._process = None
        self._tree = None
        self._responses: queue.Queue = queue.Queue()
        self._log = None
        self._job_dir = None
        self._requests = 0
        self._warm = False

    def cache_fingerprint(self) -> str:
        digest = hashlib.sha256(json.dumps(self.options, sort_keys=True).encode())
        digest.update(SCHEMA_VERSION.encode())
        digest.update(str(self.python.absolute()).encode())
        for path in (Path(__file__), docling_worker_path(),
                     ROOT / "requirements-docling.lock.txt", models_dir() / "docling/model_manifest.json"):
            if path.is_file():
                digest.update(path.read_bytes())
        return digest.hexdigest()[:16]

    def _start(self) -> None:
        if not self.python.is_file():
            raise DoclingStructureError("Docling environment missing; run scripts/setup_docling.ps1 or set OCR_DOCLING_PYTHON")
        # Absolute: the worker runs with cwd=ROOT and receives these paths.
        self._job_dir = cache_dir().resolve() / "docling-structure" / uuid.uuid4().hex
        self._job_dir.mkdir(parents=True)
        self._log = (self._job_dir / "worker.log").open("w", encoding="utf-8")
        env = os.environ.copy()
        docling_models = models_dir().resolve() / "docling"
        env.update(HF_HOME=str(docling_models / "huggingface"),
                   EASYOCR_MODULE_PATH=str(docling_models / "easyocr"),
                   OMP_NUM_THREADS=str(self.options.get("num_threads", 4)),
                   TOKENIZERS_PARALLELISM="false", PYTHONIOENCODING="utf-8")
        self._responses = queue.Queue()
        self._process = subprocess.Popen(
            [str(self.python), "-u", str(docling_worker_path()), "--serve"],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log,
            text=True, encoding="utf-8", env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            self._tree = ProcessTree(self._process)
        except BaseException:
            self._process.kill()
            self._process.wait()
            raise
        process, responses = self._process, self._responses

        def read_responses():
            for line in process.stdout:
                try:
                    responses.put(json.loads(line))
                except json.JSONDecodeError:
                    responses.put({"status": "error", "error": "Invalid worker protocol response"})
            responses.put({"status": "error", "error": "Docling worker exited; inspect worker.log"})

        threading.Thread(target=read_responses, daemon=True).start()

    def _ensure_started(self) -> bool:
        """Start the worker if needed; True when this call started it."""
        if self._process is not None:
            return False
        try:
            self._start()
        except OSError as exc:
            self.close()
            raise DoclingStructureError(f"Cannot start Docling worker: {exc}") from exc
        return True

    def warmup(self) -> float:
        """Start the worker and load every model before the first page, so
        start-up time is measured separately from page latency (PERF-05).
        Returns the elapsed seconds; 0.0 when the worker was already warm."""
        if self._warm:
            return 0.0
        started = time.perf_counter()
        self._ensure_started()
        request_id = uuid.uuid4().hex
        timeout = float(self.options.get("startup_timeout_seconds", 900))
        try:
            self._process.stdin.write(json.dumps({"id": request_id, "op": "warmup", "options": self.options}) + "\n")
            self._process.stdin.flush()
            response = self._responses.get(timeout=timeout)
        except queue.Empty:
            self.close()
            raise DoclingStructureError(f"Docling warm-up exceeded {timeout:g}s and was terminated")
        except (OSError, ValueError, RuntimeError) as exc:
            self.close()
            raise DoclingStructureError(f"Docling worker transport failed: {exc}") from exc
        if response.get("id") != request_id or response.get("status") != "success":
            self.close()
            raise DoclingStructureError(response.get("error", "Docling warm-up failed"))
        self._warm = True
        return time.perf_counter() - started

    def extract_page(self, page: pymupdf.Page, page_num: int) -> dict[str, Any]:
        started = time.perf_counter()
        # A running worker has already loaded its models (warm-up or an
        # earlier page); only a worker started by this call needs the
        # start-up timeout.
        cold = self._ensure_started()
        request_id = uuid.uuid4().hex
        path = self._job_dir / f"page-{page_num:04d}-{request_id}.pdf"
        request = {"id": request_id, "pdf": str(path), "options": self.options,
                   "formula_hints": native_formula_hints(page) if self.options.get("formulas", True) else []}
        timeout = float(self.options.get("startup_timeout_seconds", 900) if cold
                        else self.options.get("timeout_seconds", 180))
        try:
            with pymupdf.open() as selected:
                selected.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
                selected.save(path)
            self._process.stdin.write(json.dumps(request) + "\n")
            self._process.stdin.flush()
            response = self._responses.get(timeout=timeout)
        except queue.Empty:
            self.close()
            raise DoclingStructureError(f"Docling worker exceeded {timeout:g}s and was terminated")
        except (OSError, ValueError, RuntimeError) as exc:
            self.close()
            raise DoclingStructureError(f"Docling worker transport failed: {exc}") from exc
        if response.get("id") != request_id or response.get("status") not in ("success", "partial_success"):
            error = response.get("error", "Mismatched Docling worker response")
            self.close()
            raise DoclingStructureError(error)
        self._requests += 1
        self._warm = True
        response.update(elapsed_seconds=time.perf_counter() - started, cold_start=cold,
                        log_path=str(self._job_dir / "worker.log"), schema_version=SCHEMA_VERSION)
        try:
            return self.map_response(response, page_num)
        except (KeyError, TypeError, ValueError) as exc:
            self.close()
            raise DoclingStructureError(f"Invalid Docling structure response: {exc}") from exc

    @staticmethod
    def map_response(response: dict[str, Any], page_num: int) -> dict[str, Any]:
        def validate_bbox(bbox):
            if (len(bbox) != 4 or not all(math.isfinite(v) for v in bbox)
                    or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]):
                raise ValueError("Invalid structure bounding box")

        tables = []
        for index, raw in enumerate(response.get("tables", []), 1):
            cells = [TableCell(row_idx=c["row"], col_idx=c["col"],
                               row_span=c["row_span"], col_span=c["col_span"],
                               text=c["text"], bbox=c.get("bbox") or [],
                               confidence=None, confidence_source="unavailable")
                     for c in raw["cells"]]
            # An empty model output is never a replacement for existing content.
            if not cells or not any(c.text.strip() or (c.bbox and len(c.bbox) == 4) for c in cells):
                continue
            validate_bbox(raw["bbox"])
            if raw["num_rows"] <= 0 or raw["num_cols"] <= 0 or any(
                c.row_idx < 0 or c.col_idx < 0 or c.row_span < 1 or c.col_span < 1
                or c.row_idx + c.row_span > raw["num_rows"]
                or c.col_idx + c.col_span > raw["num_cols"] for c in cells
            ):
                raise ValueError("Invalid table cell indices or spans")
            tables.append(TableStructure(
                table_id=f"p{page_num}_docling_table_{index}", page=page_num,
                bbox=raw["bbox"], num_rows=raw["num_rows"], num_cols=raw["num_cols"],
                cells=cells, caption=raw.get("caption"), confidence=None,
                confidence_source="unavailable", engine="docling_tableformer", needs_review=True,
                provenance={**response.get("provenance", {}), "item_ref": raw.get("item_ref"),
                            "confidence_available": False, "html": raw.get("html", "")},
            ))
        formulas = []
        for index, raw in enumerate(response.get("formulas", []), 1):
            latex = raw.get("latex", "").strip()
            if not latex:
                continue
            validate_bbox(raw["bbox"])
            # Docling currently returns raw LaTeX. Accept wrapped LaTeX too
            # without adding a second set of display math delimiters.
            for left, right in (("$$", "$$"), (r"\[", r"\]"), (r"\(", r"\)")):
                if latex.startswith(left) and latex.endswith(right):
                    latex = latex[len(left):-len(right)].strip()
                    break
            if not latex:
                continue
            formulas.append(Formula(
                formula_id=f"p{page_num}_docling_formula_{index}", page=page_num,
                latex=latex, bbox=raw["bbox"],
                provenance={**response.get("provenance", {}), "item_ref": raw.get("item_ref"),
                            "candidate_source": raw.get("candidate_source", "docling_layout")},
            ))
        return {"tables": tables, "formulas": formulas,
                "metadata": {k: v for k, v in response.items() if k not in ("tables", "formulas")}}

    def close(self) -> None:
        process, self._process = self._process, None
        self._warm = False
        if process is not None:
            tree, self._tree = self._tree, None
            if tree is not None:
                tree.close()
            elif process.poll() is None:
                # A Windows venv executable is a launcher with a Python child.
                # Kill descendants too, so a timeout really stops inference.
                try:
                    children = psutil.Process(process.pid).children(recursive=True)
                except psutil.NoSuchProcess:
                    children = []
                for child in reversed(children):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                process.kill()
                psutil.wait_procs(children, timeout=10)
            if tree is None:
                process.wait(timeout=10)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
        if self._log is not None:
            self._log.close()
            self._log = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

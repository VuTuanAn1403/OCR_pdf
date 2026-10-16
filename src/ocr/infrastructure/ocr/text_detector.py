"""Direct ORT detector with an explicit DB probability-map export contract.

The adapter never receives layout ROIs. Model selection/parity remains a pilot
gate: do not assume an arbitrary ONNX detector uses this tensor contract.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np

from src.ocr.runtime.backend_probe import validate_ort_environment, validate_placement
from src.ocr.runtime.models import model_file_sha256


class ONNXTextDetector:
    METHOD = "onnx-db-map-v1"

    def __init__(self, options, device, *, num_threads=2, diagnostic_legacy_environment=False):
        if options.get("adapter") != self.METHOD:
            raise ValueError(f"Detector must declare adapter={self.METHOD}")
        if options.get("scope", "full_page") != "full_page" or options.get("layout_roi_gate", False):
            raise ValueError("Text detector must cover the full page independently of D1")
        path = Path(options["model_path"]).expanduser().resolve()
        actual = model_file_sha256(path)
        if actual is None or actual != options.get("sha256"):
            raise ValueError("Text detector model is missing or its SHA-256 differs from the locked contract")
        if not diagnostic_legacy_environment:
            validate_ort_environment(device)
        if device not in ("cpu", "cuda"):
            raise ValueError("Detector device must be cpu or cuda")
        import onnxruntime as ort
        if device == "cuda":
            # ORT CUDA 12/cuDNN 9 shares the DLLs shipped with pinned Torch.
            import torch
            if not torch.cuda.is_available():
                raise RuntimeError("GPU_BACKEND_UNAVAILABLE: Torch CUDA unavailable")
            if hasattr(ort, "preload_dlls"):
                ort.preload_dlls()
        provider = "CUDAExecutionProvider" if device == "cuda" else "CPUExecutionProvider"
        if provider not in ort.get_available_providers():
            raise RuntimeError(f"GPU_BACKEND_UNAVAILABLE: {provider} unavailable")
        self.options, self.device, self.sha256 = dict(options), device, actual
        self.limit = int(options.get("max_side", 2048))
        self.threshold = float(options.get("threshold", .3))
        self.box_threshold = float(options.get("box_threshold", .6))
        self.expansion = float(options.get("rectangle_expansion", 1.5))
        if self.limit < 32 or not 0 < self.threshold < 1 or not 0 < self.box_threshold < 1 or self.expansion < 0:
            raise ValueError("Invalid DB detector thresholds/resize contract")
        self.mean = np.asarray(options.get("mean", [.485, .456, .406]), dtype=np.float32)
        self.std = np.asarray(options.get("std", [.229, .224, .225]), dtype=np.float32)
        if self.mean.shape != (3,) or self.std.shape != (3,) or not np.isfinite(self.mean).all() or not np.isfinite(self.std).all() or (self.std <= 0).any():
            raise ValueError("Detector normalization requires finite RGB mean and positive standard deviation")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads, opts.inter_op_num_threads = num_threads, 1
        opts.enable_profiling = True
        profile_dir = Path(options.get("profile_dir", ".cache/backend-probes")).resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)
        opts.profile_file_prefix = str(profile_dir / "detector")
        provider_config = (provider, {"gpu_mem_limit": int(options.get("gpu_mem_limit_mb", 1024)) * 1024**2,
                                      "cudnn_conv_algo_search": "HEURISTIC"}) if device == "cuda" else provider
        self.session = ort.InferenceSession(str(path), sess_options=opts, providers=[provider_config])
        self.session.disable_fallback()
        if self.session.get_providers()[0] != provider:
            raise RuntimeError("Detector session fell back to another execution provider")
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if len(inputs) != 1 or len(outputs) != 1 or inputs[0].type != "tensor(float)":
            raise ValueError("DB map contract requires one float NCHW input and one probability-map output")
        self.input_name, self.output_name = inputs[0].name, outputs[0].name
        self.last_detections, self.placement = [], None
        self.inference_calls = 0

    def prepare(self, image):
        if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
            raise ValueError("Detector expects a full-page uint8 BGR image")
        h, w = image.shape[:2]
        if min(h, w) < 1:
            raise ValueError("Empty page image")
        scale = min(1., self.limit / max(h, w))
        height, width = max(32, int(math.ceil(h * scale / 32)) * 32), max(32, int(math.ceil(w * scale / 32)) * 32)
        rgb = cv2.cvtColor(cv2.resize(image, (width, height)), cv2.COLOR_BGR2RGB).astype(np.float32) / 255.
        tensor = ((rgb - self.mean) / self.std).transpose(2, 0, 1)[None]
        return np.ascontiguousarray(tensor), {"source_shape": [h, w], "resized_shape": [height, width],
                                             "scope": "full_page", "layout_roi_gate": False}

    def decode(self, output, geometry):
        probability = np.asarray(output)
        if probability.ndim != 4 or probability.shape[:2] != (1, 1):
            raise ValueError("Expected DB probability map shaped [1,1,H,W]")
        probability = probability[0, 0]
        if not np.isfinite(probability).all() or probability.min() < 0 or probability.max() > 1:
            raise ValueError("Detector output must contain finite probabilities")
        mask = (probability > self.threshold).astype(np.uint8)
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        h, w = geometry["source_shape"]
        mh, mw = probability.shape
        detections = []
        for contour in contours:
            if cv2.contourArea(contour) < 3:
                continue
            x, y, cw, ch = cv2.boundingRect(contour)
            local_mask = np.zeros((ch, cw), dtype=np.uint8)
            cv2.fillPoly(local_mask, [contour - np.array([[[x, y]]])], 1)
            score = float(cv2.mean(probability[y:y+ch, x:x+cw], mask=local_mask)[0])
            if score < self.box_threshold:
                continue
            center, size, angle = cv2.minAreaRect(contour)
            a, b = size
            if min(a, b) < 1:
                continue
            # Explicit rectangular expansion contract, not an undocumented
            # import of RapidOCR's polygon-offset implementation.
            offset = a * b * self.expansion / (2 * (a + b))
            points = cv2.boxPoints((center, (a + 2 * offset, b + 2 * offset), angle))
            points[:, 0] = np.clip(points[:, 0] * w / mw, 0, w)
            points[:, 1] = np.clip(points[:, 1] * h / mh, 0, h)
            detections.append({"polygon": points.astype(float).tolist(), "score": score,
                               "bbox": [float(points[:, 0].min()), float(points[:, 1].min()),
                                        float(points[:, 0].max()), float(points[:, 1].max())],
                               "ownership_status": "UNASSIGNED"})
        return sorted(detections, key=lambda d: (d["bbox"][1], d["bbox"][0], d["bbox"][3], d["bbox"][2]))

    def detect(self, image):
        self.inference_calls += 1
        self.last_input_shape = list(image.shape)
        tensor, geometry = self.prepare(image)
        output = self.session.run([self.output_name], {self.input_name: tensor})[0]
        self.last_detections = self.decode(output, geometry)
        if self.placement is None:
            profile = Path(self.session.end_profiling())
            self.placement = {**validate_placement(json.loads(profile.read_text(encoding="utf-8")), self.device),
                              "profile_path": str(profile), "model_sha256": self.sha256}
        return self.last_detections

    def __call__(self, image, **_legacy_detection_options):
        return [d["polygon"] for d in self.detect(image)], {"method": self.METHOD, "device": self.device}

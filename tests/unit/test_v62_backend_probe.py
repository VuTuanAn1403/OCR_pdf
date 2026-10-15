from unittest.mock import Mock

import numpy as np
import pytest

from scripts.preflight_v62 import resolve_benchmark_policy
from src.ocr.infrastructure.ocr.text_detector import ONNXTextDetector
from src.ocr.infrastructure.ocr.vietnamese_seq2seq_engine import VietnameseSeq2SeqEngine
from src.ocr.runtime.backend_probe import validate_ort_environment, validate_placement


@pytest.mark.parametrize("distributions", [
    {"onnxruntime": "1", "onnxruntime-gpu": "1"},
    {"onnxruntime-gpu": "1", "rapidocr-onnxruntime": "1"}, {}, {"onnxruntime": "1"},
])
def test_gpu_environment_rejects_mixed_legacy_and_missing_runtime(distributions):
    with pytest.raises(RuntimeError):
        validate_ort_environment("cuda", distributions)


def test_cpu_requires_cpu_distribution_even_when_gpu_package_imports():
    with pytest.raises(RuntimeError):
        validate_ort_environment("cpu", {"onnxruntime-gpu": "1"})
    assert validate_ort_environment("cpu", {"onnxruntime": "1"})["device"] == "cpu"


def kernel(op, provider):
    return {"cat": "Node", "args": {"op_name": op, "provider": provider}}


def test_provider_registration_does_not_certify_compute_placement():
    with pytest.raises(RuntimeError):
        validate_placement([kernel("Conv", "CPUExecutionProvider")], "cuda")
    events = [kernel("Conv", "CUDAExecutionProvider"), kernel("Shape", "CPUExecutionProvider")]
    assert validate_placement(events, "cuda")["status"] == "VERIFIED_INFERENCE"
    with pytest.raises(RuntimeError):
        validate_placement([], "cuda")
    with pytest.raises(RuntimeError):
        validate_placement(events, "cpu")


def test_cpu_benchmark_flag_pair_is_explicit():
    assert resolve_benchmark_policy("cpu", True) == "cpu_acceptance_benchmark"
    for device, flag in (("cpu", False), ("cuda", True)):
        with pytest.raises(ValueError):
            resolve_benchmark_policy(device, flag)


def test_detector_preserves_detections_without_layout_boxes():
    detector = object.__new__(ONNXTextDetector)
    detector.threshold, detector.box_threshold, detector.expansion = .3, .5, 0
    probability = np.zeros((1, 1, 100, 100), dtype=np.float32)
    probability[0, 0, 5:15, 2:30] = .9
    probability[0, 0, 75:90, 70:95] = .9
    detections = detector.decode(probability, {"source_shape": [200, 300]})
    assert len(detections) == 2
    assert all(d["ownership_status"] == "UNASSIGNED" for d in detections)
    assert detections[0]["bbox"][0] < 10 and detections[1]["bbox"][1] >= 140
    with pytest.raises(ValueError):
        detector.decode(np.zeros((100, 100)), {"source_shape": [200, 300]})


def test_oom_retries_smaller_batches_and_preserves_crop_order():
    engine = object.__new__(VietnameseSeq2SeqEngine)
    engine.batch_size = 4
    engine._detector = Mock(return_value=([
        [[x, 5], [x + 20, 5], [x + 20, 20], [x, 20]] for x in (0, 30, 60, 90)], None))
    recognizer = Mock()
    calls = []
    def recognize(crops, **kwargs):
        calls.append(len(crops))
        if len(crops) > 1:
            raise RuntimeError("CUDA out of memory")
        return [f"word{len(calls)}"], [.99]
    recognizer.predict_batch.side_effect = recognize
    engine._recognizer = recognizer
    results = engine.predict_image(np.full((40, 120, 3), 255, dtype=np.uint8))
    assert len(results) == 4
    assert [r["bbox"][0] for r in results] == [0, 30, 60, 90]
    assert calls == [4, 2, 1, 1, 2, 1, 1]
    assert engine.batch_size == 1
    recognizer.predict_batch.side_effect = RuntimeError("CUDA out of memory")
    with pytest.raises(RuntimeError, match="batch 1"):
        engine.predict_image(np.full((40, 120, 3), 255, dtype=np.uint8))

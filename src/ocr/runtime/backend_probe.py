"""Strict ONNX environment/placement checks, run in the owning child process."""
from __future__ import annotations

from collections import Counter
from importlib import metadata

# Shape/index/copy kernels are the only declared CPU exceptions in CUDA mode.
CPU_AUXILIARY_OPS = {"Shape", "Size", "Gather", "GatherElements", "Unsqueeze", "Squeeze",
                     "Concat", "Reshape", "Constant", "Identity", "MemcpyFromHost", "MemcpyToHost"}


def validate_ort_environment(device, distributions=None):
    if device not in ("cpu", "cuda"):
        raise ValueError("Backend device must be explicit cpu or cuda")
    if distributions is None:
        distributions = {}
        for name in ("onnxruntime", "onnxruntime-gpu", "rapidocr-onnxruntime"):
            try:
                distributions[name] = metadata.version(name)
            except metadata.PackageNotFoundError:
                pass
    present = {name for name, version in distributions.items() if version is not None}
    if "rapidocr-onnxruntime" in present:
        raise RuntimeError("V6.2 rejects rapidocr-onnxruntime; use a separate clean GPU/CPU-benchmark environment")
    if {"onnxruntime", "onnxruntime-gpu"} <= present:
        raise RuntimeError("Mixed ONNX Runtime CPU/GPU distributions share one namespace; create a clean environment")
    required = "onnxruntime-gpu" if device == "cuda" else "onnxruntime"
    if required not in present:
        raise RuntimeError(f"Missing required distribution {required} for {device}")
    return {"device": device, "distribution": required, "version": distributions[required]}


def validate_placement(events, device, allowed_cpu_ops=CPU_AUXILIARY_OPS):
    expected = "CUDAExecutionProvider" if device == "cuda" else "CPUExecutionProvider"
    kernels = [event for event in events if event.get("cat") == "Node" and event.get("args", {}).get("provider")]
    if not kernels:
        raise RuntimeError("ONNX profiling contains no kernel placement evidence")
    wrong = [event for event in kernels if event["args"]["provider"] != expected
             and not (device == "cuda" and event["args"]["provider"] == "CPUExecutionProvider"
                      and event["args"].get("op_name") in allowed_cpu_ops)]
    compute = [event for event in kernels if event["args"].get("op_name") not in CPU_AUXILIARY_OPS]
    if wrong or not compute or not any(e["args"]["provider"] == expected for e in compute):
        raise RuntimeError(f"GPU_BACKEND_UNAVAILABLE: required compute kernels are not verified on {expected}")
    return {"status": "VERIFIED_INFERENCE", "expected_provider": expected,
            "kernel_counts": dict(Counter(event["args"]["provider"] for event in kernels)),
            "cpu_auxiliary_ops": sorted({event["args"].get("op_name", "") for event in kernels
                                         if event["args"]["provider"] != expected})}

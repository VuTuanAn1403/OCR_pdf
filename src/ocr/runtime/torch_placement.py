"""Observed forward calls, not merely CUDA availability or model .to()."""
from __future__ import annotations


class TorchPlacementAudit:
    def __init__(self, device):
        import torch
        self.torch, self.expected = torch, device
        self.handles, self.counts, self.errors = [], {}, []
        self.registered = set()

    def attach(self, model, label):
        self.registered.add(label)
        torch = self.torch
        def tensors(value):
            if isinstance(value, torch.Tensor):
                yield value
            elif isinstance(value, dict):
                for v in value.values():
                    yield from tensors(v)
            elif isinstance(value, (list, tuple)):
                for v in value:
                    yield from tensors(v)
        def observed(module, inputs, output):
            params = list(module.parameters(recurse=False))
            if not params:
                return
            values = [*params, *tensors(inputs), *tensors(output)]
            devices = {v.device.type for v in values}
            self.counts[label] = self.counts.get(label, 0) + 1
            if devices != {self.expected}:
                self.errors.append({"component": label, "module": type(module).__name__, "devices": sorted(devices)})
        for module in model.modules():
            self.handles.append(module.register_forward_hook(observed))

    def discover(self, obj, path="docling", depth=0, seen=None):
        seen = set() if seen is None else seen
        if id(obj) in seen or depth > 8:
            return
        seen.add(id(obj))
        if isinstance(obj, self.torch.nn.Module):
            self.attach(obj, path)
        elif isinstance(obj, dict):
            for key, value in obj.items():
                self.discover(value, f"{path}.{key}", depth+1, seen)
        elif isinstance(obj, (list, tuple)):
            for i, value in enumerate(obj):
                self.discover(value, f"{path}.{i}", depth+1, seen)
        elif hasattr(obj, "__dict__") and not isinstance(obj, type):
            for key, value in vars(obj).items():
                if not key.startswith("__"):
                    self.discover(value, f"{path}.{key}", depth+1, seen)

    def report(self, *, require_inference=False):
        if self.errors:
            raise RuntimeError(f"GPU_BACKEND_UNAVAILABLE: wrong Torch placement: {self.errors[:3]}")
        if require_inference and not self.counts:
            raise RuntimeError("GPU_BACKEND_UNAVAILABLE: no observed model forward calls")
        return {"status": "VERIFIED_INFERENCE" if self.counts else "NOT_EXERCISED",
                "device": self.expected, "forward_calls": dict(self.counts),
                "not_exercised": sorted(self.registered - set(self.counts))}

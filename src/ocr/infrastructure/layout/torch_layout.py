"""Pinned local HF RT-DETR layout with actual forward/device evidence."""
from __future__ import annotations

from pathlib import Path
import cv2

from src.ocr.domain.models.region import Region, BoundingBox, ContentType
from src.ocr.infrastructure.layout.model_adapters import image_box_to_pdf


class TorchLayout:
    def get_providers(self):
        return ["TorchCUDA" if self.device == "cuda" else "TorchCPU"]

    def __init__(self, options, device):
        import torch
        from transformers import AutoImageProcessor, AutoModelForObjectDetection
        self.torch, self.device = torch, device
        path = Path(options["model_path"])
        self.processor = AutoImageProcessor.from_pretrained(path, local_files_only=True)
        self.model = AutoModelForObjectDetection.from_pretrained(path, local_files_only=True).to(device).eval()
        self.threshold = options.get("threshold", .35)
        self.placement = None

    def predict(self, image, page, page_num):
        torch = self.torch
        inputs = self.processor(images=cv2.cvtColor(image, cv2.COLOR_BGR2RGB), return_tensors="pt")
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with torch.inference_mode():
            result = self.model(**inputs)
        if self.device == "cuda":
            torch.cuda.synchronize()
        expected = "cuda" if self.device == "cuda" else "cpu"
        devices = {p.device.type for p in self.model.parameters()}
        if devices != {expected} or result.logits.device.type != expected:
            raise RuntimeError("GPU_BACKEND_UNAVAILABLE: layout parameters/output on wrong device")
        self.placement = {"status": "VERIFIED_INFERENCE", "backend": "torch-rtdetr-v2",
                          "parameter_devices": sorted(devices), "output_device": str(result.logits.device)}
        output = self.processor.post_process_object_detection(result, threshold=self.threshold,
                                                               target_sizes=[image.shape[:2]])[0]
        labels = {"list_item": "list", "page_header": "header", "page_footer": "footer", "picture": "figure"}
        regions = []
        for i, (box, label, score) in enumerate(zip(output["boxes"], output["labels"], output["scores"]), 1):
            name = self.model.config.id2label[int(label)].lower().replace("-", "_")
            name = labels.get(name, name)
            bb = image_box_to_pdf(box.tolist(), image.shape, page)
            try:
                kind = ContentType(name.upper())
            except ValueError:
                kind = ContentType.UNKNOWN
            regions.append(Region(region_id=f"p{page_num}_dla_{i}", bbox=BoundingBox(
                x0=bb[0], y0=bb[1], x1=bb[2], y1=bb[3]), content_type=kind, region_type=name,
                label=name, category_id=int(label), confidence=float(score), dla_score=float(score),
                confidence_source="layout_model", quality_score=None, source="ocr"))
        return regions

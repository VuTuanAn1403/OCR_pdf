"""Private phase worker. Only this child imports the model/pipeline modules."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import time

from src.ocr.runtime.atomic_io import atomic_write_json
from src.ocr.runtime.gpu_scheduler import sha256


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def receipt(request, phase, files, **extra):
    root = Path(request["job_dir"])
    predecessor = {"structure": "text", "export": "structure"}.get(phase)
    data = {"generation": request["generation"], "phase": phase,
            "files": {str(Path(path).relative_to(root).as_posix()): sha256(path) for path in files}, **extra}
    if predecessor:
        data["input_receipt_sha256"] = sha256(root / f"{predecessor}.done.json")
    atomic_write_json(root / f"{phase}.done.json", data)


def run_text(request):
    import pymupdf
    import numpy as np
    from src.ocr.application.process_page import ProcessPageUseCase
    from src.ocr.application.route_page import RoutePageUseCase
    from src.ocr.domain.routing.routing_policy import RoutingPolicy
    from src.ocr.runtime.torch_placement import TorchPlacementAudit
    root = Path(request["job_dir"])
    config = copy.deepcopy(request["config"])
    config["docling"]["enabled"] = False  # a deferred stage, recorded as such
    router = RoutePageUseCase(RoutingPolicy(**{
        "reliable_threshold": config["native_quality"]["reliable_threshold"],
        "review_threshold": config["native_quality"]["review_threshold"]}))
    started = time.perf_counter()
    with pymupdf.open(request["pdf"]) as doc:
        pages = request.get("pages") or list(range(1, len(doc)+1))
        if any(type(p) is not int or p < 1 or p > len(doc) for p in pages):
            raise ValueError("Page selection is outside the PDF")
        processor = ProcessPageUseCase(doc, config, request["identity"]["pdf"][:16])
        placement = TorchPlacementAudit(request["device"])
        placement.attach(processor.ocr_engine._recognizer.model, "vietocr")
        init_seconds = time.perf_counter() - started
        files = []
        try:
            for page_num in sorted(set(pages)):
                strict_ocr = config.get("ocr_source_policy") == "gpu_ocr_only_v1"
                detector = processor.ocr_engine._detector
                before_detection = getattr(detector, "inference_calls", 0)
                before_recognition = sum(placement.counts.values())
                page = processor.execute(page_num=page_num, total_pages=len(doc), router=router, resume=False)
                # Real full-page detector probe on native pages as well. It is
                # independent evidence, never a model-layout ROI gate.
                if not strict_ocr:
                    detector.detect(processor.renderer.render_page(page_num, dpi=config["render_dpi"]))
                else:
                    detection_calls = detector.inference_calls-before_detection
                    recognition_calls = sum(placement.counts.values())-before_recognition
                    if detection_calls < 1 or detector.last_detections and recognition_calls < 1:
                        raise RuntimeError("Missing actual GPU OCR inference for document page")
                    if any(b.source == "native" for b in page.blocks):
                        raise RuntimeError("Native text leaked into GPU-only page output")
                    page.metadata["gpu_ocr"] = {"scope":"all_pages", "source_policy":"gpu_ocr_only_v1",
                        "detector_device":request["device"], "recognizer_device":request["device"],
                        "detector_calls":detection_calls,"recognizer_forward_calls":recognition_calls,
                        "detector_scope":"full_page","detector_input_shape":detector.last_input_shape,
                        "render_dpi":config["render_dpi"],"workload":"document",
                        "status":"OCR_PROCESSED" if detector.last_detections else "BLANK_NO_DETECTIONS"}
                if page.quality.errors:
                    raise RuntimeError(f"Phase A page {page_num} has extraction errors; inspect the phase log")
                page.metadata["runtime_phase"] = {"method": "document-two-phase-v1", "phase": "A",
                                                   "generation": request["generation"], "docling_deferred": True}
                path = root / "text" / f"page-{page_num:04d}.json"
                atomic_write_json(path, page.model_dump(mode="json"))
                files.append(path)
            observed = placement.report()
            recognition_workload = "document"
            if observed["status"] != "VERIFIED_INFERENCE":
                if config.get("ocr_source_policy") == "gpu_ocr_only_v1":
                    # Truly blank inputs need no fictitious recognition call.
                    observed["workload"] = "document_no_recognition_regions"
                    recognition_workload = "document_no_recognition_regions"
                else:
                # A native-only input must still prove this backend can run.
                    from PIL import Image, ImageDraw
                    image = Image.new("RGB", (256, 40), "white")
                    ImageDraw.Draw(image).text((5, 5), "GPU OCR 123", fill="black")
                    processor.ocr_engine._recognizer.predict_batch([image], return_prob=True)
                    observed = placement.report(require_inference=True)
                    recognition_workload = "synthetic_backend_probe_native_only_document"
            observed["workload"] = recognition_workload
            layout = processor.content_analyzer.dla_engine._torch_layout.placement
            if not layout:
                raise RuntimeError("Layout model inference was never exercised")
            receipt(request, "text", files, total_pages=len(doc), selected_pages=sorted(set(pages)),
                    init_seconds=init_seconds, seconds=time.perf_counter()-started,
                    placement={"detector": detector.placement, "recognizer": observed, "layout": layout})
        finally:
            processor.close()


def run_structure(request):
    import pymupdf
    from types import SimpleNamespace
    from src.ocr.application.process_page import ProcessPageUseCase
    from src.ocr.domain.models.page import ExtractedPage
    from src.ocr.infrastructure.layout.docling_structure import DoclingStructureExtractor
    root = Path(request["job_dir"])
    checkpoint = read(root / "text.done.json")
    extractor = DoclingStructureExtractor(request["config"]["docling"])
    # Reuse the same absorption/mapping method without constructing VietOCR or
    # the phase A layout model in phase B's coordinating child.
    context = SimpleNamespace(structure_extractor=extractor)
    files, placement = [], []
    started = time.perf_counter()
    try:
        with pymupdf.open(request["pdf"]) as pdf:
            for name in sorted(checkpoint["files"]):
                page = ExtractedPage.model_validate(read(root / name))
                before = time.perf_counter()
                if request["config"].get("ocr_source_policy") == "gpu_ocr_only_v1":
                    from src.ocr.infrastructure.pdf.ocr_bridge import ocr_bridge
                    with ocr_bridge(pdf[page.page_num-1], page, request["config"]["render_dpi"],
                                    font_path=request["config"].get("bridge_font")) as bridge:
                        page.blocks, page.tables, page.formulas = ProcessPageUseCase._enrich_structures(
                            context, bridge[0], page.page_num, page.page_type, page.blocks,
                            page.tables, page.regions, page.metadata)
                    page.metadata["ocr_structure_bridge"] = {"policy":"raster-plus-gpu-text-v1",
                        "native_text_layer_copied":False,"generation":request["generation"]}
                    for structure in [*page.tables,*page.formulas]:
                        structure.provenance["text_source_policy"] = "gpu_ocr_only_v1"
                        structure.provenance["bridge"] = "raster-plus-gpu-text-v1"
                    for table in page.tables:
                        # Embedded font cmaps may extract a space glyph as NBSP.
                        # Undo that bridge encoding artifact; never change digits.
                        for cell in table.cells:
                            cell.text = cell.text.replace("\u00a0", " ")
                        table.provenance["bridge_whitespace"] = "nbsp-to-ocr-space"
                else:
                    page.blocks, page.tables, page.formulas = ProcessPageUseCase._enrich_structures(
                        context, pdf[page.page_num-1], page.page_num, page.page_type, page.blocks,
                        page.tables, page.regions, page.metadata)
                info = page.metadata["docling_structure"]
                if info.get("status") not in ("success", "SKIPPED_NO_STRUCTURE_CANDIDATE") or info.get("errors"):
                    raise RuntimeError(f"Incomplete Docling phase B: {info}")
                placement.append(info.get("provenance", {}).get("placement", {}))
                page.quality.stage_seconds["docling_structure"] = time.perf_counter()-before
                page.quality.time_taken_seconds += time.perf_counter()-before
                page.quality.total_blocks = len(page.blocks)
                page.metadata["runtime_phase"].update(phase="B", docling_deferred=False)
                path = root / "structure" / f"page-{page.page_num:04d}.json"
                atomic_write_json(path, page.model_dump(mode="json"))
                files.append(path)
        receipt(request, "structure", files, seconds=time.perf_counter()-started, placement=placement)
    finally:
        extractor.close()


def run_export(request):
    from src.ocr.domain.models.document import ExtractedDocument
    from src.ocr.domain.models.page import ExtractedPage
    from src.ocr.domain.models.quality import DocumentQualityReport
    from src.ocr.application.export_document import ExportDocumentUseCase
    from src.ocr.infrastructure.export.manifest_exporter import ManifestExporter
    root = Path(request["job_dir"])
    a, b = read(root / "text.done.json"), read(root / "structure.done.json")
    pages = [ExtractedPage.model_validate(read(root / name)) for name in sorted(b["files"])]
    def mean(values):
        values = [v for v in values if v is not None]
        return sum(values)/len(values) if values else None
    counts = {"native": 0, "mixed": 0, "ocr": 0}
    for p in pages:
        kind = p.page_type.value
        counts["ocr" if request["config"].get("ocr_source_policy") == "gpu_ocr_only_v1" else
               "native" if kind in ("NATIVE_TEXT", "PRODUCT_SHOWCASE_COLLAGE") else "mixed" if kind == "MIXED" else "ocr"] += 1
    quality = DocumentQualityReport(document_id=Path(request["pdf"]).stem, total_pages=a["total_pages"],
        mean_native_score=mean([p.quality.native_text_score for p in pages]) or 0,
        mean_ocr_confidence=mean([p.quality.ocr_confidence for p in pages]),
        mean_quality_score=mean([p.quality.quality_score for p in pages]),
        ocr_confidence_scored_pages=sum(p.quality.ocr_confidence is not None for p in pages),
        quality_scored_pages=sum(p.quality.quality_score is not None for p in pages),
        pages_processed_native=counts["native"], pages_processed_mixed=counts["mixed"], pages_processed_ocr=counts["ocr"],
        total_fallback_regions=sum(p.quality.fallback_count for p in pages),
        total_time_seconds=a["seconds"]+b["seconds"], startup_seconds=a["init_seconds"],
        page_reports=[p.quality for p in pages])
    document = ExtractedDocument(document_id=quality.document_id, file_path=request["pdf"],
        file_hash=request["identity"]["pdf"][:16], total_pages=a["total_pages"], selected_pages=a["selected_pages"],
        profile=request["config"]["profile"], pages=pages, quality=quality)
    document.manifest = ManifestExporter.generate_manifest(document, request["config"],
        config_path=Path(request["config_path"]), cache_key=request["generation"])
    document.manifest["gpu_runtime"] = {"method": "document-two-phase-v1", "generation": request["generation"],
        "model_locks": request["model_hashes"], "dependency_locks": request["lock_hashes"],
        "phase_a_placement": a["placement"], "phase_b_placement": b["placement"],
        "quality_acceptance": "NOT_EVALUATED"}
    output = ExportDocumentUseCase.execute(document, Path(request["output"]),
        assets_mode=request.get("assets", "png"), output_mode=request.get("output_mode", "compact"),
        keywords_file=request.get("keywords_file"), count_keywords=request.get("finalize_document", True))
    validation = read(output / "validation.json")
    if validation["status"] == "FAIL":
        raise RuntimeError(f"Export validation failed: {validation['errors'][:5]}")
    path = root / "export.json"
    atomic_write_json(path, {"output": str(output), "validation": validation["status"]})
    receipt(request, "export", [path])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--phase", choices=("text", "structure", "export"), required=True)
    args = parser.parse_args()
    request = read(args.request)
    from src.ocr.runtime.dependency_lock import verify_installed_lock
    lock = next(path for path in request["lock_hashes"] if path.endswith("text-gpu.lock"))
    verify_installed_lock(lock)
    # No mutable weights, locks or source may be reused after phase A starts.
    from src.ocr.runtime.fingerprint import source_fingerprint
    if request["identity"]["source"] != source_fingerprint():
        raise RuntimeError("Source changed during document phases; restart with a new generation")
    for path, digest in {**request["model_hashes"], **request["lock_hashes"]}.items():
        if sha256(path) != digest:
            raise RuntimeError(f"Runtime input changed during document phases: {path}")
    {"text": run_text, "structure": run_structure, "export": run_export}[args.phase](request)


if __name__ == "__main__":
    main()

"""JSON-line worker for TableFormer and CodeFormulaV2; run in .venv-docling."""
from __future__ import annotations
import argparse
import contextlib
from collections import Counter
from importlib.metadata import version
import json
import logging
import os
import re
from pathlib import Path
import sys
import time

PINNED_DOCLING = "2.137.0"


class RuntimeErrors(logging.Handler):
    def __init__(self):
        super().__init__(logging.ERROR)
        self.records = []

    def emit(self, record):
        self.records.append({"logger": record.name, "message": record.getMessage()})


def build_converter(options):
    if options.get("dependency_lock"):
        from src.ocr.runtime.dependency_lock import verify_installed_lock
        verify_installed_lock(options["dependency_lock"])
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        EasyOcrOptions, PdfPipelineOptions, TableFormerMode, TableStructureOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption
    installed = version("docling-slim")
    if installed != PINNED_DOCLING:
        raise RuntimeError(f"Expected docling-slim {PINNED_DOCLING}, installed {installed}")
    num_threads = int(options.get("num_threads", 6))
    try:
        import torch
        torch.set_num_threads(num_threads)
    except Exception:
        pass
    pipeline = PdfPipelineOptions()
    if options.get("strict_gpu"):
        pipeline.layout_batch_size = 1
        pipeline.table_batch_size = 1
        pipeline.ocr_batch_size = 1
    # The parent resolves "auto" to a concrete device; default stays CPU.
    device = AcceleratorDevice(options.get("device", "cpu"))
    pipeline.accelerator_options = AcceleratorOptions(device=device, num_threads=num_threads)
    pipeline.do_table_structure = bool(options.get("tables", True))
    pipeline.table_structure_options = TableStructureOptions(
        mode=TableFormerMode(options.get("table_mode", "fast")), do_cell_matching=True)
    pipeline.do_formula_enrichment = bool(options.get("formulas", True))
    pipeline.generate_parsed_pages = pipeline.do_formula_enrichment
    pipeline.do_code_enrichment = False
    pipeline.enable_remote_services = False
    pipeline.do_ocr = bool(options.get("ocr", False))
    if pipeline.do_ocr:
        pipeline.ocr_options = EasyOcrOptions(lang=options.get("ocr_languages", ["vi", "en"]),
                                              use_gpu=device != AcceleratorDevice.CPU)
    # Bound generation on CPU without silently truncating a result into a
    # claimed correct formula. All model output remains NEEDS_REVIEW.
    pipeline.code_formula_options.model_spec = pipeline.code_formula_options.model_spec.model_copy(
        update={"max_new_tokens": int(options.get("formula_max_tokens", 256))})
    pipeline.code_formula_options.expansion_factor = float(options.get("formula_crop_margin", 0.05))
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline)})
    # Load layout/table/formula models now, so a warm-up request measures
    # start-up separately from the first page (PERF-05).
    converter.initialize_pipeline(InputFormat.PDF)
    if options.get("verify_placement"):
        from src.ocr.runtime.torch_placement import TorchPlacementAudit
        converter.placement_audit = TorchPlacementAudit(options.get("device", "cpu"))
        converter.placement_audit.discover(converter.initialized_pipelines)
    if pipeline.do_formula_enrichment:
        # The 2.137.0 stage hardcodes 2048 per VlmEngineInput, overriding
        # model_spec.max_new_tokens. Bound actual engine requests as well.
        from docling.models.stages.code_formula.code_formula_vlm_model import CodeFormulaVlmModel
        converter.formula_request_limits = []
        for runtime in converter.initialized_pipelines.values():
            for model in runtime.enrichment_pipe:
                if isinstance(model, CodeFormulaVlmModel) and model.engine is not None:
                    if options.get("strict_gpu"):
                        model.elements_batch_size = 1
                    predict = model.engine.predict_batch
                    limit = pipeline.code_formula_options.model_spec.max_new_tokens
                    def bounded_predict(inputs, _predict=predict, _limit=limit):
                        for value in inputs:
                            value.max_new_tokens = min(value.max_new_tokens, _limit)
                            converter.formula_request_limits.append(value.max_new_tokens)
                        return _predict(inputs)
                    model.engine.predict_batch = bounded_predict
    return converter, pipeline


def convert_structures(converter, pipeline, pdf, formula_hints=()):
    from docling_core.types.doc import DocItemLabel, TableItem, PictureItem, BoundingBox, CoordOrigin, ProvenanceItem
    started = time.perf_counter()
    if pipeline.do_formula_enrichment:
        converter.formula_request_limits.clear()
    result = converter.convert(Path(pdf), raises_on_error=True)
    if hasattr(converter, "placement_audit"):
        converter.placement_audit.report(require_inference=True)
    doc = result.document
    tables, formulas, layout_items = [], [], []
    formula_hints = list(formula_hints)
    picture_candidates = []
    labels = Counter()

    def bbox_in_points(box, height):
        if box is None:
            return []
        top = box.to_top_left_origin(page_height=height)
        return [float(top.l), float(top.t), float(top.r), float(top.b)]

    for item, _level in doc.iterate_items():
        labels[item.label.value] += 1
        if not item.prov:
            continue
        prov = item.prov[0]
        height = doc.pages[prov.page_no].size.height
        bbox = bbox_in_points(prov.bbox, height)
        layout_items.append({"item_ref": item.self_ref, "label": item.label.value,
                             "bbox": bbox, "page": prov.page_no,
                             "text": getattr(item, "text", ""),
                             "confidence": None, "confidence_source": "unavailable"})
        if isinstance(item, PictureItem) and pipeline.do_formula_enrichment:
            # Layout models sometimes label an equation as a picture. Require
            # math evidence from OCR/native cells and a compact region before
            # asking CodeFormula to recognize it. Keep the source picture.
            page = result.pages[prov.page_no - 1]
            cells = page.parsed_page.textline_cells if page.parsed_page else []
            candidate_text = []
            for cell in cells:
                a, b, c, d = bbox_in_points(cell.rect.to_bounding_box(), height)
                if bbox[0] <= (a+c)/2 <= bbox[2] and bbox[1] <= (b+d)/2 <= bbox[3]:
                    candidate_text.append(cell.text)
            text = " ".join(candidate_text)
            width = doc.pages[prov.page_no].size.width
            compact = (bbox[2]-bbox[0]) < width*0.8 and (bbox[3]-bbox[1]) < height*0.25
            is_math = bool(re.search(r"[∫∑√∂=]", text)) and len(re.findall(r"[A-Za-z]{4,}", text)) < 3
            picture_candidates.append({"bbox": bbox, "text": text, "accepted": compact and is_math})
            if compact and is_math:
                formula_hints.append({"bbox": bbox, "source": "docling_picture_math_hint"})
        if isinstance(item, TableItem) and pipeline.do_table_structure:
            data = item.data
            cells = [{"row": c.start_row_offset_idx, "col": c.start_col_offset_idx,
                      "row_span": c.row_span, "col_span": c.col_span, "text": c.text,
                      "bbox": bbox_in_points(c.bbox, height)} for c in data.table_cells]
            tables.append({"item_ref": item.self_ref, "bbox": bbox,
                           "num_rows": data.num_rows, "num_cols": data.num_cols, "cells": cells,
                           "html": item.export_to_html(doc=doc), "caption": item.caption_text(doc) or None})
        elif item.label == DocItemLabel.FORMULA and pipeline.do_formula_enrichment:
            formulas.append({"item_ref": item.self_ref, "bbox": bbox, "latex": item.text,
                             "candidate_source": "docling_layout"})
    if formula_hints and pipeline.do_formula_enrichment:
        import pypdfium2
        from docling.datamodel.base_models import ItemAndImageEnrichmentElement
        from docling.models.stages.code_formula.code_formula_vlm_model import CodeFormulaVlmModel
        model = next(m for runtime in converter.initialized_pipelines.values()
                     for m in runtime.enrichment_pipe if isinstance(m, CodeFormulaVlmModel))
        with pypdfium2.PdfDocument(pdf) as source:
            source_page = source[0]
            scale = model.images_scale
            image = source_page.render(scale=scale).to_pil()
            for hint in formula_hints:
                x0, y0, x1, y1 = hint["bbox"]
                area = (x1 - x0) * (y1 - y0)
                def overlap(other):
                    a, b, c, d = other["bbox"]
                    return max(0, min(x1, c) - max(x0, a)) * max(0, min(y1, d) - max(y0, b))
                if any(overlap(f) / area >= 0.8 for f in formulas):
                    continue
                if hint["source"] == "docling_picture_math_hint" and any(
                    f.get("candidate_source") == "native_math_hint" and overlap(f) > 0 for f in formulas
                ):
                    continue
                margin = pipeline.code_formula_options.expansion_factor
                dx, dy = (x1 - x0) * margin, (y1 - y0) * margin
                crop = image.crop((max(0, int((x0-dx)*scale)), max(0, int((y0-dy)*scale)),
                                   min(image.width, int((x1+dx)*scale)), min(image.height, int((y1+dy)*scale))))
                item = doc.add_text(label=DocItemLabel.FORMULA, text="", prov=ProvenanceItem(
                    page_no=1, charspan=(0, 0), bbox=BoundingBox(l=x0, t=y0, r=x1, b=y1,
                                                              coord_origin=CoordOrigin.TOPLEFT)))
                enriched = list(model(doc=doc, element_batch=[ItemAndImageEnrichmentElement(item=item, image=crop)]))
                if enriched and enriched[0].text.strip():
                    formulas = [f for f in formulas if overlap(f) == 0]
                    formulas.append({"item_ref": item.self_ref, "bbox": hint["bbox"],
                                     "latex": enriched[0].text, "candidate_source": hint["source"]})
    hub = Path(os.environ.get("HF_HOME", "")) / "hub"
    revisions = {str(p.relative_to(hub)): p.read_text().strip()
                 for p in hub.glob("models--*/refs/*") if p.is_file()}
    provenance = {
        "docling_version": version("docling-slim"), "docling_core_version": version("docling-core"),
        "docling_ibm_models_version": version("docling-ibm-models"),
        "device": pipeline.accelerator_options.device.value, "table_model": pipeline.table_structure_options.kind,
        "table_mode": pipeline.table_structure_options.mode.value,
        "formula_model": pipeline.code_formula_options.model_spec.default_repo_id,
        "formula_max_tokens": pipeline.code_formula_options.model_spec.max_new_tokens,
        "formula_generation_limits_observed": getattr(converter, "formula_request_limits", []),
        "resolved_hf_revisions": revisions, "ocr_languages": pipeline.ocr_options.lang,
        "ocr_enabled": pipeline.do_ocr, "coordinates": "PDF_POINTS_TOP_LEFT",
        "conversion_status": result.status.value,
        "placement": (converter.placement_audit.report(require_inference=True)
                      if hasattr(converter, "placement_audit") else {"status": "NOT_EVALUATED"}),
    }
    return {"status": result.status.value, "tables": tables, "formulas": formulas,
            "layout_items": layout_items,
            "provenance": provenance, "inference_seconds": time.perf_counter() - started,
            "detected_item_labels": dict(labels),
            "picture_formula_candidates": picture_candidates,
            "errors": [str(error) for error in result.errors]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true", required=True)
    parser.parse_args()
    converter = pipeline = None
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            # Model libraries occasionally print diagnostics. Keep stdout
            # exclusively reserved for the machine-readable worker protocol.
            with contextlib.redirect_stdout(sys.stderr):
                if converter is None:
                    started = time.perf_counter()
                    converter, pipeline = build_converter(request["options"])
                    startup_seconds = time.perf_counter() - started
                else:
                    startup_seconds = 0.0
                if request.get("op") == "warmup":
                    print(json.dumps({"id": request["id"], "status": "success",
                                      "startup_seconds": startup_seconds}), file=sys.__stdout__, flush=True)
                    continue
                captured = RuntimeErrors()
                logging.getLogger().addHandler(captured)
                try:
                    response = convert_structures(converter, pipeline, request["pdf"], request.get("formula_hints", []))
                finally:
                    logging.getLogger().removeHandler(captured)
                if captured.records:
                    response["errors"].extend(captured.records)
                    response["status"] = "partial_success"
                    if any("code_formula" in r["logger"] for r in captured.records):
                        # Docling can retain backend text after a model error.
                        # That text must not be relabelled as recognized LaTeX.
                        response["formulas"] = []
            response["id"] = request["id"]
        except Exception as exc:
            response = {"id": request.get("id"), "status": "error",
                        "error": f"{type(exc).__name__}: {exc}"}
            import traceback
            traceback.print_exc(file=sys.stderr)
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

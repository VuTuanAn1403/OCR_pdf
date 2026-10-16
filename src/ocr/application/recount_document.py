"""Recount accepted V6.3 OCR through the same finalizer and publication gate."""
from pathlib import Path
import json
from src.ocr.application.process_batch import process_batch
from src.ocr.runtime.v63_backend import GPUProcessor, verify_runtime


def recount_batch(output):
    output=Path(output).resolve()
    config=json.loads((output/"batch_config.json").read_text(encoding="utf-8"))
    snapshot=json.loads((output/"keyword_dictionary.snapshot.json").read_text(encoding="utf-8"))
    source=Path(config["input_path"])
    backend=config["backend"]
    return process_batch(source,output,config,None,GPUProcessor(backend,assets=config["assets"],output_mode=config["output_mode"]),
        resume=True,force_recount=True,frozen_dictionary=snapshot,identity_check=lambda:verify_runtime(backend))

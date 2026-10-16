"""Validate the entire declared keyword group, including loss of every file."""
import json
from pathlib import Path
from src.ocr.domain.models.processing import KEYWORD_ARTIFACTS
from src.ocr.infrastructure.postprocessing.keyword_contract import validate_report
from src.ocr.infrastructure.export.keyword_v63 import verify_document
from src.ocr.runtime.gpu_scheduler import sha256


def validate_keyword_artifacts(output, manifest):
    contract = manifest.get("keyword_contract")
    if contract is None:
        if manifest.get("configuration", {}).get("ocr_source_policy") == "gpu_ocr_only_v1":
            raise ValueError("V6.3 keyword_contract is missing")
        return
    if contract.get("schema_version") != "keyword-contract-v1" or contract.get("required_artifacts") != list(KEYWORD_ARTIFACTS):
        raise ValueError("Invalid keyword_contract declaration")
    output = Path(output)
    for name in KEYWORD_ARTIFACTS:
        if not (output/name).is_file():
            raise ValueError(f"Required keyword artifact missing: {name}")
    snapshot = contract["dictionary"]
    report = json.loads((output/"keyword_counts.json").read_text(encoding="utf-8"))
    validate_report(report,snapshot,output/"result.md", output.name)
    if report["selected_pages"] != manifest["selected_pages"]:
        raise ValueError("Keyword page scope differs from OCR output")
    for name in ("keyword_validation.json","keyword_receipt.json"):
        seal = json.loads((output/name).read_text(encoding="utf-8"))
        if seal.get("report_sha256") != sha256(output/"keyword_counts.json"):
            raise ValueError(f"{name} keyword report checksum mismatch")
        if name == "keyword_validation.json" and seal.get("status") != "PASS":
            raise ValueError("Keyword validation is not PASS")
    verify_document(output/"count_keywords.xlsx",report,snapshot)

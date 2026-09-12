#!/usr/bin/env python3
"""
End-to-end Smoke Test for Vietnamese OCR Pipeline V3.1.

Verifies:
1. Configuration loading from YAML
2. OCR engine initialization
3. Processing a real PDF page
4. Output file generation (Markdown, JSON, Manifest)
5. JSON parse validation
6. Vietnamese Unicode (NFC) diacritic preservation
"""

import sys
import json
import tempfile
import unicodedata
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml
from src.ocr.application.convert_document import ConvertDocumentUseCase

def test_load_config():
    print("[1/5] Loading configuration...")
    cfg_file = PROJECT_ROOT / "config" / "profiles.yaml"
    assert cfg_file.exists(), f"Configuration file not found: {cfg_file}"
    with open(cfg_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert "profiles" in data, "No 'profiles' key in profiles.yaml"
    assert "balanced" in data["profiles"], "'balanced' profile missing"
    print("      Configuration loaded successfully.")

def test_pipeline_execution():
    print("[2/5] Running end-to-end OCR pipeline on sample PDF...")
    sample_pdf = PROJECT_ROOT / "examples" / "sample.pdf"
    if not sample_pdf.exists():
        sample_pdf = PROJECT_ROOT / "sample.pdf"
    assert sample_pdf.exists(), f"Sample PDF not found at {sample_pdf}"

    with tempfile.TemporaryDirectory() as tmp_dir:
        converter = ConvertDocumentUseCase()
        # Run on page 1 of sample
        doc = converter.execute(
            file_path=str(sample_pdf),
            profile_name="balanced",
            pages=[1],
            output_dir=tmp_dir,
            resume=False,
            benchmark=True
        )

        assert doc is not None, "Pipeline returned None document"
        assert len(doc.pages) == 1, f"Expected 1 page, got {len(doc.pages)}"
        print(f"      Processed {len(doc.pages)} page in {doc.quality.total_time_seconds:.2f}s.")

        print("[3/5] Verifying generated output artifacts...")
        doc_dir = Path(tmp_dir) / doc.document_id
        res_md = doc_dir / "result.md"
        res_json = doc_dir / "result.json"
        manifest_json = doc_dir / "manifest.json"
        quality_json = doc_dir / "quality.json"

        assert res_md.exists(), "result.md missing"
        assert res_json.exists(), "result.json missing"
        assert manifest_json.exists(), "manifest.json missing"
        assert quality_json.exists(), "quality.json missing"
        print("      All required output artifacts exist.")

        print("[4/5] Validating JSON schema and parsing...")
        with open(res_json, "r", encoding="utf-8") as f:
            res_data = json.load(f)
        with open(manifest_json, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
        with open(quality_json, "r", encoding="utf-8") as f:
            quality_data = json.load(f)

        assert "document_id" in res_data, "result.json missing document_id"
        assert "pipeline_version" in manifest_data, "manifest.json missing pipeline_version"
        assert "mean_quality_score" in quality_data, "quality.json missing mean_quality_score"
        print("      JSON files parsed and validated successfully.")

        print("[5/5] Validating Unicode diacritics and NFC normalization...")
        with open(res_md, "r", encoding="utf-8") as f:
            md_text = f.read()

        assert len(md_text.strip()) > 0, "result.md text is empty"
        assert unicodedata.is_normalized("NFC", md_text), "Markdown output is not normalized to NFC"

        # Check for presence of authentic Vietnamese diacritics in output
        vietnamese_chars = set("àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ")
        found_chars = {c for c in md_text.lower() if c in vietnamese_chars}
        assert len(found_chars) >= 5, f"Expected authentic Vietnamese diacritics, found: {found_chars}"
        print(f"      Verified authentic Vietnamese diacritics preserved ({len(found_chars)} unique accented characters).")

def main():
    print("=" * 60)
    print("VIETNAMESE OCR V3.1 - SMOKE TEST")
    print("=" * 60)
    try:
        test_load_config()
        test_pipeline_execution()
        print("=" * 60)
        print("SMOKE TEST RESULT: PASS (All checks succeeded)")
        print("=" * 60)
        sys.exit(0)
    except AssertionError as e:
        print(f"\n[SMOKE TEST FAILED]: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n[SMOKE TEST ERROR]: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()

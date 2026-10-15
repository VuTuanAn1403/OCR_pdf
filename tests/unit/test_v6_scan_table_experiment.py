"""Scanned-table experiment tooling (proposal V6 C3.5(a), review item R-10).
Variant A's hidden word layer must round-trip Vietnamese text and never
alter the source; without L5 GT the comparison stays NOT_EVALUATED."""
import hashlib
from types import SimpleNamespace

import pymupdf
import pytest
import yaml
from unittest.mock import Mock

from benchmark.scientific_evaluation.scan_table_experiment import _score, _variant_a, default_font, run
from src.ocr.domain.models.table import TableCell, TableStructure
from src.ocr.infrastructure.table.hidden_text_layer import (
    Word, assign_words_to_cells, build_derived_page, estimate_words, font_record)

FONT = default_font()
needs_font = pytest.mark.skipif(FONT is None, reason="Noto Sans (Vietnamese glyphs) not installed")
LINES = [{"text": "Chỉ tiêu Năm 2025", "bbox": [72, 100, 300, 116]},
         {"text": "Doanh thu thuần 1.234.567", "bbox": [72, 130, 300, 146]},
         {"text": "Lợi nhuận sau thuế (25.000)", "bbox": [72, 160, 300, 176]}]


def _scan(path):
    with pymupdf.open() as art:
        page = art.new_page(width=595, height=842)
        page.draw_rect(pymupdf.Rect(60, 90, 320, 190), color=(0, 0, 0))
        pixmap = page.get_pixmap(dpi=100)
    with pymupdf.open() as doc:
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, pixmap=pixmap)
        doc.save(path)
    return path


@needs_font
def test_word_boxes_split_the_line_in_order():
    words = estimate_words(LINES[1]["text"], LINES[1]["bbox"], pymupdf.Font(fontfile=str(FONT)))
    assert [w.text for w in words] == ["Doanh", "thu", "thuần", "1.234.567"]
    assert words[0].bbox[0] == 72 and words[-1].bbox[2] == pytest.approx(300, abs=0.1)
    assert all(a.bbox[2] <= b.bbox[0] for a, b in zip(words, words[1:]))


@needs_font
def test_hidden_layer_round_trips_vietnamese_and_leaves_the_source_alone(tmp_path):
    pdf = _scan(tmp_path / "scan.pdf")
    before = hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert font_record(FONT)["covers_vietnamese"]
    with pymupdf.open(pdf) as doc:
        derived, words, report = build_derived_page(doc[0], LINES, FONT)
        extracted = [w[4] for w in derived[0].get_text("words")]
        spans = [s for s in derived[0].get_texttrace()]
        derived.close()
    assert report["reextracted_word_match"] == 1.0 and "thuế" in extracted and "(25.000)" in extracted
    assert all(span["type"] == 3 for span in spans)  # invisible: render mode 3
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == before


def test_words_are_checked_against_cells():
    table = TableStructure(table_id="t", page=1, bbox=[70, 95, 310, 180], num_rows=2, num_cols=2, cells=[
        TableCell(row_idx=0, col_idx=0, text="", bbox=[70, 95, 190, 120]),
        TableCell(row_idx=0, col_idx=1, text="", bbox=[185, 95, 310, 120]),   # overlaps cell 0
        TableCell(row_idx=1, col_idx=0, text="", bbox=[70, 125, 190, 150])])
    words = [Word("a", (80, 100, 100, 110), 0), Word("b", (188, 100, 190, 110), 0),
             Word("c", (200, 160, 220, 170), 1), Word("d", (400, 400, 420, 410), 2)]
    report = assign_words_to_cells(words, [table])
    assert (report["in_one_cell"], report["in_several_cells"], report["in_table_no_cell"],
            report["outside_tables"]) == (1, 1, 1, 1)
    assert report["uncertain_near_edge"] >= 1


@needs_font
def test_variant_a_runs_docling_options_on_the_derived_page(tmp_path):
    pdf = _scan(tmp_path / "scan.pdf")
    seen = {}

    class FakeExtractor:
        def extract_page(self, page, page_num):
            seen["words"] = [w[4] for w in page.get_text("words")]
            return {"tables": [TableStructure(table_id="x", page=page_num, bbox=[60, 90, 320, 190], num_rows=1,
                                              num_cols=1, cells=[TableCell(row_idx=0, col_idx=0, text="Doanh thu",
                                                                           bbox=[60, 90, 320, 190])])]}

    page = SimpleNamespace(page_num=1, page_type=SimpleNamespace(value="SCANNED_IMAGE"))
    blocks = [{"source": "primary_ocr", **line} for line in LINES]
    with pymupdf.open(pdf) as doc:
        result = _variant_a(doc[0], page, {"metadata": {}, "blocks": blocks}, FakeExtractor(), FONT, None)
    assert result["status"] == "RUN" and "thuần" in seen["words"]
    assert result["word_cells"]["in_one_cell"] == result["layer"]["words"]
    assert result["quality"]["status"] == "NOT_EVALUATED"


@needs_font
def test_experiment_without_gt_or_docling_is_not_evaluated(tmp_path):
    pdf = _scan(tmp_path / "EXP_scan.pdf")
    result = run(pdf, [1], tmp_path / "exp", font=FONT, profile="fast")
    (record,) = result["records"]
    assert record["C"]["status"] == "NOT_IMPLEMENTED" and record["B"]["quality"]["status"] == "NOT_EVALUATED"
    assert record["A"]["status"] in ("SKIPPED_DOCLING_DISABLED", "SKIPPED_NOT_A_SCAN_PAGE", "SKIPPED_NO_OCR_WORDS")
    assert result["conclusion"] == "NOT_EVALUATED" and result["font"]["sha256"]
    with pytest.raises(ValueError, match="new or empty"):
        run(pdf, [1], tmp_path / "exp", font=FONT, profile="fast")


def test_teds_scoring_counts_missing_tables_as_zero():
    html = "<table><tr><td>Doanh thu</td><td>1.234</td></tr></table>"
    reference = [{"page": 1, "bbox": [0, 0, 100, 100], "html": html},
                 {"page": 1, "bbox": [0, 200, 100, 300], "html": html}]
    scored = _score(reference, [{"page": 1, "bbox": [0, 0, 100, 100], "html": html}])
    assert scored["matched"] == 1 and scored["missing_output"] == 1 and scored["teds_mean"] == pytest.approx(0.5)
    assert _score(None, [])["status"] == "NOT_EVALUATED"


@needs_font
def test_a_uses_ocr_evidence_consumed_by_b_tables_and_saves_inspectable_artifacts(tmp_path):
    pdf = _scan(tmp_path / "scan.pdf")
    seen = {}

    class Extractor:
        def extract_page(self, page, page_num):
            seen["words"] = [w[4] for w in page.get_text("words")]
            return {"tables": []}

    page = SimpleNamespace(page_num=1, page_type=SimpleNamespace(value="SCANNED_IMAGE"))
    page_json = {"blocks": [], "metadata": {"table_ocr_evidence": {
        "method": "post-fallback-pre-table-v1", "lines": [{"source": "primary_ocr", **line} for line in LINES]}}}
    with pymupdf.open(pdf) as doc:
        result = _variant_a(doc[0], page, page_json, Extractor(), FONT, None, tmp_path / "A")
    assert result["status"] == "RUN" and "thuần" in seen["words"]
    assert result["ocr_evidence_source"] == "post-fallback-pre-table-v1"
    assert (tmp_path / "A/page-0001.pdf").is_file() and (tmp_path / "A/page-0001.json").is_file()


@needs_font
@pytest.mark.parametrize("bbox", [[1, 2, 1, 3], [1, 4, 3, 2], [float("nan"), 1, 3, 4]])
def test_bad_geometry_is_a_page_failure_instead_of_aborting_the_experiment(tmp_path, bbox):
    pdf = _scan(tmp_path / "scan.pdf")
    page = SimpleNamespace(page_num=1, page_type=SimpleNamespace(value="SCANNED_IMAGE"))
    with pymupdf.open(pdf) as doc:
        result = _variant_a(doc[0], page, {"metadata": {}, "blocks": [
            {"source": "primary_ocr", "text": "Doanh thu", "bbox": bbox}]}, None, FONT, None)
    assert result["status"] == "FAILED" and "bbox" in result["error"]


@needs_font
def test_a_without_ocr_words_cannot_be_reported_as_a_comparison(tmp_path):
    pdf = _scan(tmp_path / "scan.pdf")
    page = SimpleNamespace(page_num=1, page_type=SimpleNamespace(value="SCANNED_IMAGE"))
    with pymupdf.open(pdf) as doc:
        result = _variant_a(doc[0], page, {"metadata": {}, "blocks": []}, None, FONT, None)
    assert result["status"] == "SKIPPED_NO_OCR_WORDS"


@needs_font
def test_tightly_spaced_word_operators_keep_explicit_word_boundaries(tmp_path):
    with pymupdf.open(_scan(tmp_path / "tight.pdf")) as doc:
        derived, words, report = build_derived_page(doc[0], [
            {"text": "CỔ PHẦN", "bbox": [106.28, 67.9, 155.38, 84.44]},
            {"text": "Số cuối", "bbox": [200, 200, 235, 215]}], FONT)
        try:
            assert report["reextraction_exact"]
            assert [w[4] for w in derived[0].get_text("words")] == [w.text for w in words]
        finally:
            derived.close()


def test_cell_audit_detects_missing_and_repeated_words_without_claiming_gt_accuracy():
    table = TableStructure(table_id="t", page=1, bbox=[0, 0, 100, 60], num_rows=1, num_cols=2, cells=[
        TableCell(row_idx=0, col_idx=0, text="thu thu", bbox=[0, 0, 50, 60]),
        TableCell(row_idx=0, col_idx=1, text="", bbox=[50, 0, 100, 60])])
    report = assign_words_to_cells([Word("thu", (10, 10, 20, 20), 0),
                                   Word("2025", (70, 10, 80, 20), 0)], [table])
    assert report["in_one_cell"] == 2 and report["cells_content_mismatch"] == 2
    assert report["cell_content_checks"][0]["unexpected_word_occurrences"] == 1
    assert report["cell_content_checks"][1]["missing_word_occurrences"] == 1
    assert report["needs_review"] and "not accuracy" in report["interpretation"]


def test_pipeline_retains_paired_ocr_evidence_before_table_blocks_are_removed(tmp_path, monkeypatch):
    from src.ocr.application import process_page
    from src.ocr.application.convert_document import ConvertDocumentUseCase
    from src.ocr.runtime.config import load_profiles
    _, profiles = load_profiles()
    profiles["fast"].update(retain_table_ocr_lines=True, orientation_fallback_enabled=False,
                            document_layout={"method": "region_tree_graph_v1"})
    profiles["fast"]["docling"] = {"enabled": True, "tables": True, "formulas": False, "page_selection": "all"}
    config = tmp_path / "profiles.yaml"
    config.write_text(yaml.safe_dump({"profiles": profiles}))
    extractor = Mock(enabled=True, options=profiles["fast"]["docling"])
    extractor.cache_fingerprint.return_value = "fixture-extractor"
    extractor.extract_page.return_value = {"tables": [TableStructure(
        table_id="t", page=1, bbox=[60, 90, 320, 190], num_rows=2, num_cols=2, engine="docling_tableformer",
        cells=[TableCell(row_idx=0, col_idx=0, text="Doanh thu"),
               TableCell(row_idx=0, col_idx=1, text="Năm 2025"),
               TableCell(row_idx=1, col_idx=0, text="Lợi nhuận"),
               TableCell(row_idx=1, col_idx=1, text="(25.000)")])], "formulas": [],
        "metadata": {"status": "success"}}
    monkeypatch.setattr(process_page, "RapidOCREngine", Mock())
    monkeypatch.setattr(process_page, "DoclingStructureExtractor", Mock(return_value=extractor))
    def predict(self, image, **_):
        sx, sy = image.shape[1] / 595, image.shape[0] / 842
        return ([{"text": line["text"], "confidence": .99, "bbox": [
            line["bbox"][0] * sx, line["bbox"][1] * sy, line["bbox"][2] * sx, line["bbox"][3] * sy]}
            for line in LINES], None)
    monkeypatch.setattr(process_page.ProcessPageUseCase, "_predict_with_orientation_fallback", predict)
    result = ConvertDocumentUseCase(str(config)).execute(
        str(_scan(tmp_path / "paired.pdf")), "fast", output_dir=str(tmp_path / "out"),
        cache_dir=str(tmp_path / "cache"), assets_mode="none")
    page = result.pages[0]
    # The deliberately incomplete table omits words/numbers from every OCR
    # line. V6.2 restores those sources instead of treating geometry as proof.
    assert page.tables and len(page.blocks) == len(LINES)
    assert {b.text for b in page.blocks} == {line["text"] for line in LINES}
    assert all("TABLE_SOURCE_UNMAPPED" in b.review_reason_codes for b in page.blocks)
    evidence = page.metadata["table_ocr_evidence"]
    assert evidence["method"] == "post-fallback-pre-table-v1"
    assert [line["text"] for line in evidence["lines"]] == [line["text"] for line in LINES]

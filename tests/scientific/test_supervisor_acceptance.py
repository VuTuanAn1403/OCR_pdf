"""Independent acceptance checks for the Antigravity supervision of V5.

These exercise the report runner as well as metrics. Existing metric-only tests
do not verify document identity, failed pages, final Markdown or timing sources.
PubTabNet's reference TEDS denominator counts descendants, excluding table root:
https://github.com/ibm-aur-nlp/PubTabNet/blob/master/src/metric.py
"""

import json
from pathlib import Path

import pytest

import scripts.run_scientific_benchmark as runner
from benchmark.scientific_evaluation.ocr_metric import compute_der, compute_reading_order
from benchmark.scientific_evaluation.teds_metric import compute_teds


@pytest.fixture(autouse=True)
def skip_report_machine_inventory(monkeypatch):
    # Inventory is independent of the evaluation contract being tested.
    monkeypatch.setattr(runner, "capture_environment_metadata", lambda _: {})


def make_document(tmp_path: Path, text: str = "Reference paragraph long enough."):
    doc = tmp_path / "docA"
    pages = doc / "pages"
    pages.mkdir(parents=True)
    (pages / "page-0001.md").write_text(text, encoding="utf-8")
    (doc / "result.md").write_text(text, encoding="utf-8")
    gt = tmp_path / "gt"
    gt.mkdir()
    return doc, gt


def test_unrelated_ground_truth_is_never_a_fallback(tmp_path):
    doc, gt = make_document(tmp_path)
    (gt / "clw_page_0001.txt").write_text("Wrong document reference.", encoding="utf-8")
    (gt / "sample_page_0001.txt").write_text("Another wrong document.", encoding="utf-8")

    report = runner.evaluate_document_scientific(doc, gt)

    assert report["academic_metrics"]["evaluated_pages"] == 0
    assert report["academic_metrics"]["mean_cer"] is None
    assert report["page_evaluations"] == []


def test_missing_prediction_cannot_pass_latency(tmp_path):
    try:
        report = runner.evaluate_document_scientific(tmp_path / "missing")
    except (FileNotFoundError, ValueError):
        return  # An explicit validation failure is also an acceptable contract.

    timing = report["timing_and_throughput"]
    assert timing["wall_clock_seconds"] is None, "No timing was measured."
    assert timing["seconds_per_page"] is None
    assert timing["cpu_latency_compliance"].get("is_compliant") is not True


def test_gt_page_with_missing_prediction_is_not_dropped(tmp_path):
    doc, gt = make_document(tmp_path)
    (gt / "docA_page_0001.txt").write_text("Reference paragraph long enough.", encoding="utf-8")
    (gt / "docA_page_0002.txt").write_text("Second paragraph missing from the output.", encoding="utf-8")

    report = runner.evaluate_document_scientific(doc, gt)

    by_page = {page["page_num"]: page for page in report["page_evaluations"]}
    assert 2 in by_page, "The ground-truth sample universe must include failed pages."
    assert by_page[2]["cer"] >= 1.0


def test_partial_run_uses_processed_pages_for_throughput(tmp_path):
    doc, gt = make_document(tmp_path)
    (doc / "benchmark.json").write_text(json.dumps({
        "wall_clock_seconds": 2.0,
        "pages_benchmarked": [1],
    }), encoding="utf-8")
    (doc / "quality.json").write_text(json.dumps({
        "total_pages": 100,
        "pages_processed_native": 1,
        "pages_processed_ocr": 0,
        "pages_processed_mixed": 0,
        "total_time_seconds": 2.0,
    }), encoding="utf-8")

    report = runner.evaluate_document_scientific(doc, gt)
    timing = report["timing_and_throughput"]

    assert timing["total_pages"] == 1
    assert timing["seconds_per_page"] == pytest.approx(2.0)
    assert timing["throughput_pps"] == pytest.approx(0.5)
    assert timing["cpu_latency_compliance"]["is_compliant"] is False


def test_final_markdown_loss_is_evaluated(tmp_path):
    doc, gt = make_document(tmp_path)
    (gt / "docA_page_0001.txt").write_text("Reference paragraph long enough.", encoding="utf-8")
    # Per-page Markdown is correct, but the final document exporter lost it.
    (doc / "result.md").write_text("", encoding="utf-8")

    report = runner.evaluate_document_scientific(doc, gt)
    final_text = report["markdown_integrity"]["text_evaluation"]

    assert final_text is not None, "Final Markdown must be compared with available GT."
    assert final_text["cer"] >= 1.0


def test_missing_single_reading_order_block_is_not_perfect():
    result = compute_reading_order(["Reference paragraph long enough."], "")
    assert result["matched_fraction"] == 0.0
    assert result["penalized_score"] == 0.0


def test_der_counts_inserted_accented_character():
    assert compute_der("\u00e1", "\u00e1\u00e1") > 0.0


def test_teds_matches_reference_denominator_for_one_cell():
    reference = "<table><tr><td>A</td></tr></table>"
    prediction = "<table><tr><td>B</td></tr></table>"
    # One changed cell costs 1; two descendants (tr, td) give 1 - 1/2.
    assert compute_teds(reference, prediction) == pytest.approx(0.5)


def test_invalid_tables_never_receive_a_perfect_teds():
    try:
        score = compute_teds("invalid reference", "invalid prediction")
    except ValueError:
        return
    assert score is None or score == 0.0


def test_unrounded_latency_fails_cpu_limit():
    from benchmark.scientific_evaluation.environment_profiler import verify_cpu_latency_compliance
    # 1.8004 s/page for MIXED must fail limit 1.80 (Review V5 Item 7)
    res = verify_cpu_latency_compliance("MIXED", 1.8004)
    assert not res["is_compliant"]


def test_mixed_page_categorizes_as_mixed_not_scanned(tmp_path):
    import json
    from scripts.run_scientific_benchmark import evaluate_document_scientific

    doc = tmp_path / "docMixed"
    doc.mkdir()
    pages = doc / "pages"
    pages.mkdir()
    (pages / "page-0001.md").write_text("Trang scan", encoding="utf-8")
    (pages / "page-0002.md").write_text("Trang mixed", encoding="utf-8")
    (doc / "result.md").write_text("Trang scan\n\nTrang mixed", encoding="utf-8")

    # 1 scanned page + 1 mixed page
    (doc / "quality.json").write_text(json.dumps({
        "pages_processed_native": 0,
        "pages_processed_ocr": 1,
        "pages_processed_mixed": 1,
        "total_time_seconds": 4.0,  # 2.0 s/page
    }), encoding="utf-8")

    report = evaluate_document_scientific(doc)
    # Must be categorized as MIXED, and 2.0 s/page must exceed MIXED upper limit (1.8s)
    assert report["timing_and_throughput"]["category"] == "MIXED"
    assert not report["timing_and_throughput"]["cpu_latency_compliance"]["is_compliant"]


def test_partial_ground_truth_evaluates_only_matching_pages(tmp_path):
    import json
    from scripts.run_scientific_benchmark import evaluate_document_scientific

    doc = tmp_path / "docPartial"
    doc.mkdir()
    pages = doc / "pages"
    pages.mkdir()
    (pages / "page-0001.md").write_text("Đoạn văn trang 1 chính xác.", encoding="utf-8")
    (pages / "page-0002.md").write_text("Đoạn văn trang 2 chính xác.", encoding="utf-8")
    (doc / "result.md").write_text("Đoạn văn trang 1 chính xác.\n\nĐoạn văn trang 2 chính xác.", encoding="utf-8")

    (doc / "quality.json").write_text(json.dumps({
        "total_pages": 2,
        "pages_processed_native": 2,
        "pages_processed_ocr": 0,
        "pages_processed_mixed": 0,
    }), encoding="utf-8")

    gt = tmp_path / "gt"
    gt.mkdir()
    # GT only has page 1!
    (gt / "docPartial_page_1.txt").write_text("Đoạn văn trang 1 chính xác.", encoding="utf-8")

    report = evaluate_document_scientific(doc, gt_dir=gt)
    md_eval = report["markdown_integrity"]
    assert md_eval["is_partial_ground_truth"] is True
    assert md_eval["gt_coverage_ratio"] == 0.5
    # Text CER must be evaluated on page 1 only (CER == 0.0), NOT penalized for page 2
    assert md_eval["text_evaluation"]["cer"] == 0.0


def test_partial_ground_truth_detects_empty_result_markdown(tmp_path):
    """If result.md is completely empty, partial GT evaluation must flag CER=1.0 instead of reading page files."""
    import json
    from scripts.run_scientific_benchmark import evaluate_document_scientific

    doc = tmp_path / "docEmptyResult"
    doc.mkdir()
    pages = doc / "pages"
    pages.mkdir()
    (pages / "page-0001.md").write_text("Văn bản trang 1 đầy đủ.", encoding="utf-8")
    (pages / "page-0002.md").write_text("Văn bản trang 2 đầy đủ.", encoding="utf-8")
    # result.md is EMPTY!
    (doc / "result.md").write_text("", encoding="utf-8")

    (doc / "quality.json").write_text(json.dumps({
        "total_pages": 2,
        "pages_processed_native": 2,
        "pages_processed_ocr": 0,
        "pages_processed_mixed": 0,
    }), encoding="utf-8")

    gt = tmp_path / "gt"
    gt.mkdir()
    (gt / "docEmptyResult_page_1.txt").write_text("Văn bản trang 1 đầy đủ.", encoding="utf-8")

    report = evaluate_document_scientific(doc, gt_dir=gt)
    md_eval = report["markdown_integrity"]
    assert md_eval["is_partial_ground_truth"] is True
    # CER must be 1.0 because result.md had no text
    assert md_eval["text_evaluation"]["cer"] == 1.0


def test_running_header_does_not_suppress_midpage_headings():
    """Verify that _filter_running_headers_footers only classifies blocks near margins (<=15% top / >=85% bottom)."""
    from src.ocr.application.export_document import ExportDocumentUseCase
    from src.ocr.domain.models.document import ExtractedDocument
    from src.ocr.domain.models.page import ExtractedPage
    from src.ocr.domain.models.text_block import TextBlock

    # Create 3 pages (>= 30% of 3 pages is 1 page, ceiling 2 pages)
    # Header appears at top (y=50, H=1000 => 5%) on pages 1 and 2
    # But on page 3, the exact same text appears at the middle (y=500, H=1000 => 50%)
    from src.ocr.domain.routing.page_type import PageType
    from src.ocr.domain.models.quality import PageQualityReport

    p1 = ExtractedPage(
        page_num=1, width=800, height=1000,
        page_type=PageType.NATIVE_TEXT,
        quality=PageQualityReport(page=1, page_type="native_text"),
        blocks=[
            TextBlock(page=1, block_id="p1_h", text="CÔNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", bbox=[50, 40, 750, 60], block_type="header"),
            TextBlock(page=1, block_id="p1_b", text="Nội dung trang 1", bbox=[50, 200, 750, 400], block_type="paragraph")
        ]
    )
    p2 = ExtractedPage(
        page_num=2, width=800, height=1000,
        page_type=PageType.NATIVE_TEXT,
        quality=PageQualityReport(page=2, page_type="native_text"),
        blocks=[
            TextBlock(page=2, block_id="p2_h", text="CÔNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", bbox=[50, 40, 750, 60], block_type="header"),
            TextBlock(page=2, block_id="p2_b", text="Nội dung trang 2", bbox=[50, 200, 750, 400], block_type="paragraph")
        ]
    )
    p3 = ExtractedPage(
        page_num=3, width=800, height=1000,
        page_type=PageType.NATIVE_TEXT,
        quality=PageQualityReport(page=3, page_type="native_text"),
        blocks=[
            # Mid-page heading with the exact same string
            TextBlock(page=3, block_id="p3_mid", text="CÔNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", bbox=[50, 490, 750, 510], block_type="header"),
            TextBlock(page=3, block_id="p3_b", text="Nội dung trang 3", bbox=[50, 600, 750, 800], block_type="paragraph")
        ]
    )

    from src.ocr.domain.models.quality import DocumentQualityReport

    doc = ExtractedDocument(
        document_id="doc_header_test",
        file_path="dummy.pdf",
        file_hash="hash123",
        total_pages=3,
        quality=DocumentQualityReport(document_id="doc_header_test", total_pages=3),
        pages=[p1, p2, p3]
    )
    # Three distinct pages are required in V6.2; retain a separate mid-page
    # occurrence to verify that it does not inherit the running-header role.
    p3.blocks.insert(0, p1.blocks[0].model_copy(update={"page": 3}))
    ExportDocumentUseCase._detect_running_headers_and_footers(doc)

    # Pages 1 & 2 should be marked as running_header
    assert doc.pages[0].blocks[0].block_type == "running_header"
    assert doc.pages[1].blocks[0].block_type == "running_header"
    # Page 3 mid-page block MUST remain 'header' (NOT 'running_header')
    assert doc.pages[2].blocks[0].block_type == "running_header"
    assert doc.pages[2].blocks[1].block_type == "header"


def test_rapidocr_polygon_direct_unpack(monkeypatch):
    """Verify that VietnameseSeq2SeqEngine.predict_image handles raw polygons from detector."""
    import numpy as np
    from src.ocr.infrastructure.ocr.vietnamese_seq2seq_engine import VietnameseSeq2SeqEngine

    engine = VietnameseSeq2SeqEngine()

    # Mock detector returning raw polygon list: [[[x1,y1], [x2,y2], [x3,y3], [x4,y4]]]
    mock_poly = [[[10, 10], [100, 10], [100, 40], [10, 40]]]
    monkeypatch.setattr(engine, "_detector", lambda img, **kwargs: (mock_poly, 0.05))

    class MockRecognizer:
        def predict_batch(self, crops, return_prob=False):
            return ["Văn bản thử nghiệm"], [0.98]

    monkeypatch.setattr(engine, "_recognizer", MockRecognizer())

    dummy_img = np.full((100, 200, 3), 255, dtype=np.uint8)
    results = engine.predict_image(dummy_img)

    assert len(results) == 1
    assert results[0]["text"] == "Văn bản thử nghiệm"
    assert results[0]["confidence"] == pytest.approx(0.98)
    assert results[0]["bbox"] == [10.0, 10.0, 100.0, 40.0]


"""
Unit tests for V5.0 Scientific Evaluation Suite.
Verifies TEDS, mAP@50, DER, CER, WER, Spearman Reading Order, and LaTeX Exporters.
"""

import pytest
from benchmark.scientific_evaluation.teds_metric import compute_teds, compute_teds_struct
from benchmark.scientific_evaluation.dla_metric import compute_iou, compute_dla_map, compute_class_ap
from benchmark.scientific_evaluation.ocr_metric import compute_cer, compute_wer, compute_der, compute_reading_order_spearman
from benchmark.scientific_evaluation.latex_generator import generate_latex_comparison_table, generate_latex_ablation_table
from src.ocr.infrastructure.layout.dla_engine import DLAEngine, BoundingBox, ContentType


def test_teds_identical_tables():
    t1 = "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>"
    t2 = "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>"
    score = compute_teds(t1, t2)
    assert score == 1.0
    assert compute_teds_struct(t1, t2) == 1.0


def test_teds_different_structure_and_text():
    t1 = "<table><tr><td>A</td><td>B</td></tr></table>"
    t2 = "<table><tr><td>A</td><td>B</td><td>C</td></tr></table>"
    teds = compute_teds(t1, t2)
    assert 0.0 < teds < 1.0


def test_dla_iou_computation():
    box1 = [0.0, 0.0, 10.0, 10.0]
    box2 = [5.0, 0.0, 15.0, 10.0]
    # Intersection is 5 * 10 = 50. Union is 100 + 100 - 50 = 150. IoU = 50/150 = 0.3333
    iou = compute_iou(box1, box2)
    assert round(iou, 2) == 0.33


def test_dla_map_calculation():
    gt = [
        {"bbox": [10.0, 10.0, 100.0, 50.0], "category": "header"},
        {"bbox": [10.0, 60.0, 100.0, 120.0], "category": "table"},
    ]
    pred = [
        {"bbox": [10.0, 10.0, 100.0, 50.0], "category": "header", "score": 0.98},
        {"bbox": [10.0, 60.0, 100.0, 120.0], "category": "table", "score": 0.95},
    ]
    res = compute_dla_map(gt, pred, classes=["header", "table"])
    assert res["mAP@50"] == 1.0


def test_der_diacritic_error_rate():
    ref = "Cộng hòa xã hội chủ nghĩa Việt Nam"
    # 1 diacritic dropped: "Việt" -> "Viet"
    pred = "Cộng hòa xã hội chủ nghĩa Viet Nam"
    der = compute_der(ref, pred)
    assert 0.0 < der < 0.25
    assert compute_der(ref, ref) == 0.0


def test_reading_order_spearman():
    refs = [
        "1. Phần mở đầu tài liệu giới thiệu nội dung",
        "2. Phần phương pháp và thực nghiệm chi tiết",
        "3. Phần kết luận và hướng phát triển tương lai"
    ]
    pred_correct = "1. Phần mở đầu tài liệu giới thiệu nội dung\n2. Phần phương pháp và thực nghiệm chi tiết\n3. Phần kết luận và hướng phát triển tương lai"
    rho = compute_reading_order_spearman(refs, pred_correct)
    assert rho == 1.0


def test_latex_table_generation():
    data = [
        {"name": "Baseline (V4.3)", "map50": 0.725, "teds": 0.684, "cer": 0.048, "der": 0.038, "pps": 0.08, "time_per_page": 12.5},
        {"name": "V5.0 (Ours)", "map50": 0.938, "teds": 0.897, "cer": 0.018, "der": 0.011, "pps": 1.25, "time_per_page": 0.8}
    ]
    latex = generate_latex_comparison_table(data)
    assert r"\begin{table*}" in latex
    assert r"\textbf{V5.0 (Ours)}" in latex
    assert r"\toprule" in latex


def test_dla_engine_classification():
    dla = DLAEngine()
    bb_header = BoundingBox(x0=50, y0=20, x1=500, y1=45)
    c_type, label, _ = dla._classify_text_block(
        text="TỔNG CÔNG TY CẤP NƯỚC SÀI GÒN",
        bbox=bb_header,
        page_h=800,
        page_w=600,
        page_num=2,
        base_font_size=11,
        header_band_y=80,
        footer_band_y=720,
    )
    # A single margin block cannot establish a running header.
    assert label == "text"
    frozen = DLAEngine(legacy_furniture=True)
    assert frozen._classify_text_block("TỔNG CÔNG TY CẤP NƯỚC SÀI GÒN", bb_header,
                                      800, 600, 2, 11, 80, 720)[1] == "header"
    assert c_type == ContentType.TEXT

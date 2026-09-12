import pytest
from pathlib import Path
from benchmark.evaluate import evaluate_document

def test_golden_pages_accuracy():
    gt_dir = Path("benchmark/ground_truth")
    assert gt_dir.exists(), "Ground truth directory missing"

    pred_dir = Path("output/CLW_Baocaothuongnien_2020")
    if not (pred_dir / "pages" / "page-0001.md").exists():
        pytest.skip("CLW predictions not yet generated in output/")

    report = evaluate_document(pred_dir, gt_dir)
    assert report.pages_evaluated >= 4

    print(f"\n[GOLDEN EVALUATION] Mean CER: {report.mean_cer:.2%}")
    print(f"[GOLDEN EVALUATION] Mean WER: {report.mean_wer:.2%}")
    print(f"[GOLDEN EVALUATION] Mean Diacritic Accuracy: {report.mean_diacritic_accuracy:.2%}")
    print(f"[GOLDEN EVALUATION] Mean Table Accuracy: {report.mean_table_accuracy:.2%}")
    print(f"[GOLDEN EVALUATION] Mean Reading Order Accuracy: {report.mean_reading_order_accuracy:.2%}")

    # Golden assertion criteria
    assert report.mean_diacritic_accuracy >= 0.80, f"Diacritic accuracy {report.mean_diacritic_accuracy} below 80%"
    assert report.mean_table_accuracy >= 0.95, f"Table accuracy {report.mean_table_accuracy} below 95%"
    assert report.mean_reading_order_accuracy >= 0.50, f"Reading order accuracy {report.mean_reading_order_accuracy} below 50%"

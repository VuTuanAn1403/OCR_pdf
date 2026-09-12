import pytest
from pathlib import Path
from src.ocr.application.convert_document import ConvertDocumentUseCase

def test_clw_benchmark_four_pages(tmp_path):
    pdf_path = "inputs/CLW_Baocaothuongnien_2020.pdf"
    if not Path(pdf_path).exists():
        pytest.skip(f"Benchmark PDF {pdf_path} not found")

    converter = ConvertDocumentUseCase()
    out_dir = tmp_path / "output"

    doc = converter.execute(
        file_path=pdf_path,
        profile_name="balanced",
        pages=[1, 3, 5, 11],
        output_dir=str(out_dir),
        resume=False,
        benchmark=True
    )

    assert doc.document_id == "CLW_Baocaothuongnien_2020"
    assert len(doc.pages) == 4

    doc_dir = out_dir / "CLW_Baocaothuongnien_2020"
    assert (doc_dir / "result.md").exists()
    assert (doc_dir / "result.json").exists()
    assert (doc_dir / "manifest.json").exists()
    assert (doc_dir / "quality.json").exists()
    assert (doc_dir / "benchmark.json").exists()

    for p_num in [1, 3, 5, 11]:
        assert (doc_dir / "pages" / f"page-{p_num:04d}.json").exists()
        assert (doc_dir / "pages" / f"page-{p_num:04d}.md").exists()

    # Check timing
    seconds_per_page = doc.quality.total_time_seconds / 4
    print(f"\n[BENCHMARK] Average seconds per page: {seconds_per_page:.2f}s")
    assert seconds_per_page < 15.0, f"Average seconds per page {seconds_per_page} exceeded 15s"

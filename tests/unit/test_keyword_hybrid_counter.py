import tempfile
from pathlib import Path

from src.ocr.domain.models.keyword import KeywordReport, KeywordCount, FuzzyMatchDetail
from src.ocr.infrastructure.postprocessing.keyword_counter import KeywordCounter
from src.ocr.infrastructure.postprocessing.fuzzy_keyword_matcher import FuzzyKeywordMatcher
from src.ocr.infrastructure.llm.llm_verifier import LLMVerifier
from src.ocr.infrastructure.export.keyword_exporter import KeywordExporter


def test_fuzzy_matcher_extracts_diacritic_variants():
    text = (
        "Doanh nghiệp tích cực ứng dụng tri tuệ nhan tạo và trí tue nhân tao vào hoạt động, "
        "bên cạnh việc chuẩn hóa hệ thống trí tuệ nhân tạo hiện hữu."
    )
    keywords = ["trí tuệ nhân tạo"]
    # Exact span for the 3rd appearance: 'trí tuệ nhân tạo'
    exact_start = text.index("hệ thống trí tuệ nhân tạo") + len("hệ thống ")
    exact_end = exact_start + len("trí tuệ nhân tạo")
    exact_spans = [(exact_start, exact_end)]

    candidates = FuzzyKeywordMatcher.extract_candidates(
        text=text,
        keywords=keywords,
        exact_spans=exact_spans,
        min_similarity=0.86,
    )

    cands = candidates["trí tuệ nhân tạo"]
    assert len(cands) == 2, f"Expected 2 fuzzy candidates, got {len(cands)}"

    matched_texts = [c["matched_text"] for c in cands]
    assert "tri tuệ nhan tạo" in matched_texts
    assert "trí tue nhân tao" in matched_texts

    for c in cands:
        assert 0.86 <= c["similarity"] < 1.0


def test_keyword_counter_tier1_tier2_tier3_hybrid():
    text = (
        "Báo cáo triển khai tri tuệ nhan tạo kết hợp trí tue nhân tao và trí tuệ nhân tạo chuẩn. "
        "Đồng thời phát triển dữ liệu lớn và dư liệu lớn."
    )
    keywords = ["trí tuệ nhân tạo", "dữ liệu lớn"]

    report: KeywordReport = KeywordCounter.count_keywords(
        text=text,
        keywords=keywords,
        document_id="test_doc",
        enable_llm=True,
        min_similarity=0.86,
    )

    assert report.total_keywords_searched == 2
    assert report.total_exact_matches == 2  # 1 trí tuệ nhân tạo, 1 dữ liệu lớn
    assert report.total_fuzzy_matches == 3  # 2 for trí tuệ nhân tạo, 1 for dữ liệu lớn
    assert report.total_matches == 5
    assert report.llm_enabled is True

    # Check breakdown for 'trí tuệ nhân tạo'
    ttnt = next(k for k in report.keywords if k.keyword == "trí tuệ nhân tạo")
    assert ttnt.exact_count == 1
    assert ttnt.fuzzy_count == 2
    assert ttnt.count == 3
    assert len(ttnt.variants) == 2

    # Check breakdown for 'dữ liệu lớn'
    dll = next(k for k in report.keywords if k.keyword == "dữ liệu lớn")
    assert dll.exact_count == 1
    assert dll.fuzzy_count == 1
    assert dll.count == 2
    assert len(dll.variants) == 1


def test_keyword_exporter_format():
    report = KeywordReport(
        document_id="demo_report",
        source_file="result.md",
        total_keywords_searched=1,
        total_exact_matches=1,
        total_fuzzy_matches=1,
        total_matches=2,
        llm_enabled=True,
        keywords=[
            KeywordCount(
                keyword="trí tuệ nhân tạo",
                exact_count=1,
                fuzzy_count=1,
                count=2,
                variants=[
                    FuzzyMatchDetail(
                        matched_text="tri tuệ nhan tạo",
                        similarity=0.88,
                        context="ứng dụng tri tuệ nhan tạo vào",
                        is_llm_confirmed=True
                    )
                ]
            )
        ]
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        exported_path = KeywordExporter.export_markdown(report, out_dir)
        assert exported_path.exists()
        content = exported_path.read_text(encoding="utf-8")
        assert "Khớp chính xác (100%)" in content
        assert "Khớp gần đúng (LLM Recheck)" in content
        assert "`tri tuệ nhan tạo` (88%)" in content

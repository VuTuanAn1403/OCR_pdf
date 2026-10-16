"""Content-loss regressions from the audited annual reports, plus controls.

These are targeted counterexamples, not an annotated accuracy benchmark.
"""
import copy
import json

import pytest

from src.ocr.application.export_document import ExportDocumentUseCase
from src.ocr.application.resolve_document_furniture import resolve_document_furniture
from src.ocr.domain.models.document import ExtractedDocument
from src.ocr.domain.models.evidence_block import EvidenceBlock
from src.ocr.domain.models.page import ExtractedPage
from src.ocr.domain.models.quality import DocumentQualityReport, PageQualityReport
from src.ocr.domain.models.region import BoundingBox, Region, ContentType
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.routing.page_type import PageType
from src.ocr.infrastructure.layout.dla_engine import DLAEngine
from src.ocr.infrastructure.postprocessing.document_furniture import DocumentFurnitureResolver
from src.ocr.infrastructure.postprocessing.reading_order import ReadingOrderSorter
from src.ocr.runtime.config import ConfigError, document_layout_settings, furniture_settings


CASES = [
    ("AAA", 28, 595.44, "header", [74.9, 36.06, 541.61, 50.71],
     "• Đầu tư vào R&D ứng dụng, đẩy mạnh chuyển đổi số, tăng cường tự động hóa dây chuyền sản"),
    ("AAA", 133, 595.44, "header", [56.9, 36.47, 541.65, 49.75],
     "hóa 5S, đào tạo bảo mật thông tin,… Công ty bắt đầu triển khai hệ thống E-learning, triển khai số hóa"),
    ("BSI", 79, 595.28, "header", [77.11, 55.05, 229.11, 68.42],
     "Khối Chiến lược và Chuyển đổi số:"),
    ("CTD", 78, 1190.55, "footer", [902.83, 763.25, 1073, 776.5],
     "tiền đề cho những đổi mới cần thiết."),
    ("CTG", 43, 1190.55, "footer", [666.14, 760.23, 978.09, 770.56],
     "Ông Lê Anh Tuấn - Giám đốc Số hóa kênh phân phối Bán lẻ VietinBank nhận"),
    ("CTG", 75, 1190.55, "footer", [70.86, 761.19, 526.91, 774.28],
     "Năm 2023, VietinBank xây dựng và triển khai Chương trình Chuyển đổi số giai đoạn 2024 - 2028."),
    ("CTG", 75, 1190.55, "footer", [70.86, 773.19, 526.91, 786.28],
     'Hành trình chuyển đổi số của VietinBank mang tên "Project X01" cùng đối tác tư vấn chiến lược'),
    ("CTG", 128, 1190.55, "header", [99.21, 67.65, 527.36, 83.44],
     'Với phương châm "Lấy khách hàng là trung tâm", VietinBank thực hiện số hóa'),
    ("CTG", 145, 1190.55, "footer", [87.88, 769.21, 228.93, 782.55],
     "iii. Đổi mới sáng tạo là đột phá"),
]


def make_doc(pages):
    for n, blocks in enumerate(pages, 1):
        for b in blocks:
            b.region_id = f"r{n}"
    extracted = [ExtractedPage(page_num=n, width=600, height=842, blocks=blocks,
        evidence_blocks=[EvidenceBlock(page=n, block_id=f"p{n}b{i}", text=b.text, bbox=b.bbox,
                                      source="native", orientation=0, region_id=f"r{n}") for i, b in enumerate(blocks)],
        regions=[Region(region_id=f"r{n}", bbox=BoundingBox(x0=0, y0=0, x1=600, y1=842),
                        content_type=ContentType.TEXT, status="EXTRACTED", orientation_status="CONFIDENT")],
        page_type=PageType.NATIVE_TEXT, quality=PageQualityReport(page=n, page_type="NATIVE_TEXT"))
        for n, blocks in enumerate(pages, 1)]
    return ExtractedDocument(document_id="furniture-v3", file_path="unused.pdf", file_hash="0123456789abcdef",
        total_pages=len(pages), selected_pages=list(range(1, len(pages)+1)), pages=extracted,
        manifest={"document_id": "furniture-v3", "file_hash": "0123456789abcdef",
                  "selected_pages": list(range(1, len(pages)+1))},
        quality=DocumentQualityReport(document_id="furniture-v3", total_pages=len(pages)))


def block(text, n, y=25, **kwargs):
    return TextBlock(text=text, page=n, bbox=[40, y, 500, y+14], **kwargs)


@pytest.mark.parametrize("company,number,width,old_role,box,text", CASES,
                         ids=[f"{c[0]}-{c[1]}-{i}" for i, c in enumerate(CASES)])
def test_real_margin_content_is_not_header_footer(company, number, width, old_role, box, text):
    engine = DLAEngine(use_onnx=False, device="cpu")
    _, proposed, _ = engine._classify_text_block(text, BoundingBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
        841.89, width, number, 10, 84.189, 757.701)
    assert proposed not in {"header", "footer", "running_header", "running_footer", "page_number"}
    doc = make_doc([[block("Nội dung khác ở giữa trang.", n, 300)] for n in range(1, 4)])
    bad = TextBlock(text=text, text_raw=text, page=2, bbox=box, block_type=old_role)
    doc.pages[1].width = width
    doc.pages[1].blocks.append(bad)
    resolve_document_furniture(doc, {"method": "legacy"})
    assert bad.role_initial == old_role
    assert bad.role_final not in {"header", "footer", "running_header", "running_footer", "page_number"}
    assert bad.visibility == "visible" and bad.role_status == "NEEDS_REVIEW"
    assert bad.text_raw == bad.text == text


@pytest.mark.parametrize("role,y", [("header", 25), ("footer", 800)])
def test_verified_repeat_requires_distinct_pages_and_records_support(role, y):
    doc = make_doc([[block("Báo cáo thường niên 2025", n, y, block_type=role)] for n in range(1, 5)])
    resolve_document_furniture(doc, {"method": "legacy"})
    for page in doc.pages:
        b = page.blocks[0]
        assert b.block_type == f"running_{role}"
        assert b.role_status == "ACCEPTED" and b.visibility == "suppressed"
        assert len(b.support_page_ids) == 4 and b.template_id
    assert doc.manifest["furniture"]["quality_acceptance"] == "NOT_EVALUATED"


def test_odd_even_templates_and_page_size_groups():
    doc = make_doc([[block("Tên doanh nghiệp" if n % 2 else "Báo cáo thường niên", n)] for n in range(1, 7)])
    DocumentFurnitureResolver().resolve(doc)
    assert all(p.blocks[0].block_type == "running_header" for p in doc.pages)
    for page in doc.pages:
        assert len(page.blocks[0].support_page_ids) == 3
    assert {template["parity"] for template in doc.manifest["furniture"]["templates"]} == {0, 1}
    mixed = make_doc([[block("Nội dung giống nhau", n)] for n in range(1, 4)])
    mixed.pages[1].width = 1200
    DocumentFurnitureResolver().resolve(mixed)
    assert all(p.blocks[0].visibility == "visible" for p in mixed.pages)


@pytest.mark.parametrize("text,kind", [("• Đầu tư chuyển đổi số", "list"),
    ("Hình 1: Chuyển đổi số", "caption"), ("iii. Đổi mới sáng tạo", "section_header"),
    ("Đơn vị tính: triệu đồng", "paragraph"), ("Bảng 1 (tiếp theo)", "caption")])
def test_semantic_content_is_protected_even_when_repeated(text, kind):
    doc = make_doc([[block(text, n, block_type=kind)] for n in range(1, 5)])
    DocumentFurnitureResolver().resolve(doc)
    assert all(p.blocks[0].visibility == "visible" for p in doc.pages)


def test_selected_context_short_docs_different_years_and_recompute():
    doc = make_doc([[block("Báo cáo thường niên", n, block_type="header")] for n in range(1, 4)])
    resolver = DocumentFurnitureResolver()
    resolver.resolve(doc)
    assert doc.pages[0].blocks[0].visibility == "suppressed"
    doc.pages = doc.pages[:2]
    resolver.resolve(doc)
    assert all(p.blocks[0].visibility == "visible" for p in doc.pages)
    assert all(p.blocks[0].block_type == "paragraph" for p in doc.pages)
    assert doc.manifest["furniture"]["context_scope"] == "selected_pages"
    years = make_doc([[block(f"Báo cáo thường niên {2022+n}", n)] for n in range(1, 4)])
    resolver.resolve(years)
    assert all(p.blocks[0].visibility == "visible" for p in years.pages)


def test_unconfirmed_model_proposal_does_not_change_column_reading_order():
    blocks = [block("Left upper", 1, 100), block("Left lower", 1, 200), block("Right upper", 1, 100, block_type="header")]
    blocks[0].bbox = [40, 100, 260, 114]
    blocks[1].bbox = [40, 200, 260, 214]
    blocks[2].bbox = [320, 100, 550, 114]
    proposed = ReadingOrderSorter().sort_blocks(copy.deepcopy(blocks))
    ordinary = copy.deepcopy(blocks)
    ordinary[2].block_type = "paragraph"
    assert [b.text for b in proposed] == [b.text for b in ReadingOrderSorter().sort_blocks(ordinary)]


def test_repeated_model_header_in_body_is_not_suppressed():
    doc = make_doc([[block("Nội dung lặp ở giữa trang", n, 300, block_type="header")] for n in range(1, 5)])
    DocumentFurnitureResolver().resolve(doc)
    assert all(p.blocks[0].visibility == "visible" and p.blocks[0].role_status == "NEEDS_REVIEW" for p in doc.pages)


@pytest.mark.parametrize("initial", ["paragraph", "header", "page_number"])
def test_year_alone_is_not_pagination_even_with_model_proposal(initial):
    doc = make_doc([[block("2025", 1, 800, block_type=initial)]])
    DocumentFurnitureResolver().resolve(doc)
    assert doc.pages[0].blocks[0].block_type == "paragraph"
    assert doc.pages[0].blocks[0].visibility == "visible"


@pytest.mark.parametrize("change", ["font", "bold", "right_edge"])
def test_same_text_with_incompatible_style_or_width_is_not_template(change):
    doc = make_doc([[block("Tên báo cáo giống nhau", n, font_size=10)] for n in range(1, 4)])
    changed = doc.pages[1].blocks[0]
    if change == "font":
        changed.font_size = 13
    elif change == "bold":
        changed.is_bold = True
    else:
        changed.bbox[2] = 350
    DocumentFurnitureResolver().resolve(doc)
    assert all(p.blocks[0].visibility == "visible" for p in doc.pages)


@pytest.mark.parametrize("storage", ["legacy", "compact"])
def test_default_export_keeps_content_in_contract_and_emits_trace(tmp_path, storage):
    text = CASES[5][-1]
    doc = make_doc([[block(text, 1, 765, block_type="footer")]])
    out = ExportDocumentUseCase.execute(doc, tmp_path, assets_mode="none", output_mode=storage)
    for name in ("result.md", "document.md", "pages/page-0001.md"):
        assert text in (out/name).read_text(encoding="utf-8")
    document = json.loads((out/"document.json").read_text(encoding="utf-8"))
    consumed = {ref for chunk in document["chunks"] for span in chunk["spans"] for ref in span["block_ids"]}
    assert "0123456789abcdef:p0001:b0001" in consumed
    audit = json.loads((out/"furniture.json").read_text(encoding="utf-8"))
    item = audit["pages"][0]["items"][0]
    assert item["id"].endswith(":b0001") and item["role_initial"] == "footer"
    assert item["role_final"] == "paragraph" and item["role_reasons"]
    assert not (out/"layout_order.json").exists()  # No unrelated reading-order migration.


def test_invalid_settings_and_explicit_frozen_baseline():
    assert furniture_settings(document_layout_settings({}))["method"] == "document_context_v3"
    for settings in ({"method": "typo"}, {"min_pages": 2}, {"window": 2}, {"edge_ratio": .7},
                     {"support_ratio": float("nan")}, {"min_pages": True}, {"unknown": 3}):
        with pytest.raises(ConfigError):
            document_layout_settings({"document_layout": {"furniture": settings}})
    doc = make_doc([[block("Báo cáo 2025", n, block_type="header")] for n in range(1, 5)])
    resolve_document_furniture(doc, {"method": "legacy", "furniture": {"method": "legacy_v6_1"}})
    assert doc.manifest["furniture"]["method"] == "legacy_v6_1"


def test_reexport_old_document_pass_recomputes_furniture_version(tmp_path):
    from src.ocr.application.resolve_document_layout import resolve_document_layout
    text = "Một đoạn văn nội dung ở đầu trang."
    doc = make_doc([[block(text, 1, block_type="header")]])
    resolve_document_layout(doc)
    doc.manifest["furniture"]["method"] = "document_context_v2"
    doc.pages[0].blocks[0].block_type = "running_header"
    doc.pages[0].blocks[0].role_final = "running_header"
    doc.pages[0].blocks[0].visibility = "suppressed"
    out = ExportDocumentUseCase.execute(doc, tmp_path, assets_mode="none")
    assert doc.manifest["furniture"]["method"] == "document_context_v3"
    assert text in (out/"document.md").read_text(encoding="utf-8")
    assert (out/"furniture.json").exists()


def test_similar_ocr_token_matches_without_rewriting_raw_text():
    texts = ["BÁO CÁO THƯỜNG NIÊN DOANH NGHIỆP", "BÁO CÁO THƯƠNG NIÊN DOANH NGHIỆP", "BÁO CÁO THƯỜNG NIÊN DOANH NGHIỆP"]
    doc = make_doc([[block(text, n)] for n, text in enumerate(texts, 1)])
    DocumentFurnitureResolver().resolve(doc)
    assert all(p.blocks[0].block_type == "running_header" for p in doc.pages)
    assert [p.blocks[0].text for p in doc.pages] == texts
    assert "SIMILAR_TOKENS" in doc.pages[1].blocks[0].role_reasons


def test_inline_page_numbers_require_sequence_before_normalization():
    doc = make_doc([[block(f"Annual report 2025 | {n+10}", n)] for n in range(1, 5)])
    DocumentFurnitureResolver().resolve(doc)
    assert all(p.blocks[0].block_type == "running_header" for p in doc.pages)
    assert doc.manifest["furniture"]["page_number_sequences"][0]["printed_minus_pdf"] == 10
    constant = make_doc([[block("Annual report 2025 | 12", n)] for n in range(1, 4)])
    DocumentFurnitureResolver().resolve(constant)
    assert constant.manifest["furniture"]["page_number_sequences"] == []
    varied = make_doc([[block(f"Annual report 2025 | {n*10}", n)] for n in range(1, 4)])
    DocumentFurnitureResolver().resolve(varied)
    assert all(p.blocks[0].visibility == "visible" for p in varied.pages)


@pytest.mark.parametrize("text", ["IV", "DCL", "Trang 12/100", "12/100"])
def test_single_roman_or_number_is_insufficient_pagination_evidence(text):
    doc = make_doc([[block(text, 1, 800, block_type="page_number")]])
    DocumentFurnitureResolver().resolve(doc)
    assert doc.pages[0].blocks[0].block_type == "paragraph"
    assert doc.pages[0].blocks[0].role_status == "NEEDS_REVIEW"


def test_missing_pages_do_not_inflate_template_support():
    doc = make_doc([[block("A recurring report header", n)] for n in range(1, 4)])
    for page, number in zip(doc.pages, (1, 7, 13)):
        page.page_num = number
    doc.total_pages = 13
    DocumentFurnitureResolver().resolve(doc)
    assert all(p.blocks[0].visibility == "visible" for p in doc.pages)


def test_learned_boundary_does_not_hide_unmatched_body_in_the_same_band():
    doc = make_doc([[block("Recurring company report", n, 95),
                     block(f"Unique paragraph about operations number {n}.", n, 110)] for n in range(1, 4)])
    DocumentFurnitureResolver().resolve(doc)
    for page in doc.pages:
        assert page.blocks[0].visibility == "suppressed"
        assert page.blocks[1].visibility == "visible"
        region = page.metadata["furniture_regions"][0]
        assert region["boundary_y"] == 109
        assert region["policy"] == "only_matched_blocks_are_furniture"


def test_initial_twenty_page_model_and_explicit_local_adaptation():
    pages = []
    for n in range(1, 31):
        text = "Recurring initial report" if n <= 20 or n == 30 else "New appendix running header"
        pages.append([block(text, n)])
    doc = make_doc(pages)
    DocumentFurnitureResolver().resolve(doc)
    templates = doc.manifest["furniture"]["templates"]
    assert any(t["learning_scope"] == "initial_pages" for t in templates)
    assert any(t["learning_scope"] == "local_adaptation" for t in templates)
    assert doc.pages[-1].blocks[0].visibility == "suppressed"


@pytest.mark.parametrize("text", ["A. Chính sách hoạt động", "1.2 Chính sách hoạt động"])
def test_alphabetic_and_hierarchical_headings_are_protected(text):
    doc = make_doc([[block(text, n, block_type="section_header", is_bold=True)] for n in range(1, 5)])
    DocumentFurnitureResolver().resolve(doc)
    assert all(p.blocks[0].visibility == "visible" for p in doc.pages)

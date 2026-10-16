"""C2.4 section tree (heuristic-v1) and C3.7 document contracts on synthetic
blocks. These pin behaviour; heading accuracy needs the L4 ground truth."""
import copy
import json
import unicodedata

import pytest

from src.ocr.application.export_document import ExportDocumentUseCase
from src.ocr.domain.models.document import ExtractedDocument
from src.ocr.domain.models.evidence_block import EvidenceBlock
from src.ocr.domain.models.page import ExtractedPage
from src.ocr.domain.models.quality import DocumentQualityReport, PageQualityReport
from src.ocr.domain.models.region import BoundingBox, ContentType, Region
from src.ocr.domain.models.table import TableCell, TableStructure
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.routing.page_type import PageType
from src.ocr.infrastructure.export.artifact_validator import validate_document_artifacts
from src.ocr.infrastructure.export.document_contracts import (
    build_document_json, build_extraction_json, detect_language, render_document_md,
)
from src.ocr.infrastructure.export.contract_schema import contract_json_schemas, validate_contract_pair
from src.ocr.infrastructure.export.json_exporter import JSONExporter
from src.ocr.infrastructure.structure.ids import document_items, make_id
from src.ocr.infrastructure.structure.section_tree import (
    Line, assign_levels, build_structure, normalize_title, parse_numbering, rejection_reason, size_level,
)

BODY = ("Năm 2025 công ty tiếp tục mở rộng sản xuất và cải tiến quy trình quản trị "
        "nhằm nâng cao hiệu quả hoạt động kinh doanh của toàn hệ thống")


class Builder:
    """Lines placed top-down on consecutive y positions."""

    def __init__(self):
        self.pages = {}

    def add(self, text, page=1, size=10.0, bold=False, block_type="text", x0=50, gap=4.0, source="native",
            quality=1.0):
        blocks = self.pages.setdefault(page, [])
        y = blocks[-1].bbox[3] + gap if blocks else 60.0
        blocks.append(TextBlock(text=text, page=page, bbox=[x0, y, x0 + min(480, 5 * len(text)), y + size * 1.2],
                                font_size=size if source == "native" else None, is_bold=bold,
                                block_type=block_type, source=source, confidence=quality, quality_score=quality))
        return self

    def body(self, page=1, lines=3):
        for _ in range(lines):
            self.add(BODY, page=page)
        return self

    def doc(self, tables=None, regions=None):
        pages = []
        for number in sorted(self.pages):
            blocks = self.pages[number]
            page_regions = list((regions or {}).get(number, [])) or [
                Region(region_id=f"r{number}", bbox=BoundingBox(x0=0, y0=0, x1=595, y1=842),
                       content_type=ContentType.TEXT, status="EXTRACTED")]
            evidence = [EvidenceBlock(block_id=f"b{number}_{i}", page=number, region_id=page_regions[0].region_id,
                                      bbox=b.bbox, orientation=0, text=b.text, source="native")
                        for i, b in enumerate(blocks)]
            pages.append(ExtractedPage(page_num=number, width=595, height=842, page_type=PageType.NATIVE_TEXT,
                                       blocks=blocks, tables=(tables or {}).get(number, []), regions=page_regions,
                                       evidence_blocks=evidence,
                                       quality=PageQualityReport(page=number, page_type="NATIVE_TEXT")))
        selected = [p.page_num for p in pages]
        return ExtractedDocument(document_id="doc", file_path="unused.pdf", file_hash="0123456789abcdef",
                                 total_pages=len(pages), selected_pages=selected, pages=pages,
                                 manifest={"selected_pages": selected, "device": "cpu"},
                                 quality=DocumentQualityReport(document_id="doc", total_pages=len(pages)))


def structure_of(doc):
    return build_structure(document_items(doc))


def headings(structure):
    return [(s.heading.level, " ".join(line.text for line in s.heading.lines)) for s in structure.sections]


def test_numbering_patterns():
    assert parse_numbering("I. THÔNG TIN CHUNG") == ("roman", (1,))
    assert parse_numbering("IV - RỦI RO") == ("roman", (4,))
    assert parse_numbering("2.1. Cơ cấu cổ đông") == ("arabic", (2, 1))
    assert parse_numbering("01. THÔNG TIN CHUNG") == ("arabic", (1,))
    assert parse_numbering("PHẦN II TÀI CHÍNH")[0] == "keyword"
    assert parse_numbering("2020 Doanh thu tăng") is None
    assert parse_numbering("1.234.567 đồng") is None


def _line(text, block_type="text"):
    return Line("x:p0001:b0001", 1, text, [0, 0, 10, 10], 10.0, False, block_type, "native")


@pytest.mark.parametrize("text", ["1.234.567 đồng", "12,5%", "(1.234)", "31/12/2025", "Trang 12", "- Doanh thu",
                                  "Địa chỉ:", "Ngày 5 tháng 3 năm 2021"])
def test_amounts_dates_bullets_and_labels_are_rejected(text):
    assert rejection_reason(_line(text)) is not None


def test_person_name_after_job_title_is_rejected():
    assert rejection_reason(_line("NGUYỄN VĂN AN"), [_line("Chủ tịch HĐQT")]) == "person_name"
    assert rejection_reason(_line("NGUYỄN VĂN AN"), [_line("Chủ tịch HĐQT"), _line("CÔNG TY")]) == "person_name"
    assert rejection_reason(_line("NGUYỄN VĂN AN"), [_line("Báo cáo năm")]) is None


def test_size_bold_upper_headings_are_found_and_body_is_not():
    doc = (Builder().add("THÔNG TIN CHUNG", size=18, bold=True).body()
           .add("Cơ cấu cổ đông", size=13, bold=True).body()
           .add("CHỈ TIÊU", bold=True).body().doc())
    found = headings(structure_of(doc))
    assert found == [(1, "THÔNG TIN CHUNG"), (2, "Cơ cấu cổ đông")]  # bold body-size label is not a heading


def test_block_type_alone_never_makes_a_heading():
    doc = Builder().add("Kết quả kinh doanh được trình bày dưới đây", block_type="title").body().doc()
    assert structure_of(doc).sections == []


def test_running_headers_page_numbers_and_footers_are_excluded():
    builder = Builder()
    for page in (1, 2):
        builder.add("BÁO CÁO THƯỜNG NIÊN 2025", page=page, size=20, bold=True, block_type="running_header")
        builder.body(page=page)
        builder.add("12", page=page, block_type="page_number")
    structure = structure_of(builder.doc())
    assert structure.sections == []
    assert set(structure.dropped.values()) == {"running_header", "page_number"}


def test_broken_heading_lines_merge_even_when_boxes_overlap():
    doc = (Builder().add("TÌNH HÌNH HOẠT ĐỘNG SẢN XUẤT KINH", size=16)
           .add("DOANH", size=16, gap=-4).body().doc())
    assert headings(structure_of(doc)) == [(1, "TÌNH HÌNH HOẠT ĐỘNG SẢN XUẤT KINH DOANH")]


def test_new_numbering_starts_a_new_heading():
    doc = Builder().add("1. TỔNG QUAN", size=14).add("2. RỦI RO", size=14).body().doc()
    assert [title for _, title in headings(structure_of(doc))] == ["1. TỔNG QUAN", "2. RỦI RO"]


def test_english_translation_line_becomes_title_en():
    doc = Builder().add("THÔNG TIN CHUNG", size=16).add("GENERAL INFORMATION", size=12).body().doc()
    section = structure_of(doc).sections[0]
    assert section.heading.title_en == "GENERAL INFORMATION"
    md = render_document_md(doc, structure_of(doc), "png")
    assert "# THÔNG TIN CHUNG\n\n*GENERAL INFORMATION*" in md


def test_repeated_running_section_title_is_not_a_new_section_in_legacy_mode():
    builder = Builder()
    for page in (1, 2, 3):
        builder.add("TỔ CHỨC VÀ NHÂN SỰ", page=page, size=16).body(page=page)
    structure = structure_of(builder.doc())
    assert len(structure.sections) == 1
    assert list(structure.dropped.values()).count("repeated_heading") == 2


def test_levels_from_size_bands_and_numbering_depth_without_skipping():
    assert [size_level(r) for r in (3.0, 2.0, 1.3, 1.1, None)] == [1, 2, 3, 4, None]
    doc = (Builder().add("PHẦN I TỔNG QUAN", size=22).body()
           .add("1.1.2 Chi tiết", size=10, bold=True).body().doc())
    # Depth-3 numbering would be level 3, but cannot skip below level 2.
    assert [level for level, _ in headings(structure_of(doc))] == [1, 2]


def test_text_heavy_large_font_page_is_measured_against_its_own_body():
    builder = Builder().body(page=1, lines=8)  # 10pt document body
    for _ in range(4):
        builder.add(BODY, page=2, size=14)  # a 14pt cover letter
    builder.add("Kính gửi Ủy ban Chứng khoán", page=2, size=14)
    assert structure_of(builder.doc()).sections == []


def test_paragraphs_join_lines_and_continue_across_pages():
    builder = (Builder().add("Doanh thu năm nay tăng mạnh nhờ mở rộng thị trường", page=1)
               .add("xuất khẩu và cải thiện biên lợi nhuận gộp của", page=1))
    builder.add("các dòng sản phẩm chủ lực trong năm.", page=2)
    builder.add("Kế hoạch năm sau tập trung vào chuyển đổi số.", page=2, gap=20)
    structure = structure_of(builder.doc())
    paragraphs = [value for kind, value in structure.flow if kind == "paragraph"]
    assert len(paragraphs) == 2
    assert paragraphs[0].text.endswith("của các dòng sản phẩm chủ lực trong năm.")
    assert {line.page for line in paragraphs[0].lines} == {1, 2}


def test_lone_bullet_glyph_prefixes_next_line_and_glyph_only_is_dropped():
    structure = structure_of(Builder().add("-").add("Tăng cường quản trị rủi ro").add("•", gap=30).doc())
    paragraphs = [value for kind, value in structure.flow if kind == "paragraph"]
    assert [(p.kind, p.text) for p in paragraphs] == [("list_item", "- Tăng cường quản trị rủi ro")]
    assert "decoration" in structure.dropped.values()


def test_toc_with_page_numbers_confirms_headings_and_estimates_offset():
    builder = Builder().add("MỤC LỤC", page=1, size=16)
    titles = ["THÔNG TIN CHUNG", "TÌNH HÌNH HOẠT ĐỘNG", "BÁO CÁO TÀI CHÍNH", "QUẢN TRỊ CÔNG TY"]
    for number, title in enumerate(titles, start=3):
        builder.add(f"{title} .......... {number}", page=1)
    for page, title in enumerate(titles, start=4):  # printed page = PDF page - 1
        builder.add(title, page=page, size=16).body(page=page)
    structure = structure_of(builder.doc())
    assert structure.toc is not None and structure.toc.pages == [1]
    assert structure.toc.page_offset == 1
    assert all(s.heading.toc_confirmed for s in structure.sections)
    assert "MỤC LỤC" not in [s.heading.text for s in structure.sections]


def test_ids_are_stable_per_kind_per_page():
    doc = Builder().add("A heading", size=16).body(lines=2).doc()
    items = document_items(doc)[0]
    assert [item.id for item in items] == [make_id("0123456789abcdef", 1, "b", n) for n in (1, 2, 3)]
    assert document_items(doc)[0] == items


def _table(page=1):
    cells = [TableCell(row_idx=0, col_idx=0, text="Chỉ tiêu", bbox=[50, 300, 150, 320]),
             TableCell(row_idx=0, col_idx=1, text="2025", bbox=[150, 300, 250, 320]),
             TableCell(row_idx=1, col_idx=0, text="Doanh thu", bbox=[50, 320, 150, 340]),
             TableCell(row_idx=1, col_idx=1, text="651.761", bbox=[150, 320, 250, 340])]
    return TableStructure(table_id="p1_t1", page=page, bbox=[50, 300, 250, 340], num_rows=2, num_cols=2,
                          cells=cells, confidence=None, confidence_source="unavailable", engine="docling_tableformer",
                          needs_review=True)


def test_document_json_references_only_extraction_ids_and_null_confidence_survives():
    doc = Builder().add("KẾT QUẢ KINH DOANH", size=16).body().doc(tables={1: [_table()]})
    items = document_items(doc)
    extraction = build_extraction_json(doc, items, "cpu")
    known = {item["id"] for page in extraction["pages"] for item in page["items"]}
    document = build_document_json(doc, build_structure(items), "cpu")
    referenced = {ref for chunk in document["chunks"] for span in chunk["spans"] for ref in span["block_ids"]}
    assert referenced <= known
    table = document["tables"][0]
    assert table["table_id"] in known and document["chunks"][-1]["confidence"] is None
    assert all(chunk["section_id"] in {s["section_id"] for s in document["sections"]} for chunk in document["chunks"])
    assert next(i for i in extraction["pages"][0]["items"] if i["kind"] == "t")["confidence"] is None


def test_document_md_levels_tables_and_no_html():
    doc = (Builder().add("THÔNG TIN CHUNG", size=18).body()
           .add("Cơ cấu cổ đông", size=13, bold=True).body().doc(tables={1: [_table()]}))
    md = render_document_md(doc, structure_of(doc), "png")
    assert md.startswith("# THÔNG TIN CHUNG\n") and "\n## Cơ cấu cổ đông\n" in md
    assert "| Doanh thu | 651.761 |" in md.replace("  ", " ")
    assert "<table" not in md and "Trang" not in md


def test_document_md_images_follow_assets_mode():
    region = Region(region_id="p1_img", bbox=BoundingBox(x0=50, y0=400, x1=300, y1=700),
                    content_type=ContentType.IMAGE, status="PRESERVED")
    text = Region(region_id="r1", bbox=BoundingBox(x0=0, y0=0, x1=595, y1=300),
                  content_type=ContentType.TEXT, status="EXTRACTED")
    doc = Builder().body().doc(regions={1: [text, region]})
    assert "![Embedded image](images/page_01_p1_img.png)" in render_document_md(doc, structure_of(doc), "png")
    assert "<!-- image omitted: p1_img -->" in render_document_md(doc, structure_of(doc), "none")


def test_language_tags():
    assert detect_language("Doanh thu tăng trưởng mạnh") == "vi"
    assert detect_language("Revenue grew strongly") == "en"
    assert detect_language("12.345") == "unknown"


def test_export_writes_contracts_and_validator_catches_dangling_ids(tmp_path):
    doc = Builder().add("THÔNG TIN CHUNG", size=18).body().doc(tables={1: [_table()]})
    out = ExportDocumentUseCase.execute(doc, tmp_path, assets_mode="none")
    for name in ("extraction.json", "structure.json", "document.json", "document.md"):
        assert (out / name).is_file(), name
    report = validate_document_artifacts(out)
    assert report["status"] != "FAIL", report["errors"]
    assert report["counts"]["structure_sections"] == 1
    document = json.loads((out / "document.json").read_text(encoding="utf-8"))
    document["chunks"][0]["spans"][0]["block_ids"].append("0123456789abcdef:p0009:b0001")
    (out / "document.json").write_text(json.dumps(document), encoding="utf-8")
    assert any("unknown id" in e for e in validate_document_artifacts(out)["errors"])


def test_structure_failure_preserves_evidence_but_fails_complete_v6_export(tmp_path, monkeypatch):
    def broken(_items, **_settings):
        raise RuntimeError("boom")

    monkeypatch.setattr("src.ocr.infrastructure.export.document_contracts.build_structure", broken)
    with pytest.raises(ValueError, match="Artifact validation failed"):
        ExportDocumentUseCase.execute(Builder().body().doc(), tmp_path)
    out = tmp_path / "doc"
    assert json.loads((out / "structure.json").read_text(encoding="utf-8"))["error"] == "RuntimeError: boom"
    assert (out / "extraction.json").is_file() and not (out / "document.json").exists()
    assert validate_document_artifacts(out)["status"] == "FAIL"


def test_normalized_toc_titles_strip_roman_and_letter_numbering_before_casefold():
    assert normalize_title("I. THÔNG TIN CHUNG") == normalize_title("THÔNG TIN CHUNG")
    assert normalize_title("A. CƠ CẤU CỔ ĐÔNG") == normalize_title("CƠ CẤU CỔ ĐÔNG")


@pytest.mark.parametrize("source", ["native", "primary_ocr"])
def test_numbered_siblings_do_not_descend_merely_due_to_missing_font(source):
    builder = Builder().add("I. TỔNG QUAN", source=source).body()
    builder.add("II. RỦI RO", source=source).body()
    builder.add("III. QUẢN TRỊ", source=source).body()
    assert [level for level, _ in headings(structure_of(builder.doc()))] == [1, 1, 1]


def test_arabic_numbering_preserves_siblings_and_parent_depth():
    builder = Builder().add("PHẦN I TỔNG QUAN", source="primary_ocr").body()
    for title in ("1. HOẠT ĐỘNG", "1.1 CHI TIẾT", "1.2 KẾ HOẠCH", "2. RỦI RO"):
        builder.add(title, source="primary_ocr").body()
    structure = structure_of(builder.doc())
    assert [s.heading.level for s in structure.sections] == [1, 2, 3, 3, 2]
    assert [s.parent for s in structure.sections] == [None, 1, 2, 2, 1]


def test_keyword_numbering_preserves_part_chapter_section_hierarchy_without_fonts():
    builder = Builder()
    for title in ("PHẦN I TỔNG QUAN", "CHƯƠNG I HOẠT ĐỘNG", "MỤC 1 KẾT QUẢ", "CHƯƠNG II RỦI RO"):
        builder.add(title, source="primary_ocr").body()
    structure = structure_of(builder.doc())
    assert [s.heading.level for s in structure.sections] == [1, 2, 3, 2]
    assert [s.parent for s in structure.sections] == [None, 1, 2, 1]


def test_repeated_title_with_different_numbering_is_a_real_section():
    doc = (Builder().add("1. KẾT QUẢ", page=1, size=16).body(page=1)
           .add("2. KẾT QUẢ", page=2, size=16).body(page=2).doc())
    assert len(structure_of(doc).sections) == 2


def test_paragraphs_do_not_join_over_unselected_pages():
    doc = (Builder().add("Kết quả kinh doanh tiếp tục tăng nhờ", page=1)
           .add("các sản phẩm mới được đưa ra thị trường.", page=3).doc())
    paragraphs = [v for k, v in structure_of(doc).flow if k == "paragraph"]
    assert len(paragraphs) == 2
    assert [[line.page for line in p.lines] for p in paragraphs] == [[1], [3]]


def test_toc_suppresses_index_rows_and_retains_introduction_on_same_page():
    builder = Builder().add("MỤC LỤC", size=16)
    for number, title in enumerate(("THÔNG TIN CHUNG", "HOẠT ĐỘNG", "RỦI RO", "QUẢN TRỊ"), 3):
        builder.add(f"{title} .......... {number}")
    builder.add(BODY, gap=20)
    structure = structure_of(builder.doc())
    assert structure.toc is not None
    assert structure.sections == []
    assert [v.text for k, v in structure.flow if k == "paragraph"] == [BODY]


def test_document_index_labels_recognize_toc_with_separate_page_number_boxes():
    builder = Builder()
    for number, title in enumerate(("THÔNG TIN CHUNG", "HOẠT ĐỘNG", "RỦI RO", "QUẢN TRỊ"), 3):
        builder.add(title, block_type="Document-index", size=14)
        title_block = builder.pages[1][-1]
        builder.add(str(number), x0=500)
        builder.pages[1][-1].bbox = [500, title_block.bbox[1], 520, title_block.bbox[3]]
    structure = structure_of(builder.doc())
    assert structure.toc is not None and len(structure.toc.entries) == 4
    assert structure.sections == [] and structure.flow == []


def test_line_overlapping_table_center_is_not_discarded_when_most_text_is_outside():
    doc = Builder().add("Thuyết minh chung cho bảng và hoạt động của công ty").doc(tables={1: [_table()]})
    doc.pages[0].blocks[0].bbox = [0, 310, 500, 325]
    structure = structure_of(doc)
    assert any(k == "paragraph" and v.text.startswith("Thuyết minh") for k, v in structure.flow)


def _contracts(doc):
    items = document_items(doc)
    return build_extraction_json(doc, items, "cpu"), build_document_json(doc, build_structure(items), "cpu")


def test_all_extraction_kinds_and_table_cells_have_traceable_provenance():
    from src.ocr.domain.models.formula import Formula

    image = Region(region_id="image", bbox=BoundingBox(x0=50, y0=600, x1=300, y1=800),
                   content_type=ContentType.IMAGE, status="PRESERVED")
    doc = Builder().add("THÔNG TIN CHUNG", size=18).body().doc(tables={1: [_table()]}, regions={1: [image]})
    doc.pages[0].formulas = [Formula(formula_id="f", page=1, latex="E=mc^2", bbox=[50, 400, 200, 420])]
    extraction, document = _contracts(doc)
    required = {"text_raw", "text_corrected", "correction_source", "source", "device", "confidence", "confidence_source"}
    items = extraction["pages"][0]["items"]
    assert {item["kind"] for item in items} == {"b", "t", "f", "i"}
    assert all(required <= item.keys() for item in items)
    table = document["tables"][0]
    assert all(cell["spans"][0]["block_ids"] == [table["table_id"]] for cell in table["cells"])
    assert document["sections"][0]["spans"][0]["block_ids"] == document["sections"][0]["heading_item_ids"]
    assert validate_contract_pair(extraction, document) == []


def test_unknown_constituent_confidence_keeps_paragraph_unscored():
    doc = Builder().add("Doanh thu công ty tăng nhờ mở rộng thị trường").add("xuất khẩu trong năm.").doc()
    doc.pages[0].blocks[1].confidence = None
    doc.pages[0].blocks[1].confidence_source = "unavailable"
    extraction, document = _contracts(doc)
    assert len(document["chunks"]) == 1
    assert document["chunks"][0]["confidence"] is None
    assert document["chunks"][0]["confidence_source"] == "unavailable"
    assert validate_contract_pair(extraction, document) == []


def test_line_wrap_dehyphenation_retains_raw_text_and_records_source():
    doc = Builder().add("Kế hoạch phát triển inter-").add("national trong năm tới.").doc()
    extraction, document = _contracts(doc)
    chunk = document["chunks"][0]
    assert "inter- national" in chunk["text_raw"]
    assert "international" in chunk["text_corrected"]
    assert chunk["correction_source"] == "line_wrap_dehyphenation" and chunk["needs_review"]
    assert extraction["pages"][0]["items"][0]["text_raw"].endswith("inter-")
    assert validate_contract_pair(extraction, document) == []


def test_numeric_dash_at_line_end_is_never_removed():
    doc = Builder().add("Khoản điều chỉnh được ghi nhận là 123-").add("theo thông báo trong kỳ.").doc()
    _, document = _contracts(doc)
    assert "123- theo" in document["chunks"][0]["text_corrected"]
    assert document["chunks"][0]["correction_source"] is None


@pytest.mark.parametrize("text", ["-1.234 triệu đồng", "+12,5%", "- 1.234 triệu đồng"])
def test_numeric_signs_are_retained_and_do_not_become_list_markers(text):
    doc = Builder().add(text).doc()
    structure = structure_of(doc)
    assert [(k, v.kind) for k, v in structure.flow] == [("paragraph", "paragraph")]
    _, document = _contracts(doc)
    assert document["chunks"][0]["text_raw"] == document["chunks"][0]["text_corrected"] == text
    markdown = render_document_md(doc, structure, "none")
    assert markdown.strip() == ("\\" + text if text.startswith("- ") else text)


def test_list_rendering_removes_only_the_marker_and_keeps_negative_value():
    doc = Builder().add("- Mức điều chỉnh: -1.234 triệu đồng").doc()
    assert render_document_md(doc, structure_of(doc), "none").strip() == "- Mức điều chỉnh: -1.234 triệu đồng"


def test_raw_block_text_and_unicode_normalization_source_survive_paragraph_export():
    doc = Builder().add("Công ty phát triển theo kế hoạch mới.").doc()
    block = doc.pages[0].blocks[0]
    block.text_raw = unicodedata.normalize("NFD", block.text)
    block.correction_source = "unicode_nfc"
    extraction, document = _contracts(doc)
    assert extraction["pages"][0]["items"][0]["text_raw"] == block.text_raw
    chunk = document["chunks"][0]
    assert chunk["text_raw"] == block.text_raw
    assert chunk["text_corrected"] == block.text and chunk["correction_source"] == "unicode_nfc"
    assert not chunk["needs_review"]
    assert validate_contract_pair(extraction, document) == []


def test_page_json_roundtrip_preserves_native_structure_features_and_ids():
    doc = (Builder().add("THÔNG TIN CHUNG", size=18, bold=True).body()
           .add("Cơ cấu cổ đông", size=13, bold=True).body().doc())
    doc.pages[0].blocks[0].line_index = 3
    doc.pages[0].blocks[0].column_index = 1
    restored = doc.model_copy(update={"pages": [ExtractedPage.model_validate(JSONExporter.export_page_json(p))
                                               for p in doc.pages]})
    assert restored.pages[0].blocks[0].line_index == 3 and restored.pages[0].blocks[0].column_index == 1
    assert headings(structure_of(restored)) == headings(structure_of(doc))
    assert _contracts(restored) == _contracts(doc)


def test_missing_cell_bbox_declares_table_fallback_and_merged_cells_are_engine_independent():
    table = _table()
    table.engine = "heuristic"
    table.cells[0].bbox = []
    table.cells[0].col_span = 2
    extraction, document = _contracts(Builder().body().doc(tables={1: [table]}))
    exported = document["tables"][0]
    assert exported["has_merged_cells"] is True
    assert exported["cells"][0]["bbox_source"] == "table_fallback"
    assert exported["cells"][0]["spans"][0]["bbox"] == table.bbox
    assert validate_contract_pair(extraction, document) == []


def test_contract_json_schemas_publish_required_fields_and_nullable_confidence():
    schemas = contract_json_schemas()
    assert set(schemas) == {"extraction-v1", "doc-v1", "structure-v1"}
    assert all(schema["$schema"].endswith("2020-12/schema") for schema in schemas.values())
    chunk = schemas["doc-v1"]["$defs"]["Chunk"]
    assert {"text_raw", "text_corrected", "spans", "confidence", "confidence_source"} <= set(chunk["required"])
    assert {variant["type"] for variant in chunk["properties"]["confidence"]["anyOf"]} == {"number", "null"}


@pytest.mark.parametrize("mutation, expected", [
    (lambda e, d: d["chunks"][0]["spans"][0]["block_ids"].clear(), "at least 1"),
    (lambda e, d: d["chunks"][0]["spans"][0].update(page=2), "unknown page"),
    (lambda e, d: d["tables"][0]["cells"][0]["spans"][0]["block_ids"].append("bad:p0001:t0001"), "unknown id"),
    (lambda e, d: d["sections"][0].update(parent_id=d["sections"][0]["section_id"]), "invalid parent"),
    (lambda e, d: d["sections"][0]["chunk_ids"].clear(), "outside the section tree"),
    (lambda e, d: d["chunks"][0].update(confidence=None, confidence_source="recognizer"), "null confidence"),
    (lambda e, d: d["chunks"][0].update(text_corrected="changed"), "changed text"),
    (lambda e, d: d["chunks"][0]["spans"][0].update(bbox=[3, 2, 1, 4]), "reversed coordinates"),
    (lambda e, d: e["pages"][0]["items"][0].update(bbox=[0, 0, float("nan"), 4]), "finite number"),
    (lambda e, d: d["tables"][0]["cells"][0].update(row_span=100), "outside table dimensions"),
    (lambda e, d: d["tables"][0].update(pages=[2]), "pages/type differ"),
    (lambda e, d: d["chunks"][0].update(type="table"), "wrong element kind"),
])
def test_contract_validation_rejects_broken_provenance_tree_and_geometry(mutation, expected):
    extraction, document = _contracts(Builder().add("THÔNG TIN CHUNG", size=18).body().doc(tables={1: [_table()]}))
    extraction, document = copy.deepcopy(extraction), copy.deepcopy(document)
    mutation(extraction, document)
    assert any(expected in error for error in validate_contract_pair(extraction, document))


def test_same_title_in_the_middle_of_another_page_is_preserved_as_a_section():
    doc = (Builder().add("KẾT QUẢ KINH DOANH", page=1, size=18).body(page=1)
           .add("KẾT QUẢ KINH DOANH", page=2, size=18).body(page=2).doc())
    for block in doc.pages[1].blocks:
        block.bbox[1] += 340
        block.bbox[3] += 340
    structure = structure_of(doc)
    assert len(structure.sections) == 2
    assert "repeated_heading" not in structure.dropped.values()


@pytest.mark.parametrize("cells", [[], [TableCell(row_idx=0, col_idx=0, text="Different text", bbox=[50, 300, 250, 340])]])
def test_text_over_empty_or_unmatched_table_is_retained_and_requires_review(cells):
    table = _table()
    table.cells = cells
    doc = Builder().add("Doanh thu thực tế tăng trong kỳ.").doc(tables={1: [table]})
    doc.pages[0].blocks[0].bbox = [60, 305, 220, 320]
    extraction, document = _contracts(doc)
    paragraph = next(chunk for chunk in document["chunks"] if chunk["type"] == "paragraph")
    assert paragraph["text_corrected"] == doc.pages[0].blocks[0].text
    assert paragraph["needs_review"]
    assert validate_contract_pair(extraction, document) == []


def test_table_text_is_suppressed_only_when_cell_content_covers_it():
    doc = Builder().add("Doanh thu").doc(tables={1: [_table()]})
    doc.pages[0].blocks[0].bbox = [55, 321, 145, 335]
    structure = structure_of(doc)
    assert structure.dropped[make_id(doc.file_hash, 1, "b", 1)] == "table_cell"


def test_document_cell_cannot_reference_another_table_on_the_same_page():
    second = _table()
    second.bbox = [50, 500, 250, 540]
    doc = Builder().body().doc(tables={1: [_table(), second]})
    extraction, document = _contracts(doc)
    document["tables"][0]["cells"][0]["spans"][0]["block_ids"] = [document["tables"][1]["table_id"]]
    assert any("owning table" in error for error in validate_contract_pair(extraction, document))


@pytest.mark.parametrize("sentence", ["The company grew revenue during the year.", "The company grew revenue during the year"])
def test_english_body_after_vietnamese_heading_is_not_taken_as_translation(sentence):
    doc = Builder().add("KẾT QUẢ KINH DOANH", size=18).add(sentence, size=10).body().doc()
    structure = structure_of(doc)
    assert structure.sections[0].heading.title_en is None
    _, document = _contracts(doc)
    assert any(sentence in chunk["text_corrected"] for chunk in document["chunks"])


def test_english_heading_candidate_still_attaches_as_translation():
    doc = Builder().add("THÔNG TIN CHUNG", size=18).add("GENERAL INFORMATION", size=14).body().doc()
    structure = structure_of(doc)
    assert len(structure.sections) == 1 and structure.sections[0].heading.title_en == "GENERAL INFORMATION"


def test_running_title_suppression_requires_matching_font_style():
    doc = (Builder().add("KẾT QUẢ KINH DOANH", page=1, size=18).body(page=1)
           .add("KẾT QUẢ KINH DOANH", page=2, size=24).body(page=2).doc())
    assert len(structure_of(doc).sections) == 2


def test_table_matching_preserves_difference_in_numeric_punctuation():
    doc = Builder().add("651761").doc(tables={1: [_table()]})
    doc.pages[0].blocks[0].bbox = [160, 321, 240, 335]
    _, document = _contracts(doc)
    paragraph = next(chunk for chunk in document["chunks"] if chunk["type"] == "paragraph")
    assert paragraph["text_corrected"] == "651761" and paragraph["needs_review"]

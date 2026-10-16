"""Regression probes for the seven V6.2 review findings."""
import copy
import json
from unittest.mock import Mock

import pytest
import pymupdf
import yaml

from src.ocr.application.convert_document import ConvertDocumentUseCase
from src.ocr.application.export_document import ExportDocumentUseCase
from src.ocr.application.process_page import ProcessPageUseCase
from src.ocr.application.resolve_document_layout import resolve_document_layout
from src.ocr.domain.models.diagram import DiagramNode, DiagramStructure
from src.ocr.domain.models.document import ExtractedDocument
from src.ocr.domain.models.evidence_block import EvidenceBlock
from src.ocr.domain.models.formula import Formula
from src.ocr.domain.models.page import ExtractedPage
from src.ocr.domain.models.quality import DocumentQualityReport, PageQualityReport
from src.ocr.domain.models.region import Region, BoundingBox, ContentType
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.routing.page_type import PageType
from src.ocr.infrastructure.export.artifact_validator import validate_document_artifacts
from src.ocr.infrastructure.export.contract_schema import validate_extraction, validate_contract_set
from src.ocr.infrastructure.export.document_contracts import build_extraction_json, build_document_json, build_structure_json, render_document_md
from src.ocr.infrastructure.export.markdown_exporter import MarkdownExporter
from src.ocr.infrastructure.export.result_reader import load_document_json
from src.ocr.infrastructure.layout.region_tree import LayoutItem, infer_region_tree
from src.ocr.infrastructure.layout.dla_engine import DLAEngine
from src.ocr.infrastructure.postprocessing.reading_order import ReadingOrderSorter
from src.ocr.infrastructure.structure.ids import document_items
from src.ocr.infrastructure.structure.section_tree import build_structure
from src.ocr.infrastructure.structure.source_identity import lock_text_evidence
from src.ocr.runtime.config import load_profiles, document_layout_settings, ConfigError

NEW = "region_tree_graph_v1"


def b(text, box, page=1, **kwargs):
    return TextBlock(text=text, bbox=box, page=page, source="native", confidence=1., quality_score=1., **kwargs)


def make_doc(blocks_by_page):
    pages = []
    for n, blocks in enumerate(blocks_by_page, 1):
        region = Region(region_id=f"r{n}", bbox=BoundingBox(x0=0, y0=0, x1=600, y1=842),
                        content_type=ContentType.TEXT, status="EXTRACTED", orientation_status="CONFIDENT")
        for block in blocks:
            block.region_id = region.region_id
        evidence = [EvidenceBlock(page=n, block_id=f"p{n}b{i}", text=block.text, bbox=block.bbox,
                                  region_id=region.region_id, orientation=0, source="native") for i, block in enumerate(blocks)]
        pages.append(ExtractedPage(page_num=n, width=600, height=842, blocks=blocks, evidence_blocks=evidence,
                                  regions=[region], page_type=PageType.NATIVE_TEXT,
                                  quality=PageQualityReport(page=n, page_type="NATIVE_TEXT")))
    return ExtractedDocument(document_id="review-fixes", file_path="unused.pdf", file_hash="0123456789abcdef",
                             total_pages=len(pages), selected_pages=list(range(1, len(pages)+1)), pages=pages,
                             manifest={"selected_pages": list(range(1, len(pages)+1)),
                                       "document_id": "review-fixes", "file_hash": "0123456789abcdef"},
                             quality=DocumentQualityReport(document_id="review-fixes", total_pages=len(pages)))


@pytest.mark.parametrize("profile", ["balanced", "accuracy"])
@pytest.mark.parametrize("storage_mode", ["legacy", "compact"])
def test_docling_formula_sources_are_owned_and_rendered_once(tmp_path, profile, storage_mode):
    raw = "ROE = LN / VCSH"
    latex = r"ROE = \frac{LN}{VCSH}"
    doc = make_doc([[b(raw, [50, 200, 450, 230]), b("(1)", [460, 205, 490, 220])]])
    page = doc.pages[0]
    lock_text_evidence(page.blocks, doc.file_hash, 1, page.metadata)
    source_id = page.blocks[0].source_id
    formula = Formula(formula_id="f1", page=1, latex=latex, bbox=[45, 195, 455, 235])
    options = copy.deepcopy(load_profiles()[1][profile]["docling"])
    processor = object.__new__(ProcessPageUseCase)
    processor.structure_extractor = Mock(enabled=True, options=options)
    processor.structure_extractor.cache_fingerprint.return_value = "formula-fixture"
    processor.structure_extractor.extract_page.return_value = {"tables": [], "formulas": [formula], "metadata": {"status": "success"}}
    page.blocks, page.tables, page.formulas = processor._enrich_structures(
        Mock(), 1, PageType.MIXED, page.blocks, [], page.regions, page.metadata)
    page.evidence_blocks = [EvidenceBlock(page=1, block_id=f"e{i}", text=block.text, bbox=block.bbox,
        region_id=block.region_id or "r1", orientation=0, source="ocr") for i, block in enumerate(page.blocks)]
    resolve_document_layout(doc)
    assert page.metadata["source_dispositions"][source_id]["status"] == "REPLACED_BY_FORMULA"
    assert not any("TABLE_SOURCE_UNMAPPED" in block.review_reason_codes for block in page.blocks)
    out = ExportDocumentUseCase.execute(doc, tmp_path, assets_mode="none", output_mode=storage_mode)
    for name in ("document.md", "result.md", "pages/page-0001.md"):
        text = (out / name).read_text(encoding="utf-8")
        assert raw not in text and text.count(latex) == 1
        assert "(1)" in text
    source = json.loads((out / "extraction.json").read_text(encoding="utf-8"))
    assert validate_extraction(source) == []
    assert source["pages"][0]["text_evidence"]["items"][0]["text"] == raw
    assert next(i for i in source["pages"][0]["items"] if i["kind"] == "f")["derived_from_source_ids"] == [source_id]
    assert load_document_json(out / "result.json")["pages"][0]["formulas"][0]["latex"] == latex
    source["pages"][0]["source_dispositions"].clear()
    assert any("disposition" in error for error in validate_extraction(source))


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("storage_mode", ["legacy", "compact"])
def test_export_switch_versions_sidecars_and_mermaid(tmp_path, modern, storage_mode):
    doc = make_doc([[b("SƠ ĐỒ TỔ CHỨC", [40, 200, 400, 220]), b("Chữ nguồn vẫn được giữ.", [40, 240, 400, 260])]])
    doc.pages[0].diagrams = [DiagramStructure(diagram_id="org", title="SƠ ĐỒ TỔ CHỨC",
        nodes=[DiagramNode(node_id="A", label="Giám đốc")])]
    settings = {"method": NEW if modern else "legacy"}
    out = ExportDocumentUseCase.execute(doc, tmp_path, assets_mode="none", output_mode=storage_mode, document_layout=settings)
    for name in ("document.md", "result.md", "pages/page-0001.md"):
        text = (out / name).read_text(encoding="utf-8")
        assert text.count("```mermaid") == (1 if modern or name != "document.md" else 0)
        assert "Chữ nguồn vẫn được giữ." in text
    assert (out / "layout_order.json").exists() == modern
    # Furniture evidence is now independent of the reading-order method.
    assert (out / "furniture.json").exists()
    source = json.loads((out / "extraction.json").read_text(encoding="utf-8"))
    document = json.loads((out / "document.json").read_text(encoding="utf-8"))
    structure = json.loads((out / "structure.json").read_text(encoding="utf-8"))
    assert source["schema_version"] == ("extraction-v2-layout" if modern else "extraction-v1")
    from src.ocr.infrastructure.export.manifest_exporter import PIPELINE_VERSION
    assert source["pipeline_version"] == PIPELINE_VERSION
    assert validate_contract_set(source, document, structure) == []
    assert validate_document_artifacts(out)["status"] != "FAIL"
    if modern:
        document["schema_version"] = "doc-v1"
        assert any("schema differs" in error for error in validate_contract_set(source, document, structure))


def test_borderless_financial_table_keeps_values_on_each_row():
    items = []
    for row, label in enumerate(("Tiền và tương đương tiền", "Các khoản phải thu", "Hàng tồn kho", "Tài sản ngắn hạn khác")):
        y = 100 + row * 20
        items += [LayoutItem(f"label{row}", [40, y, 280, y+10], label),
                  LayoutItem(f"year1-{row}", [330 + row, y, 410, y+10], "1.234.567"),
                  LayoutItem(f"year2-{row}", [470 + row, y, 550, y+10], "(987.654)")]
    result = infer_region_tree(list(reversed(items)))
    assert result.ordered_ids == [item.id for item in items]
    assert result.tree["type"] == "borderless_table"
    # Identical geometry with prose remains a three-column layout.
    prose = [LayoutItem(item.id, item.bbox, "Nội dung đoạn văn tiếp theo.") for item in items]
    assert infer_region_tree(prose).ordered_ids == [i.id for i in prose[::3]] + [i.id for i in prose[1::3]] + [i.id for i in prose[2::3]]


def test_repeated_styled_title_keeps_one_section_in_both_modes():
    pages = [[b("TỔ CHỨC VÀ NHÂN SỰ", [40, 30, 500, 50], n, font_size=16, is_bold=True),
              b("Công ty tiếp tục phát triển hoạt động sản xuất kinh doanh.", [40, 200, 500, 220], n, font_size=10)] for n in range(1, 6)]
    legacy = make_doc(copy.deepcopy(pages))
    assert len(build_structure(document_items(legacy)).sections) == 1
    modern = make_doc(pages)
    resolve_document_layout(modern)
    structure = build_structure(document_items(modern), layout_method=NEW)
    assert len(structure.sections) == 1
    assert modern.pages[0].blocks[0].visibility == "visible"
    assert all(page.blocks[0].block_type == "running_header" for page in modern.pages[1:])
    assert not any(block.role_status == "NEEDS_REVIEW" for page in modern.pages for block in page.blocks)


def test_numbered_and_midpage_headings_are_not_repeated_furniture():
    doc = make_doc([[b("1. TỔ CHỨC VÀ NHÂN SỰ", [40, 30, 500, 50], n, font_size=16, is_bold=True),
                     b("TỔ CHỨC VÀ NHÂN SỰ", [40, 350, 500, 370], n, font_size=16, is_bold=True)] for n in range(1, 6)])
    resolve_document_layout(doc)
    assert all(block.visibility == "visible" for page in doc.pages for block in page.blocks)


def test_ordinary_sixty_line_page_and_unheaded_columns_do_not_flood_review():
    doc = make_doc([[b(f"Dòng nội dung riêng biệt số {row}.", [40, 20 + row*12, 550, 30 + row*12]) for row in range(60)]])
    resolve_document_layout(doc)
    assert doc.pages[0].metadata["layout_review"] == []
    assert doc.pages[0].quality.review_required_count == 0
    assert all(block.role_status != "NEEDS_REVIEW" and not block.review_reason_codes for block in doc.pages[0].blocks)
    columns = [LayoutItem(f"{c}-{r}", [c*100, 100+r*18, c*100+80, 110+r*18], "Văn bản thông thường") for c in range(3) for r in range(6)]
    assert infer_region_tree(columns).review == []


@pytest.mark.parametrize("text", ["VCI", "civil", "IIII", "LC"])
@pytest.mark.parametrize("initial_role", ["paragraph", "page_number"])
def test_invalid_roman_words_remain_visible_at_page_edge(text, initial_role):
    doc = make_doc([[b(text, [40, 810, 100, 824], block_type=initial_role)]])
    resolve_document_layout(doc)
    assert doc.pages[0].blocks[0].block_type == "paragraph"
    assert text in MarkdownExporter.export_page_markdown(doc.pages[0])


@pytest.mark.parametrize("text", ["VCI", "civil", "IIII", "LC"])
def test_opt_in_dla_does_not_label_invalid_roman_text_as_pagination(text):
    engine = DLAEngine(legacy_furniture=False, device="cpu")
    _, label, _ = engine._classify_text_block(text, BoundingBox(x0=40, y0=810, x1=100, y1=824),
                                            842, 600, 2, 11, 84, 758)
    assert label != "page_number"


@pytest.mark.parametrize("sequence", [["IV", "V", "VI"], ["xii", "xiii", "xiv"],
    ["Trang IV", "Trang V", "Trang VI"], ["Trang 12/100", "Trang 13/100", "Trang 14/100"]])
def test_valid_page_number_sequences_move_to_metadata(sequence):
    doc = make_doc([[b(text, [40, 810, 100, 824], n)] for n, text in enumerate(sequence, 1)])
    resolve_document_layout(doc)
    assert all(page.blocks[0].block_type == "page_number" for page in doc.pages)


def test_profile_defaults_are_legacy_and_invalid_settings_are_rejected():
    assert all(document_layout_settings(profile)["method"] == "legacy" for profile in load_profiles()[1].values())
    assert document_layout_settings({})["method"] == "legacy"
    for bad in (None, "xycut", {"method": "typo"}, {"method": NEW, "min_gap": 0}):
        with pytest.raises(ConfigError):
            document_layout_settings({"document_layout": bad})


def test_legacy_export_preserves_selected_sorter_and_removes_stale_sidecars(tmp_path):
    blocks = [b(f"Column {c}, line {r} content.", [40+c*170, 150+r*18, 190+c*170, 160+r*18]) for c in range(3) for r in range(10)]
    outputs = {}
    for method in ("heuristic", "xycut"):
        sorted_blocks = ReadingOrderSorter(method=method).sort_blocks(copy.deepcopy(blocks))
        doc = make_doc([sorted_blocks])
        out = ExportDocumentUseCase.execute(doc, tmp_path / method, assets_mode="none")
        extraction = json.loads((out / "extraction.json").read_text(encoding="utf-8"))
        outputs[method] = [item["text_raw"] for item in extraction["pages"][0]["items"]]
        assert outputs[method] == [block.text for block in sorted_blocks]
        assert not (out / "layout_order.json").exists()
    assert outputs["heuristic"] != outputs["xycut"]
    modern = make_doc(copy.deepcopy([blocks]))
    out = ExportDocumentUseCase.execute(modern, tmp_path / "reuse", assets_mode="none", document_layout={"method": NEW})
    assert (out / "furniture.json").exists()
    legacy = make_doc(copy.deepcopy([blocks]))
    ExportDocumentUseCase.execute(legacy, tmp_path / "reuse", assets_mode="none")
    assert json.loads((out / "furniture.json").read_text(encoding="utf-8"))["method"] == "document_context_v3"
    frozen = make_doc(copy.deepcopy([blocks]))
    ExportDocumentUseCase.execute(frozen, tmp_path / "reuse", assets_mode="none",
        document_layout={"method": "legacy", "furniture": {"method": "legacy_v6_1"}})
    assert not (out / "furniture.json").exists()


@pytest.mark.parametrize("layout_method", [None, "legacy", NEW])
def test_conversion_respects_profile_opt_in(tmp_path, monkeypatch, layout_method):
    """Exercise the real converter/exporter boundary without loading models."""
    from src.ocr.application import convert_document
    source = tmp_path / "input.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(source)
    profile = {"profile": "probe", "primary_engine": "rapidocr", "docling": {"enabled": False},
               "reading_order": {"method": "xycut"}}
    if layout_method is not None:
        profile["document_layout"] = {"method": layout_method}
    path = tmp_path / "profiles.yaml"
    path.write_text(yaml.safe_dump({"profiles": {"probe": profile}}), encoding="utf-8")

    class PageProcessor:
        cache_key = "fixture"
        def __init__(self, pdf, config, *_):
            assert config["reading_order"]["method"] == "xycut"
            self.structure_extractor = Mock(enabled=False)
        def execute(self, **_):
            page = make_doc([[b("Ordinary body paragraph.", [40, 200, 450, 220])]]).pages[0]
            page.metadata["reading_order"] = {"method": "xycut"}
            return page
        def close(self):
            pass

    monkeypatch.setattr(convert_document, "ProcessPageUseCase", PageProcessor)
    doc = ConvertDocumentUseCase(str(path)).execute(str(source), "probe", output_dir=str(tmp_path / "out"), assets_mode="none")
    modern = layout_method == NEW
    assert ("ordered_item_ids" in doc.pages[0].metadata) == modern
    assert doc.pages[0].metadata["reading_order"]["method"] == (NEW if modern else "xycut")
    assert (tmp_path / "out/input/layout_order.json").exists() == modern

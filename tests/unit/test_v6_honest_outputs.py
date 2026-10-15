"""V6 output contracts: no invented confidence (C2.3d, H11), HTML sidecars
follow refilled cells (C3.5e), asset modes (ENV-10, H26) and running-header
matching robust to letter spacing (C2.3a, H6)."""
from unittest.mock import Mock

import pymupdf
import pytest

from src.ocr.application.export_document import ExportDocumentUseCase
from src.ocr.application.process_page import ProcessPageUseCase, _known, _mean
from src.ocr.domain.models.document import ExtractedDocument
from src.ocr.domain.models.evidence_block import EvidenceBlock
from src.ocr.domain.models.page import ExtractedPage
from src.ocr.domain.models.quality import DocumentQualityReport, PageQualityReport
from src.ocr.domain.models.region import BoundingBox, ContentType, Region
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.routing.page_type import PageType
from src.ocr.infrastructure.export.artifact_validator import validate_document_artifacts
from src.ocr.infrastructure.layout.docling_structure import DoclingStructureExtractor


def docling_response(cell_text=""):
    return {"status": "success", "provenance": {"device": "cpu"},
            "tables": [{"bbox": [10, 40, 110, 100], "num_rows": 1, "num_cols": 2, "html": "<table></table>",
                        "cells": [{"row": 0, "col": 0, "row_span": 1, "col_span": 1, "text": "Label",
                                   "bbox": [10, 40, 60, 100]},
                                  {"row": 0, "col": 1, "row_span": 1, "col_span": 1, "text": cell_text,
                                   "bbox": [60, 40, 110, 100]}]}],
            "formulas": [{"bbox": [10, 120, 100, 145], "latex": "E=mc^2"}]}


def enrich(blocks):
    proc = ProcessPageUseCase.__new__(ProcessPageUseCase)
    proc.structure_extractor = Mock(enabled=True, options={"page_selection": "all"})
    proc.structure_extractor.extract_page.return_value = DoclingStructureExtractor.map_response(docling_response(), 4)
    regions = []
    meta = {"width": 595, "height": 842}
    (blocks, tables, formulas) = proc._enrich_structures(None, 4, PageType.NATIVE_TEXT, blocks, [], regions, meta)
    return blocks, tables, formulas, regions


def test_refilled_docling_table_keeps_unavailable_confidence():
    native = TextBlock(text="1.234.567", page=4, bbox=[70, 60, 100, 80], confidence=0.98)
    blocks, tables, _, regions = enrich([native])
    table = tables[0]
    assert table.cells[1].text == "1.234.567"  # refilled from the native block
    assert table.confidence is None and table.confidence_source == "unavailable"
    formula_block = next(b for b in blocks if b.block_type == "formula")
    assert formula_block.confidence is None and formula_block.quality_score is None
    assert formula_block.confidence_source == "unavailable"
    assert all(r.confidence is None and r.status == "NEEDS_REVIEW" for r in regions)


def test_unscored_values_are_excluded_not_replaced():
    assert _known([0.5, None, 1.0]) == [0.5, 1.0]
    assert _mean([None, None]) is None
    assert _mean([0.5, None, 1.0]) == 0.75


def test_text_block_serializes_missing_confidence():
    block = TextBlock(text="x", page=1, confidence=None, quality_score=None, confidence_source="unavailable")
    data = block.to_dict()
    assert data["confidence"] is None and data["quality_score"] is None
    assert data["confidence_source"] == "unavailable"
    assert TextBlock.model_validate(block.model_dump()).confidence is None


def _page_with_regions(page_num=1, regions=(), tables=(), blocks=()):
    blocks = list(blocks)
    regions = list(regions) or [Region(region_id="r1", bbox=BoundingBox(x0=0, y0=0, x1=595, y1=842),
                                       content_type=ContentType.TEXT, status="EXTRACTED")]
    evidence = [EvidenceBlock(block_id=f"b{i}", page=page_num, region_id=regions[0].region_id, bbox=b.bbox,
                              orientation=0, text=b.text, source="native") for i, b in enumerate(blocks)]
    return ExtractedPage(page_num=page_num, width=595, height=842, page_type=PageType.NATIVE_TEXT,
                         blocks=blocks, tables=list(tables), regions=regions, evidence_blocks=evidence,
                         quality=PageQualityReport(page=page_num, page_type="NATIVE_TEXT"))


def _doc(pages, file_path="unused.pdf"):
    return ExtractedDocument(document_id="fixture", file_path=str(file_path), file_hash="abc",
                             total_pages=len(pages), selected_pages=[p.page_num for p in pages], pages=pages,
                             manifest={"selected_pages": [p.page_num for p in pages]},
                             quality=DocumentQualityReport(document_id="fixture", total_pages=len(pages)))


def test_html_sidecar_is_rendered_from_refilled_cells(tmp_path):
    table = DoclingStructureExtractor.map_response(docling_response(), 1)["tables"][0]
    table.cells[1].text = "Refilled value"  # as _enrich_structures does after TableFormer
    out = ExportDocumentUseCase.execute(_doc([_page_with_regions(tables=[table])]), tmp_path)
    html = (out / table.provenance["html_artifact"]).read_text(encoding="utf-8")
    assert "Refilled value" in html and "Label" in html
    assert table.provenance["html_artifact_source"] == "cells"
    assert table.provenance["html"] == "<table></table>"  # Docling's own HTML is kept for reference


def _image_region():
    return Region(region_id="p1_img", bbox=BoundingBox(x0=50, y0=50, x1=300, y1=400),
                  content_type=ContentType.IMAGE, status="PRESERVED")


def test_assets_none_writes_no_images_and_validates(tmp_path):
    text_region = Region(region_id="r1", bbox=BoundingBox(x0=0, y0=500, x1=595, y1=842),
                         content_type=ContentType.TEXT, status="EXTRACTED")
    page = _page_with_regions(regions=[text_region, _image_region()],
                              blocks=[TextBlock(text="Body", page=1, bbox=[10, 600, 100, 620])])
    out = ExportDocumentUseCase.execute(_doc([page]), tmp_path, assets_mode="none")
    assert not (out / "images").exists()
    assert "<!-- image omitted: p1_img -->" in (out / "pages" / "page-0001.md").read_text(encoding="utf-8")
    report = validate_document_artifacts(out)
    assert report["status"] != "FAIL", report["errors"]
    # The omission note is required in this mode.
    md = out / "pages" / "page-0001.md"
    md.write_text(md.read_text(encoding="utf-8").replace("<!-- image omitted: p1_img -->", ""), encoding="utf-8")
    assert any("omitted-image note" in e for e in validate_document_artifacts(out)["errors"])


def test_assets_jpeg_writes_jpg_crops(tmp_path):
    pdf = tmp_path / "in.pdf"
    with pymupdf.open() as source:
        source_page = source.new_page(width=595, height=842)
        source_page.draw_rect(pymupdf.Rect(60, 60, 290, 390), color=(1, 0, 0), fill=(0, 0, 1))
        source.save(pdf)
    text_region = Region(region_id="r1", bbox=BoundingBox(x0=0, y0=500, x1=595, y1=842),
                         content_type=ContentType.TEXT, status="EXTRACTED")
    page = _page_with_regions(regions=[text_region, _image_region()])
    out = ExportDocumentUseCase.execute(_doc([page], pdf), tmp_path / "out", assets_mode="jpeg")
    crop = out / "images" / "page_01_p1_img.jpg"
    assert crop.read_bytes()[:2] == b"\xff\xd8"  # JPEG magic
    assert page.regions[1].asset == "images/page_01_p1_img.jpg"
    assert validate_document_artifacts(out)["status"] != "FAIL"


def test_unknown_assets_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="assets_mode"):
        ExportDocumentUseCase.execute(_doc([_page_with_regions()]), tmp_path, assets_mode="gif")


def test_missing_structured_document_fails_v6_inventory(tmp_path):
    out = ExportDocumentUseCase.execute(_doc([_page_with_regions()]), tmp_path, assets_mode="none")
    (out / "document.json").unlink()
    report = validate_document_artifacts(out)
    assert report["status"] == "FAIL"
    assert any("document.json is missing" in message for message in report["errors"])


def test_invalid_extraction_fails_even_when_document_is_missing(tmp_path):
    out = ExportDocumentUseCase.execute(_doc([_page_with_regions()]), tmp_path, assets_mode="none")
    (out / "document.json").unlink()
    (out / "extraction.json").write_text('{"schema": "extraction-v1"}', encoding="utf-8")
    report = validate_document_artifacts(out)
    assert report["status"] == "FAIL"
    assert any("extraction.json" in message for message in report["errors"])


def test_letter_spaced_running_header_is_still_detected_in_legacy_mode():
    pages = []
    for page_num, text in ((1, "BÁO CÁO THƯỜNG NIÊN 2025"), (2, "BÁO CÁ O THƯỜNG NIÊN 2025"),
                           (3, "BÁO CÁO THƯỜNG NIÊN 2025")):
        header = TextBlock(text=text, page=page_num, bbox=[100, 20, 400, 40], block_type="header")
        pages.append(_page_with_regions(page_num=page_num, blocks=[header]))
    ExportDocumentUseCase._detect_running_headers_and_footers(_doc(pages))
    assert [p.blocks[0].block_type for p in pages] == ["running_header"] * 3

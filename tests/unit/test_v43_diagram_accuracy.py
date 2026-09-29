from src.ocr.infrastructure.layout.diagram_builder import DiagramBuilder
from src.ocr.infrastructure.export.json_exporter import JSONExporter
from src.ocr.infrastructure.export.markdown_exporter import MarkdownExporter
from src.ocr.domain.models.page import ExtractedPage
from src.ocr.domain.models.quality import PageQualityReport
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.routing.page_type import PageType


def _line(text: str, y: int) -> dict:
    return {"text": text, "bbox": [10, y, 200, y + 15]}


def test_roles_in_financial_text_do_not_create_diagram() -> None:
    lines = [
        _line("Hội đồng quản trị", 10),
        _line("Ban kiểm soát", 30),
        _line("Tổng giám đốc", 50),
    ]
    assert not DiagramBuilder.is_diagram_content(lines)
    assert DiagramBuilder.build_org_chart(lines, page_num=1) is None


def test_diagram_uses_source_title_and_no_invented_departments() -> None:
    lines = [
        _line("Sơ đồ tổ chức", 10),
        _line("Đại hội đồng cổ đông", 40),
        _line("Hội đồng quản trị", 70),
        _line("Tổng giám đốc", 100),
        _line("Phòng Đầu tư", 130),
    ]
    diagram = DiagramBuilder.build_org_chart(lines, page_num=1)
    assert diagram is not None
    assert diagram.title == "Sơ đồ tổ chức"
    labels = [node.label for node in diagram.nodes]
    assert not any("BIA - RƯỢU" in label for label in labels)
    assert not any("MARKETING" in label for label in labels)


def test_diagram_survives_json_round_trip() -> None:
    diagram = DiagramBuilder.build_org_chart(
        [_line("Sơ đồ tổ chức", 10), _line("Hội đồng quản trị", 40), _line("Tổng giám đốc", 70)],
        page_num=1,
    )
    page = ExtractedPage(
        page_num=1,
        width=200,
        height=100,
        page_type=PageType.NATIVE_TEXT,
        quality=PageQualityReport(page=1, page_type="NATIVE_TEXT"),
        diagrams=[diagram],
    )
    restored = ExtractedPage.model_validate(JSONExporter.export_page_json(page))
    assert restored.diagrams[0].title == "Sơ đồ tổ chức"
    assert "```mermaid" in restored.diagrams[0].to_mermaid()


def test_diagram_does_not_suppress_other_ocr_text() -> None:
    diagram = DiagramBuilder.build_org_chart(
        [_line("Sơ đồ tổ chức", 10), _line("Hội đồng quản trị", 40), _line("Tổng giám đốc", 70)],
        page_num=1,
    )
    page = ExtractedPage(
        page_num=1,
        width=200,
        height=100,
        page_type=PageType.SCANNED_IMAGE,
        quality=PageQualityReport(page=1, page_type="SCANNED_IMAGE"),
        diagrams=[diagram],
        blocks=[TextBlock(text="Nội dung bên cạnh sơ đồ", source="image_ocr", page=1, bbox=[10, 80, 190, 95])],
    )
    markdown = MarkdownExporter.export_page_markdown(page)
    assert "```mermaid" in markdown
    assert "Nội dung bên cạnh sơ đồ" in markdown

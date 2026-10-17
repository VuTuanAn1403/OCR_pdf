"""Regressions from Kaggle worker tracebacks; no simulated GPU certification."""
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys

import numpy as np
import pymupdf
import pytest

from src.ocr.application.process_page import ProcessPageUseCase
from src.ocr.application.route_page import RoutePageUseCase
from src.ocr.domain.models.region import BoundingBox, Region
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.routing.routing_policy import RoutingPolicy
from src.ocr.infrastructure.pdf.coordinate_transform import CoordinateChain
from src.ocr.infrastructure.pdf.ocr_bridge import bridge_font_path, load_bridge_font, ocr_bridge
from src.ocr.runtime.process_tree import log_tail, supervised_run


def test_bridge_embeds_vietnamese_without_system_fonts(monkeypatch):
    monkeypatch.delenv("OCR_BRIDGE_FONT", raising=False)
    path, font = load_bridge_font()
    assert path == bridge_font_path() and path.name == "NotoSans-Regular.ttf"
    text = "Chuyển đổi số Đặng Thị Thuỳ 123.456"
    extracted = SimpleNamespace(width=400, height=300,
        blocks=[TextBlock(text=text, page=1, bbox=[20, 70, 370, 100], source="primary_ocr")])
    with pymupdf.open() as source:
        page = source.new_page(width=400, height=300)
        page.insert_text((20, 150), "NATIVE DECOY")
        with ocr_bridge(page, extracted, 72) as bridge:
            actual = " ".join(bridge[0].get_text().split()).replace("\u00a0", " ")
            assert actual == text
            assert all(span["type"] == 3 for span in bridge[0].get_texttrace())


def test_missing_or_non_vietnamese_override_fails_clearly(tmp_path, monkeypatch):
    path = tmp_path / "missing.ttf"
    monkeypatch.setenv("OCR_BRIDGE_FONT", str(path))
    with pytest.raises(RuntimeError, match="font missing"):
        load_bridge_font()
    path.write_bytes(pymupdf.Font("helv").buffer)
    with pytest.raises(RuntimeError, match="does not cover Vietnamese"):
        load_bridge_font()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_full_page_ocr_layout_regions_and_bridge_share_display_points(rotation, monkeypatch, tmp_path):
    import src.ocr.application.process_page as module
    from src.ocr.infrastructure.layout.model_adapters import image_box_to_pdf

    text = "Chuyển đổi số Internet di động 123.456"
    with pymupdf.open() as source:
        page = source.new_page(width=600, height=900)
        page.insert_text((160, 300), "NATIVE DECOY")
        page.draw_rect((160, 400, 350, 500), fill=(1, 0, 0))
        raster = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), False)
        raster.clear_with(100)
        page.insert_image((380, 570, 500, 720), stream=raster.tobytes("png"))
        page.set_cropbox(pymupdf.Rect(50, 70, 550, 870))
        page.set_rotation(rotation)
        native = pymupdf.Rect(100, 180, 330, 210)
        expected = list(native * page.rotation_matrix)
        fake = Mock()
        fake.predict_image.return_value = [{"bbox": expected, "text": text, "confidence": .99}]
        monkeypatch.setattr(module, "VietnameseSeq2SeqEngine", lambda **kw: fake)
        config = {"primary_engine": "vietocr", "native_first": False, "ocr_source_policy": "gpu_ocr_only_v1",
            "render_dpi": 72, "orientation_fallback_enabled": False, "fallback_enabled": False,
            "two_up_detection_enabled": False, "multi_region_detection_enabled": False,
            "docling": {"enabled": False}}
        processor = ProcessPageUseCase(source, config, "fixture")
        engine = processor.content_analyzer.dla_engine
        engine.model_path, engine.strict = Path("fixture-layout"), True
        engine._session = Mock()
        # Adapter output is native crop-relative even though OCR is displayed.
        engine._run_onnx_dla = Mock(side_effect=lambda image, p, n: [Region(region_id="model",
            bbox=BoundingBox(**dict(zip(("x0", "y0", "x1", "y1"), image_box_to_pdf(expected, image.shape, p)))),
            label="section_header")])
        try:
            result = processor.execute(1, 1, RoutePageUseCase(RoutingPolicy()))
        finally:
            processor.close()
        assert result.blocks and all(b.source != "native" for b in result.blocks)
        assert result.blocks[0].bbox == pytest.approx(expected, abs=.02)
        predicted = next(r for r in result.regions if r.region_id == "model")
        assert predicted.bbox.to_list() == pytest.approx(expected, abs=.02)
        assert result.metadata["layout_model"]["status"] == "MODEL_OUTPUT"
        assert result.metadata["layout_model"]["coordinates"].startswith("PDF_DISPLAY")
        chain = CoordinateChain.from_dict(result.metadata["coordinate_space"]["native_to_output"])
        assert chain.inverse().bbox(expected) == pytest.approx(list(native), abs=.02)
        vector = next(r for r in result.regions if r.source == "vector")
        assert vector.bbox.to_list() == pytest.approx(list(pymupdf.Rect(110, 330, 300, 430) * page.rotation_matrix), abs=.02)
        image = next(r for r in result.regions if r.source == "image")
        assert image.bbox.to_list() == pytest.approx(list(pymupdf.Rect(330, 500, 450, 650) * page.rotation_matrix), abs=.02)
        # Docling receives the original visual page and the same OCR coordinates.
        with ocr_bridge(page, result, 72) as bridge:
            assert list(bridge[0].rect) == pytest.approx(list(page.rect))
            assert text in " ".join(bridge[0].get_text().split()).replace("\u00a0", " ")
            expected_pixels = np.frombuffer(page.get_pixmap(dpi=72).samples, dtype=np.uint8)
            actual_pixels = np.frombuffer(bridge[0].get_pixmap(dpi=72).samples, dtype=np.uint8)
            assert np.array_equal(actual_pixels, expected_pixels)
        assert page.rotation == rotation  # Source geometry remains intact.
        from src.ocr.application.export_document import ExportDocumentUseCase
        pdf_path = tmp_path / "source.pdf"
        source.save(pdf_path)
        ExportDocumentUseCase._export_image_assets(SimpleNamespace(pages=[result], file_path=str(pdf_path)),
                                                   tmp_path / "export", "png")
        crop = pymupdf.Pixmap(str(tmp_path / "export" / image.asset))
        assert abs(crop.width - image.bbox.width * 2.5) <= 1
        assert abs(crop.height - image.bbox.height * 2.5) <= 1
        # The exported crop contains the projected image, rather than a region
        # from the wrong side of the original rotated page.
        assert np.mean(np.frombuffer(crop.samples, dtype=np.uint8)) == pytest.approx(100, abs=2)


def test_worker_exception_reaches_batch_error(tmp_path):
    log = tmp_path / "text.log"
    code = "import time; time.sleep(.3); raise RuntimeError('Strict GPU layout original traceback')"
    with pytest.raises(RuntimeError, match="Strict GPU layout original traceback"):
        supervised_run([sys.executable, "-c", code], cwd=tmp_path, env=os.environ.copy(),
                       log_path=log, timeout=10)
    log.write_text("x" * 100_000 + "\nRuntimeError: font missing", encoding="utf-8")
    assert log_tail(log).endswith("RuntimeError: font missing")
    assert len(log_tail(log)) <= 2400

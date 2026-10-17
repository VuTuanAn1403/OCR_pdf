"""Raster PDF + hidden text from phase-A OCR; no original text layer copying."""
from contextlib import contextmanager
import os
from pathlib import Path

import pymupdf

from src.ocr.runtime.paths import PROJECT_ROOT, runtime_resource


def bridge_font_path():
    """A repository font makes headless workers independent of OS fonts."""
    default = PROJECT_ROOT / "assets/fonts/NotoSans-Regular.ttf"
    if not (PROJECT_ROOT / "pyproject.toml").is_file():
        default = runtime_resource("fonts/NotoSans-Regular.ttf")
    return Path(os.environ.get("OCR_BRIDGE_FONT") or default).absolute()


def load_bridge_font(path=None):
    path = Path(path) if path is not None else bridge_font_path()
    if not path.is_file():
        raise RuntimeError(f"OCR bridge font missing: {path}. Restore assets/fonts or set OCR_BRIDGE_FONT.")
    font = pymupdf.Font(fontfile=str(path))
    sample = "ĐđĂăÂâÊêÔôƠơƯưẮằẵặẾềễệỐồỗộỚờỡợỨừữựỲỳỷỹỵ"
    if any(not font.has_glyph(ord(char), fallback=False) for char in sample):
        raise RuntimeError(f"OCR bridge font does not cover Vietnamese: {path}")
    return path, font


@contextmanager
def ocr_bridge(source_page, extracted_page, dpi, *, font_path=None):
    font_path, font = load_bridge_font(font_path)
    with pymupdf.open() as bridge:
        target = bridge.new_page(width=extracted_page.width, height=extracted_page.height)
        target.insert_image(target.rect, pixmap=source_page.get_pixmap(dpi=dpi, alpha=False))
        target.insert_font(fontname="OCRBridge", fontfile=str(font_path))
        for block in extracted_page.blocks:
            if block.source == "native":
                raise ValueError("OCR bridge refuses native text")
            if len(block.bbox) != 4 or not block.text.strip():
                continue
            x0, y0, x1, y1 = block.bbox
            for index, line in enumerate(block.text.splitlines()):
                if not line.strip():
                    continue
                size = min(max(1., (y1-y0)/max(1,len(block.text.splitlines()))*.70),
                           max(1., (x1-x0)/max(.1,font.text_length(line, fontsize=1))))
                target.insert_text((x0, y0+size*(index+1)), line, fontname="OCRBridge", fontsize=size, render_mode=3)
        yield bridge

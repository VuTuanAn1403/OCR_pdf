"""Raster PDF + hidden text from phase-A OCR; no original text layer copying."""
from contextlib import contextmanager
import os
from pathlib import Path

import pymupdf


@contextmanager
def ocr_bridge(source_page, extracted_page, dpi):
    fonts = [Path(os.getenv("OCR_BRIDGE_FONT", "C:/Windows/Fonts/arial.ttf")),
             Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    font_path = next((p for p in fonts if p.is_file()), None)
    if font_path is None:
        raise RuntimeError("Set OCR_BRIDGE_FONT to a Unicode TrueType font")
    font = pymupdf.Font(fontfile=str(font_path))
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

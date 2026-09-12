from typing import List, Dict, Any
import pymupdf
import unicodedata
from src.ocr.domain.models.text_block import TextBlock

class NativeTextExtractor:
    """
    Extracts high-quality native text blocks and lines with exact geometry and formatting.
    """
    def __init__(self, doc: pymupdf.Document):
        self.doc = doc

    def extract_blocks(self, page_num: int) -> List[TextBlock]:
        """
        Extracts native text blocks for page_num (1-indexed).
        """
        page = self.doc[page_num - 1]
        text_page = page.get_text("dict")
        blocks_data = text_page.get("blocks", [])

        extracted_blocks: List[TextBlock] = []
        line_idx = 0

        for b in blocks_data:
            if b.get("type") != 0:  # 0 is text, 1 is image
                continue

            for line in b.get("lines", []):
                line_bbox = line.get("bbox", [0, 0, 0, 0])
                line_text_parts = []
                font_sizes = []
                is_bold = False

                for span in line.get("spans", []):
                    span_text = span.get("text", "")
                    if span_text:
                        line_text_parts.append(span_text)
                        font_sizes.append(span.get("size", 10.0))
                        flags = span.get("flags", 0)
                        if flags & 2 or "bold" in span.get("font", "").lower():
                            is_bold = True

                full_line_text = "".join(line_text_parts).strip()
                if not full_line_text:
                    continue

                avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 10.0
                norm_text = unicodedata.normalize("NFC", full_line_text)

                block = TextBlock(
                    text=norm_text,
                    source="native",
                    page=page_num,
                    bbox=[round(coord, 2) for coord in line_bbox],
                    confidence=1.0,
                    quality_score=1.0,
                    reviewed=False,
                    block_type="paragraph",
                    line_index=line_idx,
                    font_size=round(avg_font_size, 1),
                    is_bold=is_bold
                )
                extracted_blocks.append(block)
                line_idx += 1

        return extracted_blocks

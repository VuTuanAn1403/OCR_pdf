from typing import List, Dict, Any, Tuple
import pymupdf
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.infrastructure.pdf.pdf_renderer import PDFRenderer
from src.ocr.infrastructure.ocr.fallback_engine import FallbackEngine

class ReocrRegionUseCase:
    """
    Executes selective regional re-OCR on lines that failed the OCR quality gate.
    """
    def __init__(self, renderer: PDFRenderer, fallback_engine: FallbackEngine):
        self.renderer = renderer
        self.fallback_engine = fallback_engine

    def execute(
        self,
        page_num: int,
        blocks_to_retry: List[TextBlock],
        upscale_dpi: int = 250
    ) -> Tuple[List[TextBlock], int]:
        """
        Re-crops and re-runs OCR on specific failed blocks.
        Returns (updated_blocks, improved_count).
        """
        improved_count = 0
        for block in blocks_to_retry:
            if not block.bbox or len(block.bbox) < 4:
                continue

            crop_img, _ = self.renderer.render_region_crop(
                page_num=page_num,
                bbox=block.bbox,
                dpi=upscale_dpi,
                margin=4.0
            )
            if crop_img is None or crop_img.size == 0:
                continue

            res = self.fallback_engine.reocr_crop(
                crop_bgr=crop_img,
                original_text=block.text,
                original_conf=block.confidence
            )

            if res.get("needs_review"):
                block.fallback_reason = res.get("review_reason") or "ocr_disagreement"
                block.reviewed = False

            if res.get("improved", False):
                block.text = res["text"]
                block.confidence = res["confidence"]
                block.quality_score = res.get("quality_score", block.quality_score)
                block.source = "fallback_ocr"
                block.engine = "vietnamese_seq2seq_fallback"
                block.fallback = True
                if not res.get("needs_review"):
                    block.fallback_reason = "regional_quality_gate_retry"
                    block.reviewed = True
                improved_count += 1

            if res.get("needs_review"):
                block.quality_score = min(block.quality_score, 0.74)

        return blocks_to_retry, improved_count

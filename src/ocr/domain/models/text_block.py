from typing import List, Optional, Literal
from pydantic import BaseModel, Field

class TextBlock(BaseModel):
    text: str
    source: Literal["native", "primary_ocr", "fallback_ocr"] = "primary_ocr"
    page: int
    bbox: List[float] = Field(default_factory=list)  # [x0, y0, x1, y1]
    confidence: float = 1.0
    quality_score: float = 1.0
    reviewed: bool = False
    block_type: str = "paragraph"  # paragraph, title, header, footer, list_item, table_cell, caption
    line_index: int = 0
    column_index: int = 0
    font_size: Optional[float] = None
    is_bold: Optional[bool] = None
    engine: str = "vietnamese_seq2seq"
    fallback: bool = False
    fallback_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "source": self.source,
            "engine": self.engine,
            "page": self.page,
            "bbox": self.bbox,
            "confidence": round(self.confidence, 4),
            "quality_score": round(self.quality_score, 4),
            "reviewed": self.reviewed,
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason
        }

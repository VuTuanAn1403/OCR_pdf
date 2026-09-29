from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from src.ocr.domain.models.region import ContentType


class EvidenceBlock(BaseModel):
    """
    V3.1.2 Evidence-Preserving intermediate representation (spec §3).

    Markdown is only generated AFTER EvidenceBlocks are complete.
    Each block carries its provenance (source, confidence, content_type)
    so the merge/reading-order stage can make informed decisions.
    """
    page: int
    block_id: str = ""
    region_id: Optional[str] = None
    bbox: List[float] = Field(default_factory=list)  # [x0, y0, x1, y1]
    bbox_original: List[float] = Field(default_factory=list)
    bbox_normalized: List[float] = Field(default_factory=list)
    text: str = ""
    content_type: ContentType = ContentType.TEXT
    source: Literal["native", "ocr", "vector", "image"] = "native"
    confidence: float = 1.0
    orientation: Optional[float] = None
    rotation: float = 0.0
    column_id: Optional[int] = None
    table_id: Optional[str] = None
    image_id: Optional[str] = None
    reading_order: int = -1
    parent_region: Optional[str] = None
    transform_matrix: List[float] = Field(default_factory=list)
    status: str = "VALIDATED"
    failure_reason: Optional[str] = None
    quality_score: float = 1.0
    block_type: str = "paragraph"
    is_bold: Optional[bool] = None
    font_size: Optional[float] = None

from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from src.ocr.domain.models.confidence import ConfidenceModel

class TextBlock(ConfidenceModel):
    text: str
    text_raw: Optional[str] = None
    correction_source: Optional[str] = None
    source: Literal["native", "primary_ocr", "fallback_ocr", "image_ocr", "vector", "docling"] = "primary_ocr"
    page: int
    bbox: List[float] = Field(default_factory=list)  # [x0, y0, x1, y1]
    # None = the producing model gives no calibrated confidence (see confidence_source).
    quality_score: Optional[float] = None
    reviewed: bool = False
    block_type: str = "paragraph"  # paragraph, title, section_header, header, footer, page_number, list_item, table_cell, caption, formula
    line_index: int = 0
    column_index: int = 0
    font_size: Optional[float] = None
    is_bold: Optional[bool] = None
    engine: str = "vietnamese_seq2seq"
    fallback: bool = False
    fallback_reason: Optional[str] = None
    region_id: Optional[str] = None
    source_id: Optional[str] = None
    source_id_method: Optional[str] = None
    raw_text_sha8: Optional[str] = None
    evidence_generation: int = 1
    producer_ref: Optional[str] = None
    reading_order_index: Optional[int] = None
    parent_region_id: Optional[str] = None
    article_group_id: Optional[str] = None
    column_id: Optional[str] = None
    role_initial: Optional[str] = None
    role_final: Optional[str] = None
    role_source: Optional[str] = None
    role_status: str = "UNRESOLVED"
    role_reasons: List[str] = Field(default_factory=list)
    support_page_ids: List[str] = Field(default_factory=list)
    template_id: Optional[str] = None
    visibility: str = "visible"
    suppression_reason: Optional[str] = None
    protected_from_furniture: bool = False
    protection_reason: Optional[str] = None
    table_candidate_id: Optional[str] = None
    review_reason_codes: List[str] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            **self.model_dump(include={
                "source_id", "source_id_method", "raw_text_sha8", "evidence_generation", "producer_ref",
                "reading_order_index", "parent_region_id", "article_group_id", "column_id",
                "role_initial", "role_final", "role_source", "role_status", "role_reasons",
                "support_page_ids", "template_id", "visibility", "suppression_reason",
                "protected_from_furniture", "protection_reason", "table_candidate_id", "review_reason_codes"}),
            "text": self.text,
            "text_raw": self.text_raw if self.text_raw is not None else self.text,
            "text_corrected": self.text,
            "correction_source": self.correction_source,
            "block_type": self.block_type,
            "source": self.source,
            "engine": self.engine,
            "page": self.page,
            "bbox": self.bbox,
            "confidence": None if self.confidence is None else round(self.confidence, 4),
            "quality_score": None if self.quality_score is None else round(self.quality_score, 4),
            "confidence_source": self._confidence_source(),
            "reviewed": self.reviewed,
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
            "region_id": self.region_id,
            "font_size": self.font_size,
            "is_bold": self.is_bold,
            "line_index": self.line_index,
            "column_index": self.column_index,
        }

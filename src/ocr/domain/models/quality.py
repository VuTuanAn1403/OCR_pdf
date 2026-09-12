from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class QualityMetrics(BaseModel):
    cer: Optional[float] = None
    wer: Optional[float] = None
    mean_confidence: float = 0.0
    vietnamese_character_errors: int = 0
    unicode_errors: int = 0
    reading_order_errors: int = 0
    table_structure_errors: int = 0
    primary_lines: int = 0
    fallback_lines: int = 0
    fallback_ratio: float = 0.0

class PageQualityReport(BaseModel):
    page: int
    page_type: str
    native_text_score: float = 0.0
    ocr_confidence: float = 0.0
    quality_score: float = 0.0
    total_blocks: int = 0
    fallback_count: int = 0
    suspicious_lines: List[str] = Field(default_factory=list)
    time_taken_seconds: float = 0.0

class DocumentQualityReport(BaseModel):
    document_id: str
    total_pages: int
    mean_native_score: float = 0.0
    mean_ocr_confidence: float = 0.0
    mean_quality_score: float = 0.0
    pages_processed_native: int = 0
    pages_processed_ocr: int = 0
    pages_processed_mixed: int = 0
    total_fallback_regions: int = 0
    total_time_seconds: float = 0.0
    metrics: QualityMetrics = Field(default_factory=QualityMetrics)
    page_reports: List[PageQualityReport] = Field(default_factory=list)

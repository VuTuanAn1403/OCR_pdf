from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

class PageAccuracyReport(BaseModel):
    page: int
    cer: float = 0.0
    wer: float = 0.0
    diacritic_accuracy: float = 1.0
    table_accuracy: Optional[float] = None
    reading_order_accuracy: float = 1.0
    ref_char_count: int = 0
    pred_char_count: int = 0
    ref_word_count: int = 0
    pred_word_count: int = 0

class AccuracyReport(BaseModel):
    document_id: str
    mean_cer: float = 0.0
    mean_wer: float = 0.0
    mean_diacritic_accuracy: float = 1.0
    mean_table_accuracy: Optional[float] = None
    mean_reading_order_accuracy: float = 1.0
    pages_evaluated: int = 0
    page_reports: Dict[int, PageAccuracyReport] = Field(default_factory=dict)

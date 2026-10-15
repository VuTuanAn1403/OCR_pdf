"""Exact keyword frequency reports, adapted from OCR_pdf_backup."""
from pydantic import BaseModel, Field


class KeywordCount(BaseModel):
    keyword: str
    exact_count: int = 0
    count: int = 0


class KeywordReport(BaseModel):
    document_id: str
    source_file: str
    keywords: list[KeywordCount] = Field(default_factory=list)
    total_keywords_searched: int = 0
    total_exact_matches: int = 0
    total_matches: int = 0

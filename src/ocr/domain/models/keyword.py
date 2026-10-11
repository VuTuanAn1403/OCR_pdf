from typing import List, Optional
from pydantic import BaseModel, Field


class FuzzyMatchDetail(BaseModel):
    """Details of a single approximate/fuzzy match found in the text."""
    matched_text: str = Field(..., description="The actual variant string found in OCR text")
    similarity: float = Field(..., description="Similarity ratio compared to standard keyword (0.0 - 1.0)")
    context: str = Field(default="", description="Sentence or excerpt containing the match")
    is_llm_confirmed: bool = Field(default=True, description="Whether LLM verified this as valid OCR error/variant")
    reasoning: Optional[str] = Field(default=None, description="Explanation or classification from verifier")


class KeywordCount(BaseModel):
    """Represents the count of a single keyword/phrase found in the document text."""
    keyword: str
    exact_count: int = 0
    fuzzy_count: int = 0
    count: int = 0  # Total count = exact_count + fuzzy_count (backwards compatible)
    variants: List[FuzzyMatchDetail] = Field(default_factory=list)

    def sync_total(self) -> None:
        """Synchronizes total count with exact and verified fuzzy counts."""
        self.count = self.exact_count + self.fuzzy_count


class KeywordReport(BaseModel):
    """Aggregated keyword frequency report for an entire document."""
    document_id: str
    source_file: str
    keywords: List[KeywordCount] = Field(default_factory=list)
    total_keywords_searched: int = 0
    total_exact_matches: int = 0
    total_fuzzy_matches: int = 0
    total_matches: int = 0
    llm_enabled: bool = False

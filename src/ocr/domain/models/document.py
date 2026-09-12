from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from src.ocr.domain.models.page import ExtractedPage
from src.ocr.domain.models.quality import DocumentQualityReport

class ExtractedDocument(BaseModel):
    document_id: str
    file_path: str
    file_hash: str
    total_pages: int
    selected_pages: List[int] = Field(default_factory=list)
    profile: str = "balanced"
    pages: List[ExtractedPage] = Field(default_factory=list)
    quality: DocumentQualityReport
    manifest: Dict[str, Any] = Field(default_factory=dict)
    full_markdown: str = ""
    accuracy: Optional[Any] = None

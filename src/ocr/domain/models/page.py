from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.models.table import TableStructure
from src.ocr.domain.models.quality import PageQualityReport
from src.ocr.domain.routing.page_type import PageType

class ExtractedPage(BaseModel):
    page_num: int
    width: float
    height: float
    page_type: PageType
    rendered_dpi: Optional[int] = None
    blocks: List[TextBlock] = Field(default_factory=list)
    tables: List[TableStructure] = Field(default_factory=list)
    raw_text: str = ""
    markdown_content: str = ""
    quality: PageQualityReport
    metadata: Dict[str, Any] = Field(default_factory=dict)

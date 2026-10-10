from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.models.table import TableStructure
from src.ocr.domain.models.quality import PageQualityReport
from src.ocr.domain.models.region import Region
from src.ocr.domain.models.evidence_block import EvidenceBlock
from src.ocr.domain.models.diagram import DiagramStructure
from src.ocr.domain.routing.page_type import PageType

class ExtractedPage(BaseModel):
    page_num: int
    width: float
    height: float
    page_type: PageType
    rendered_dpi: Optional[int] = None
    blocks: List[TextBlock] = Field(default_factory=list)
    tables: List[TableStructure] = Field(default_factory=list)
    diagrams: List[DiagramStructure] = Field(default_factory=list)
    regions: List[Region] = Field(default_factory=list)  # V3.1.2 Content Composition
    evidence_blocks: List[EvidenceBlock] = Field(default_factory=list)  # V3.1.2 Intermediate Repr
    raw_text: str = ""
    markdown_content: str = ""
    quality: PageQualityReport
    metadata: Dict[str, Any] = Field(default_factory=dict)

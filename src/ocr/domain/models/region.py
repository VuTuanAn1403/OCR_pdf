from enum import Enum
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class ContentType(str, Enum):
    """V3.1.2 Content Composition region types."""
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    VECTOR = "VECTOR"
    TABLE = "TABLE"
    IMAGE_TABLE = "IMAGE_TABLE"
    FIGURE = "FIGURE"
    DIAGRAM = "DIAGRAM"
    OUTLINED_TEXT = "OUTLINED_TEXT"
    UNKNOWN = "UNKNOWN"


class BoundingBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height

    def to_list(self) -> List[float]:
        return [round(self.x0, 2), round(self.y0, 2), round(self.x1, 2), round(self.y1, 2)]

    def intersects(self, other: "BoundingBox") -> bool:
        return not (self.x1 < other.x0 or self.x0 > other.x1 or self.y1 < other.y0 or self.y0 > other.y1)

    def contains_point(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def overlap_ratio(self, other: "BoundingBox") -> float:
        """Returns the ratio of intersection area to the smaller box's area."""
        ix0 = max(self.x0, other.x0)
        iy0 = max(self.y0, other.y0)
        ix1 = min(self.x1, other.x1)
        iy1 = min(self.y1, other.y1)
        if ix1 <= ix0 or iy1 <= iy0:
            return 0.0
        inter_area = (ix1 - ix0) * (iy1 - iy0)
        min_area = min(self.area, other.area)
        if min_area <= 0:
            return 0.0
        return inter_area / min_area


class Region(BaseModel):
    """
    V3.1.2 Content Composition region.

    Each page is decomposed into regions with explicit content_type, source,
    and coordinate metadata.  Regions that cannot be classified are kept as
    UNKNOWN — never silently dropped.
    """
    region_id: str
    bbox: BoundingBox
    # --- V3.1.1 fields (kept for backward compat) ---
    region_type: str = "text"  # text, table, figure, header, footer
    confidence: float = 1.0
    quality_score: float = 1.0
    needs_fallback: bool = False
    failure_reason: Optional[str] = None
    # --- V3.1.2 Content Composition fields ---
    content_type: ContentType = ContentType.TEXT
    source: Literal["native", "ocr", "vector", "image", "unknown"] = "unknown"
    rotation: float = 0.0
    reading_order: int = -1
    parent_region: Optional[str] = None
    orientation_status: str = "UNKNOWN"
    orientation_confidence: float = 0.0
    transform_matrix: List[float] = Field(default_factory=list)
    status: str = "UNRESOLVED"

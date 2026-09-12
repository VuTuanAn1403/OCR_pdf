from typing import List, Optional
from pydantic import BaseModel, Field

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

class Region(BaseModel):
    region_id: str
    bbox: BoundingBox
    region_type: str = "text"  # text, table, figure, header, footer
    confidence: float = 1.0
    quality_score: float = 1.0
    needs_fallback: bool = False
    failure_reason: Optional[str] = None

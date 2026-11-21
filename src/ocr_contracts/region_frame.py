"""Exact quarter-turn transforms between a region view and its source page."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RegionFrame:
    bbox: tuple[float, float, float, float]
    clockwise_degrees: int = 0

    def __post_init__(self):
        if (len(self.bbox) != 4 or not all(math.isfinite(v) for v in self.bbox)
                or self.bbox[2] <= self.bbox[0] or self.bbox[3] <= self.bbox[1]
                or type(self.clockwise_degrees) is not int or self.clockwise_degrees not in (0,90,180,270)):
            raise ValueError("Region frame needs a finite rectangle and a quarter turn")

    @property
    def size(self):
        width,height=self.bbox[2]-self.bbox[0],self.bbox[3]-self.bbox[1]
        return (height,width) if self.clockwise_degrees in (90,270) else (width,height)

    def to_view_point(self, x, y):
        x,y=x-self.bbox[0],y-self.bbox[1]
        width,height=self.bbox[2]-self.bbox[0],self.bbox[3]-self.bbox[1]
        if self.clockwise_degrees==90:return height-y,x
        if self.clockwise_degrees==180:return width-x,height-y
        if self.clockwise_degrees==270:return y,width-x
        return x,y

    def to_source_point(self, x, y):
        width,height=self.bbox[2]-self.bbox[0],self.bbox[3]-self.bbox[1]
        if self.clockwise_degrees==90:x,y=y,height-x
        elif self.clockwise_degrees==180:x,y=width-x,height-y
        elif self.clockwise_degrees==270:x,y=width-y,x
        return x+self.bbox[0],y+self.bbox[1]

    def _box(self, box, transform):
        if len(box)!=4 or not all(math.isfinite(v) for v in box) or box[2]<box[0] or box[3]<box[1]:
            raise ValueError("Invalid region box")
        x0,y0,x1,y1=box
        points=[transform(x,y) for x,y in ((x0,y0),(x1,y0),(x1,y1),(x0,y1))]
        return [min(p[0] for p in points),min(p[1] for p in points),
                max(p[0] for p in points),max(p[1] for p in points)]

    def to_view_bbox(self, box):
        return self._box(box,self.to_view_point)

    def to_source_bbox(self, box):
        return self._box(box,self.to_source_point)

    def to_dict(self):
        return {"method":"region-quarter-turn-v1","source_bbox":list(self.bbox),
                "clockwise_degrees":self.clockwise_degrees,"view_size":list(self.size),
                "coordinate_convention":"continuous_rectangle_edges"}

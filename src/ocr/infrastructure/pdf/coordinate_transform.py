"""Composable, invertible affine coordinate chains with named spaces."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class AffineStep:
    name: str
    source: str
    target: str
    matrix: Sequence[Sequence[float]]

    def __post_init__(self):
        value = np.asarray(self.matrix, dtype=float)
        if (value.shape != (3, 3) or not np.isfinite(value).all()
                or not np.allclose(value[2], [0, 0, 1]) or abs(np.linalg.det(value)) < 1e-12):
            raise ValueError("Coordinate steps require a finite invertible 3x3 affine matrix")
        if not all(isinstance(v, str) and v for v in (self.name, self.source, self.target)):
            raise ValueError("Coordinate spaces and operation names are required")
        object.__setattr__(self, "matrix", tuple(tuple(float(v) for v in row) for row in value))


@dataclass(frozen=True)
class CoordinateChain:
    source: str
    steps: tuple[AffineStep, ...] = ()

    def __post_init__(self):
        current = self.source
        for step in self.steps:
            if step.source != current:
                raise ValueError("Coordinate steps are not contiguous")
            current = step.target

    @property
    def target(self):
        return self.steps[-1].target if self.steps else self.source

    @property
    def matrix(self):
        value = np.eye(3)
        for step in self.steps:
            value = np.asarray(step.matrix) @ value
        return value

    def then(self, name: str, target: str, matrix):
        return CoordinateChain(self.source, self.steps + (AffineStep(name, self.target, target, matrix),))

    def inverse(self):
        return CoordinateChain(self.target, tuple(AffineStep("inverse_" + step.name, step.target, step.source,
            np.linalg.inv(step.matrix)) for step in reversed(self.steps)))

    def points(self, points):
        value = np.asarray(points, dtype=float)
        if value.ndim != 2 or value.shape[1] != 2 or not np.isfinite(value).all():
            raise ValueError("Points must be finite Nx2 coordinates")
        homogeneous = np.column_stack([value, np.ones(len(value))])
        return (self.matrix @ homogeneous.T).T[:, :2].tolist()

    def bbox(self, box):
        if len(box) != 4 or box[2] < box[0] or box[3] < box[1]:
            raise ValueError("Invalid source bbox")
        a, b, c, d = box
        points = np.asarray(self.points([[a, b], [c, b], [c, d], [a, d]]))
        return [float(points[:, 0].min()), float(points[:, 1].min()),
                float(points[:, 0].max()), float(points[:, 1].max())]

    def to_dict(self):
        return {"schema_version": "affine-chain-v1", "source": self.source, "target": self.target,
                "steps": [{"op": s.name, "source": s.source, "target": s.target,
                           "matrix": [list(row) for row in s.matrix]} for s in self.steps]}

    @classmethod
    def from_dict(cls, payload):
        if payload.get("schema_version") != "affine-chain-v1":
            raise ValueError("Unknown coordinate-chain schema")
        result = cls(payload["source"], tuple(AffineStep(s["op"], s["source"], s["target"], s["matrix"])
                                               for s in payload["steps"]))
        if result.target != payload["target"]:
            raise ValueError("Coordinate chain target differs from its steps")
        return result


def page_display_chain(page) -> CoordinateChain:
    """Unrotated crop-relative points -> visible, rotated page points."""
    a, b, c, d, e, f = page.rotation_matrix
    return CoordinateChain("pdf_native_crop_points").then(
        "pdf_rotation", "pdf_display_points", [[a, c, e], [b, d, f], [0, 0, 1]])


def render_coordinate_chain(page, image_shape) -> CoordinateChain:
    """PyMuPDF native crop-relative PDF points -> rendered pixels.

Cropbox origin is already removed in native PyMuPDF coordinates. Adding it
again would shift every box; page.rotation_matrix accounts for PDF rotation.
Raster scale uses actual output dimensions, including integer rounding.
"""
    height, width = image_shape[:2]
    if min(height, width, page.rect.width, page.rect.height) <= 0:
        raise ValueError("Render dimensions must be positive")
    scale = [[width/page.rect.width, 0, 0], [0, height/page.rect.height, 0], [0, 0, 1]]
    return page_display_chain(page).then("render_scale", "render_pixels", scale)

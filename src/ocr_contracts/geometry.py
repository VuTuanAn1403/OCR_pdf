"""Pure bounding-box geometry shared by structure merging and document layout."""
from __future__ import annotations

from typing import Any
import hashlib
import math
import unicodedata

VERTICAL_LINE_ORIENTATION_METHOD = "vertical-line-orientation-v1"
LINE_ORIENTATION_METHOD = "line-orientation-v2"
BRIDGE_LINE_GEOMETRY_METHOD = "pymupdf-text-line-v1"
BRIDGE_TOKEN_GEOMETRY_METHOD = "pymupdf-text-tokens-v1"


def placed_line_bbox(source: dict, placement: dict) -> list[float] | None:
    """Accept geometry only for the same placed source text, box and angle."""
    provenance = source.get("recognition_provenance", {})
    if not isinstance(placement, dict) or not isinstance(provenance, dict) or not is_oriented_line_candidate(provenance):
        return None
    angle = source["recognition_provenance"].get("rotation_clockwise_degrees")
    if (type(angle) is not int or angle not in (0, 90, 180, 270)
            or placement.get("source_id") != source.get("source_id")
            or placement.get("status") != "PLACED_ORIENTATION_HYPOTHESIS"
            or placement.get("coordinate_space") != "bridge_display_points"
            or type(placement.get("crop_rotation_clockwise_degrees")) is not int
            or placement.get("crop_rotation_clockwise_degrees") != angle
            or placement.get("placement_geometry_method") != BRIDGE_LINE_GEOMETRY_METHOD
            or placement.get("bbox") != source.get("bbox")
            or not isinstance(source.get("text"), str)
            or placement.get("placed_text_sha256") != hashlib.sha256(source["text"].encode("utf-8")).hexdigest()):
        return None
    box, original = placement.get("placed_text_bbox"), source.get("bbox")
    if not all(isinstance(value, (list, tuple)) and len(value) == 4
               and all(type(v) in (float, int) and math.isfinite(v) for v in value)
               and value[0] < value[2] and value[1] < value[3] for value in (box, original)):
        return None
    if not (original[0] <= box[0] < box[2] <= original[2] and original[1] <= box[1] < box[3] <= original[3]):
        return None
    return list(box)


def placed_line_tokens(source: dict, placement: dict) -> list[dict] | None:
    """Require a complete ordered token geometry record bound to this line."""
    line = placed_line_bbox(source, placement)
    if line is None or placement.get("placed_token_geometry_method") != BRIDGE_TOKEN_GEOMETRY_METHOD:
        return None
    tokens = placement.get("placed_tokens")
    if (not isinstance(tokens, list) or not tokens or any(not isinstance(t, dict) for t in tokens)
            or [t.get("text") for t in tokens] != unicodedata.normalize("NFC", source["text"]).split()):
        return None
    for token in tokens:
        box = token.get("bbox")
        if (not isinstance(box, list) or len(box) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                or not (line[0] <= box[0] < box[2] <= line[2] and line[1] <= box[1] < box[3] <= line[3])):
            return None
    return tokens


def is_vertical_line_candidate(provenance: dict) -> bool:
    return provenance.get("method") == VERTICAL_LINE_ORIENTATION_METHOD


def is_oriented_line_candidate(provenance: dict) -> bool:
    """Include uncertain 180-degree lines in the same source-preservation gate."""
    return provenance.get("method") in (VERTICAL_LINE_ORIENTATION_METHOD, LINE_ORIENTATION_METHOD)


def is_non_horizontal_line_candidate(provenance: dict) -> bool:
    """A tested zero-degree reading still has horizontal source geometry."""
    return is_vertical_line_candidate(provenance) or (
        provenance.get("method") == LINE_ORIENTATION_METHOD and (
            type(provenance.get("rotation_clockwise_degrees")) is not int
            or provenance["rotation_clockwise_degrees"] != 0))


def block_is_covered(bbox: list[float], structures: list[Any], threshold: float = 0.8) -> bool:
    """Remove only blocks mostly contained in a successfully reconstructed item."""
    if len(bbox) != 4:
        return False
    x0, y0, x1, y1 = bbox
    area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if not area:
        return False
    for item in structures:
        if len(item.bbox) != 4:
            continue
        a, b, c, d = item.bbox
        intersection = max(0.0, min(x1, c) - max(x0, a)) * max(0.0, min(y1, d) - max(y0, b))
        if intersection / area >= threshold:
            return True
    return False

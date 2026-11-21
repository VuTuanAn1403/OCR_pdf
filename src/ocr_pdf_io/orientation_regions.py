"""Visible PDF glyph directions used only as image-orientation geometry."""
import math
from statistics import median

from ocr_pdf_io.coordinate_transform import page_display_chain


METHOD = "visible-glyph-orientation-geometry-v1"
_DIRECTIONS = {0: (1.0, 0.0), 90: (0.0, -1.0), 180: (-1.0, 0.0), 270: (0.0, 1.0)}


def visible_orientation_geometry(page):
    """Do not copy characters or use invisible OCR layers to authorize a turn."""
    record = {"method": METHOD, "native_text_copied": False, "upright": False, "regions": []}
    try:
        traces = page.get_texttrace()
    except (AttributeError, RuntimeError, ValueError):
        record["status"] = "UNAVAILABLE"
        return record
    if not isinstance(traces,(list,tuple)) or not traces:
        record["status"] = "NO_VISIBLE_DIRECTIONS"
        return record
    chain = page_display_chain(page)
    origin, = chain.points([[0, 0]])
    spans = []
    for span in traces:
        if span.get("type", 3) not in (0, 1) or span.get("opacity", 0) <= 0.01:
            continue
        direction = span.get("dir", ())
        box = span.get("bbox", ())
        count = len(span.get("chars", ()))
        if len(direction) != 2 or len(box) != 4 or not count:
            continue
        end, = chain.points([direction])
        dx, dy = end[0]-origin[0], end[1]-origin[1]
        length = math.hypot(dx, dy)
        if length <= 0:
            continue
        scores = {angle: (dx*x+dy*y)/length for angle, (x,y) in _DIRECTIONS.items()}
        angle = max(scores, key=scores.get)
        if scores[angle] < .995:
            continue
        bbox = chain.bbox(box)
        if not all(math.isfinite(v) for v in bbox) or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        spans.append({"bbox": bbox, "angle": angle, "glyphs": count})
    counts = {angle: sum(s["glyphs"] for s in spans if s["angle"] == angle) for angle in _DIRECTIONS}
    total = sum(counts.values())
    record.update(status="OBSERVED" if total else "NO_VISIBLE_DIRECTIONS", glyph_counts=counts)
    record["upright"] = total >= 20 and counts[0] / total >= .98
    area = float(page.rect.width * page.rect.height)
    for angle in (90, 180, 270):
        candidates = sorted((s for s in spans if s["angle"] == angle), key=lambda s: s["bbox"][0])
        if not candidates:
            continue
        gap = max(18.0, min(36.0, median(min(s["bbox"][2]-s["bbox"][0],
                                                 s["bbox"][3]-s["bbox"][1]) for s in candidates)*4))
        groups, current, right = [], [], -math.inf
        for span in candidates:
            if current and span["bbox"][0] > right + gap:
                groups.append(current)
                current = []
            current.append(span)
            right = max(s["bbox"][2] for s in current)
        if current:
            groups.append(current)
        for group in groups:
            if len(group) < 5 or sum(s["glyphs"] for s in group) < 40:
                continue
            box = [max(0., min(s["bbox"][0] for s in group)-8),
                   max(0., min(s["bbox"][1] for s in group)-8),
                   min(float(page.rect.width), max(s["bbox"][2] for s in group)+8),
                   min(float(page.rect.height), max(s["bbox"][3] for s in group)+8)]
            if (box[2]-box[0])*(box[3]-box[1]) < .01*area:
                continue
            inside = [s for s in spans if box[0] <= (s["bbox"][0]+s["bbox"][2])/2 <= box[2]
                      and box[1] <= (s["bbox"][1]+s["bbox"][3])/2 <= box[3]]
            support = sum(s["glyphs"] for s in inside if s["angle"] == angle)
            if support / max(1, sum(s["glyphs"] for s in inside)) < .9:
                continue
            record["regions"].append({"id": f"direction_region_{len(record['regions'])+1}",
                                      "bbox": box, "clockwise_degrees": angle,
                                      "method": METHOD, "support_glyphs": support,
                                      "coordinate_space": "pdf_display_points"})
    return record

"""Raster PDF + hidden text from phase-A OCR; no original text layer copying."""
from contextlib import contextmanager
from collections import defaultdict
import hashlib
import math
import os
import re
import unicodedata
from pathlib import Path

import pymupdf

from ocr_contracts.geometry import (BRIDGE_LINE_GEOMETRY_METHOD, BRIDGE_TOKEN_GEOMETRY_METHOD,
                                    LINE_ORIENTATION_METHOD, is_oriented_line_candidate)
from ocr_contracts.text_block import TextBlock
from ocr_runtime.paths import PROJECT_ROOT, is_checkout, runtime_resource


def bridge_font_path():
    """A repository font makes headless workers independent of OS fonts."""
    default = PROJECT_ROOT / "assets/fonts/NotoSans-Regular.ttf"
    if not is_checkout():
        default = runtime_resource("fonts/NotoSans-Regular.ttf")
    return Path(os.environ.get("OCR_BRIDGE_FONT") or default).absolute()


def load_bridge_font(path=None):
    path = Path(path) if path is not None else bridge_font_path()
    if not path.is_file():
        raise RuntimeError(f"OCR bridge font missing: {path}. Restore assets/fonts or set OCR_BRIDGE_FONT.")
    font = pymupdf.Font(fontfile=str(path))
    sample = "ĐđĂăÂâÊêÔôƠơƯưẮằẵặẾềễệỐồỗộỚờỡợỨừữựỲỳỷỹỵ"
    if any(not font.has_glyph(ord(char), fallback=False) for char in sample):
        raise RuntimeError(f"OCR bridge font does not cover Vietnamese: {path}")
    return path, font


def _vertical_placement(target, block, font):
    angle = block.recognition_provenance.get("rotation_clockwise_degrees")
    horizontal_retry = block.recognition_provenance.get("method") == LINE_ORIENTATION_METHOD
    if type(angle) is not int or (angle not in (90, 270) and not (horizontal_retry and angle in (0, 180))):
        return "no_supported_crop_rotation"
    text = block.text
    if text.splitlines() != [text]:
        return "vertical_candidate_is_not_one_line"
    if any(not font.has_glyph(ord(char), fallback=False) for char in text):
        return "source_text_not_supported_by_bridge_font"
    if len(block.bbox) != 4 or not all(math.isfinite(value) for value in block.bbox):
        return "invalid_source_bbox"
    box = pymupdf.Rect(block.bbox)
    if box.is_empty or not target.rect.contains(box):
        return "source_bbox_outside_bridge"
    advance = font.text_length(text, fontsize=1)
    thickness = font.ascender - font.descender
    if not advance or thickness <= 0:
        return "invalid_font_metrics"
    if horizontal_retry and angle in (0, 180):
        size = min(box.width * .98 / advance, box.height * .9 / thickness)
        along_margin = (box.width - advance * size) / 2
        cross_margin = (box.height - thickness * size) / 2
        anchor = ((box.x0 + along_margin, box.y0 + cross_margin + font.ascender * size) if angle == 0
                  else (box.x1 - along_margin, box.y1 - cross_margin - font.ascender * size))
        placed = target.insert_text(anchor, text, fontname="OCRBridge", fontsize=size, rotate=angle, render_mode=3)
        return None if placed == 1 else "text_could_not_be_inserted"
    size = min(box.height * .98 / advance, box.width * .9 / thickness)
    cross_margin = (box.width - thickness * size) / 2
    along_margin = (box.height - advance * size) / 2
    # Clockwise rotation of the input crop maps recognizer x increasing to
    # upward at 90 and downward at 270. PyMuPDF uses these same numeric values
    # for those writing directions. The display-page /Rotate is already applied.
    if angle == 90:
        anchor = (box.x0 + cross_margin + font.ascender * size, box.y1 - along_margin)
    else:
        anchor = (box.x0 + cross_margin - font.descender * size, box.y0 + along_margin)
    placed = target.insert_text(anchor, text, fontname="OCRBridge", fontsize=size, rotate=angle, render_mode=3)
    return None if placed == 1 else "text_could_not_be_inserted"


def _record_placed_geometry(target, blocks, placements):
    """Read actual hidden-line geometry once, without changing source boxes."""
    directions = {0: (1., 0.), 90: (0., -1.), 180: (-1., 0.), 270: (0., 1.)}
    lines = defaultdict(list)
    for group in target.get_text("rawdict", flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES)["blocks"]:
        for line in group.get("lines", []):
            chars = [char for span in line["spans"] for char in span["chars"]]
            text = "".join(char["c"] for char in chars).replace("\u00a0", " ")
            tokens = []
            # These are observed hidden glyph boxes, never interpolated from
            # word lengths or copied from the original PDF's text layer.
            for match in re.finditer(r"\S+", text):
                boxes = [char["bbox"] for char in chars[match.start():match.end()]]
                tokens.append({"text": unicodedata.normalize("NFC", match.group()),
                               "bbox": [min(b[0] for b in boxes), min(b[1] for b in boxes),
                                        max(b[2] for b in boxes), max(b[3] for b in boxes)]})
            lines[text, tuple(line["dir"])].append((list(line["bbox"]), tokens))
    for record in placements:
        if record["status"] != "PLACED_ORIENTATION_HYPOTHESIS":
            continue
        index = record.get("block_index")
        if type(index) is int and 0 <= index < len(blocks):
            block = blocks[index]
        else:
            matches = [block for block in blocks if block.source_id == record["source_id"]]
            if len(matches) != 1:
                continue
            block = matches[0]
        if block.source_id != record["source_id"]:
            continue
        key = block.text.replace("\u00a0", " "), directions[record["crop_rotation_clockwise_degrees"]]
        matches = [(box, tokens) for box, tokens in lines[key] if pymupdf.Rect(block.bbox).contains(pymupdf.Rect(box))]
        if len(matches) == 1:
            box, tokens = matches[0]
            record.update(placement_geometry_method=BRIDGE_LINE_GEOMETRY_METHOD, placed_text_bbox=box,
                          placed_text_sha256=hashlib.sha256(block.text.encode("utf-8")).hexdigest(),
                          placed_token_geometry_method=BRIDGE_TOKEN_GEOMETRY_METHOD, placed_tokens=tokens)


def _bridge_sources(extracted_page):
    """Keep frozen OCR sources removed from body text by heuristic tables."""
    visible = list(extracted_page.blocks)
    sources = [(block, index, None) for index, block in enumerate(visible)]
    visible_ids = {block.source_id for block in visible if block.source_id}
    frozen = getattr(extracted_page, "metadata", {}).get("text_evidence", {}).get("items", [])
    seen = set()
    for index, source in enumerate(frozen):
        sid = source.get("source_id")
        if not isinstance(sid, str) or not sid or sid in seen:
            raise ValueError("Frozen OCR bridge sources require unique source IDs")
        seen.add(sid)
        if source.get("source") == "native":
            raise ValueError("OCR bridge refuses native text")
        if sid not in visible_ids:
            sources.append((TextBlock.model_validate(source), None, index))
    return sources


def unplaced_visible_block_indices(placements, visible_count):
    """Frozen-only sources have no body index; they remain in frozen evidence."""
    return {record["block_index"] for record in placements
            if record["status"] == "KEPT_FOR_REVIEW" and type(record.get("block_index")) is int
            and 0 <= record["block_index"] < visible_count}


def _bridge_insertion_key(indexed_entry):
    """Order rotated lines in their upright reading direction for PDF text extraction."""
    pos, (block, _, _) = indexed_entry
    if is_oriented_line_candidate(block.recognition_provenance) and len(block.bbox) == 4:
        angle = block.recognition_provenance.get("rotation_clockwise_degrees")
        if angle == 90:
            return (1, round(block.bbox[0], 1), -block.bbox[3], pos)
        if angle == 270:
            return (2, -round(block.bbox[2], 1), block.bbox[1], pos)
        if angle == 180:
            return (3, -round(block.bbox[3], 1), -block.bbox[2], pos)
    return (0, float(pos), 0.0, pos)


@contextmanager
def ocr_bridge(source_page, extracted_page, dpi, *, font_path=None, orientation_placements=None, source_coverage=None):
    font_path, font = load_bridge_font(font_path)
    sources = _bridge_sources(extracted_page)
    if source_coverage is not None:
        source_coverage.update(method="visible-and-frozen-ocr-v1", input_count=len(sources),
                               visible_count=len(extracted_page.blocks),
                               frozen_only_source_ids=[block.source_id for block, index, _ in sources if index is None])
    with pymupdf.open() as bridge:
        target = bridge.new_page(width=extracted_page.width, height=extracted_page.height)
        target.insert_image(target.rect, pixmap=source_page.get_pixmap(dpi=dpi, alpha=False))
        target.insert_font(fontname="OCRBridge", fontfile=str(font_path))
        for _, (block, index, evidence_index) in sorted(enumerate(sources), key=_bridge_insertion_key):
            if block.source == "native":
                raise ValueError("OCR bridge refuses native text")
            if is_oriented_line_candidate(block.recognition_provenance):
                reason = _vertical_placement(target, block, font)
                if orientation_placements is not None:
                    record = {
                        "source_id": block.source_id, "block_index": index, "bbox": list(block.bbox),
                        "coordinate_space": "bridge_display_points",
                        "crop_rotation_clockwise_degrees": block.recognition_provenance.get("rotation_clockwise_degrees"),
                        "status": "KEPT_FOR_REVIEW" if reason else "PLACED_ORIENTATION_HYPOTHESIS",
                        "reason": reason or "unverified_recognizer_orientation",
                    }
                    if evidence_index is not None:
                        record["frozen_evidence_index"] = evidence_index
                    orientation_placements.append(record)
                continue
            if len(block.bbox) != 4 or not block.text.strip():
                continue
            x0, y0, x1, y1 = block.bbox
            for line_index, line in enumerate(block.text.splitlines()):
                if not line.strip():
                    continue
                size = min(max(1., (y1-y0)/max(1,len(block.text.splitlines()))*.70),
                           max(1., (x1-x0)/max(.1,font.text_length(line, fontsize=1))))
                target.insert_text((x0, y0+size*(line_index+1)), line, fontname="OCRBridge", fontsize=size, render_mode=3)
        if orientation_placements:
            _record_placed_geometry(target, [block for block, _, _ in sources], orientation_placements)
        yield bridge

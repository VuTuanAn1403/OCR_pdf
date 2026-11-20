"""Generic OCR cell projection with explicit estimated-geometry provenance."""
from __future__ import annotations

from typing import List, Tuple
import re


def _split_trailing_numbers(s: str) -> List[str]:
    parts = s.strip().split()
    cols = []
    idx = len(parts) - 1
    while idx >= 0:
        token = parts[idx]
        if re.match(r"^\(?[0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]+)?\)?$|^\-?$|^[0-9]+(?:,[0-9]+)?%$|^\(?\d+\)?$", token):
            cols.insert(0, token)
            idx -= 1
        else:
            break
    desc = " ".join(parts[:idx + 1])
    if desc.lower() in ("năm", "tháng", "quý", "kỳ", "ngày", "mục", "điều", "khoản", "phần"):
        return [s.strip()]
    return [desc] + cols if desc else cols


def _cell_pieces(text: str, box: List[float]) -> List[Tuple[str, List[float], str]]:
    """Split one OCR line into cell texts, each with a box inside the line's box.

    One piece keeps the line box. Several pieces get the horizontal
    share of their characters in the line: an estimate, so named.
    """
    pieces = _split_trailing_numbers(text)
    line_box = list(box) if box and len(box) >= 4 else []
    if len(pieces) == 1 or not line_box:
        return [(piece, line_box, "SOURCE_LINE") for piece in pieces]
    spans = [m.span() for m in re.finditer(r"\S+", text)]
    width, length = line_box[2] - line_box[0], max(1, len(text))
    out, cursor = [], 0
    for piece in pieces:
        count = len(piece.split())
        start, end = spans[cursor][0], spans[cursor + count - 1][1]
        cursor += count
        out.append((piece, [round(line_box[0] + width * start / length, 2), line_box[1],
                            round(line_box[0] + width * end / length, 2), line_box[3]],
                    "CHAR_SPAN_ESTIMATE"))
    return out

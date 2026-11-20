"""Group physical OCR lines and separate financial table bands."""
from __future__ import annotations

import re
from typing import List
from ocr_contracts.text_block import TextBlock
from .patterns import (FINANCIAL_NUM_RE, HEADER_KEYWORDS_NORM, SECTION_TITLE_RE, ENTITY_BULLET_RE, strip_accents, is_total_line)


def group_rows(blocks, row_y_tolerance):
    sorted_blocks = sorted(blocks, key=lambda b: (b.bbox[1], b.bbox[0]))
    lines: List[List[TextBlock]] = []
    curr_line: List[TextBlock] = []
    curr_y = -999.0

    for b in sorted_blocks:
        y_mid = (b.bbox[1] + b.bbox[3]) / 2.0
        if curr_y < 0 or abs(y_mid - curr_y) <= row_y_tolerance:
            curr_line.append(b)
            curr_y = sum((x.bbox[1] + x.bbox[3]) / 2.0 for x in curr_line) / len(curr_line)
        else:
            curr_line.sort(key=lambda x: x.bbox[0])
            lines.append(curr_line)
            curr_line = [b]
            curr_y = y_mid

    if curr_line:
        curr_line.sort(key=lambda x: x.bbox[0])
        lines.append(curr_line)

    return lines


def segment_table_regions(
    blocks: List[TextBlock],
    page_width: float,
    page_height: float,
    row_y_tolerance: float,
) -> List[List[TextBlock]]:
    """
    Divides page blocks into independent table candidate clusters.
    """
    lines = group_rows(blocks, row_y_tolerance)

    # Identify lines with financial numbers or header characteristics
    segments: List[List[TextBlock]] = []
    current_segment: List[TextBlock] = []
    in_table = False

    for l_idx, line in enumerate(lines):
        line_text = " ".join(b.text.strip() for b in line).strip()
        norm_text = strip_accents(line_text).lower()
        num_tokens = [b for b in line if FINANCIAL_NUM_RE.match(b.text.strip())]
        has_hdr_kw = bool(HEADER_KEYWORDS_NORM.search(norm_text))
        is_sec_title = bool(SECTION_TITLE_RE.match(norm_text))
        is_total = is_total_line(line_text)
        is_footnote = line_text.startswith("(*)") or "tam dung hoat dong" in norm_text
        is_prose = (len(line) == 1 and len(line_text) > 50 and not num_tokens
                    and not ENTITY_BULLET_RE.match(line_text))

        # Break table on section title, footnote note, or explanatory prose
        if in_table and (is_sec_title or is_footnote or is_prose):
            segments.append(current_segment)
            current_segment = []
            in_table = False
            continue

        is_table_data = (
            len(num_tokens) >= 2
            or (len(num_tokens) >= 1 and (ENTITY_BULLET_RE.match(line_text) or is_total))
        )

        is_table_hdr = not is_footnote and (
            (has_hdr_kw and len(line) >= 2)
            or (re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", line_text) and len(line) >= 2)
            or (has_hdr_kw and any(kw in norm_text for kw in ["dia chi", "noi thanh lap", "ty le", "gia goc", "du phong", "hoat dong"]))
        )

        # A new header after observed values starts another table, rather than
        # becoming a data row in a single page-wide financial grid.
        if (in_table and is_table_hdr
                and not any(re.search(r"\d[.,]\d|\d\s*%", b.text) for b in line)
                and any(FINANCIAL_NUM_RE.match(b.text.strip())
                        and re.search(r"\d[.,]\d|\d\s*%", b.text) for b in current_segment)):
            segments.append(current_segment)
            current_segment = []
            in_table = False

        if is_table_data or is_table_hdr:
            if not in_table:
                in_table = True
                current_segment = []
                # Check look-back for preceding section title or date header
                if l_idx > 0:
                    prev_l = lines[l_idx - 1]
                    prev_text = " ".join(b.text.strip() for b in prev_l).strip()
                    prev_norm = strip_accents(prev_text).lower()
                    if (
                        re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", prev_text)
                        or HEADER_KEYWORDS_NORM.search(prev_norm)
                    ) and not (prev_text.startswith("(*)") or is_prose or "nhu sau" in prev_norm):
                        current_segment.extend(prev_l)
            current_segment.extend(line)

            if is_total:
                segments.append(current_segment)
                current_segment = []
                in_table = False
        elif in_table:
            # Text line inside table (e.g. wrapped description or multi-line entity name)
            current_segment.extend(line)

    if current_segment:
        segments.append(current_segment)

    # Filter segments: must have at least 1 numeric line and 3+ blocks
    filtered = []
    for seg in segments:
        has_nums = any(FINANCIAL_NUM_RE.match(b.text.strip()) for b in seg)
        if has_nums and len(seg) >= 4:
            filtered.append(seg)
    return filtered

"""Column boundaries and table-coverage queries for generic OCR routing."""
from __future__ import annotations

from typing import List
from ocr_contracts.table import TableStructure
from ocr_contracts.text_block import TextBlock


def compute_column_boundaries(table_rows: List[List[TextBlock]]) -> List[float]:
    """
    Computes X boundaries separating columns using horizontal projection density valleys.
    """
    all_boxes = [b.bbox for r in table_rows for b in r if b.bbox and len(b.bbox) >= 4]
    if not all_boxes:
        return []

    min_x = int(min(b[0] for b in all_boxes))
    max_x = int(max(b[2] for b in all_boxes))

    # Horizontal projection histogram
    hist = [0] * (max_x + 30)
    for b in all_boxes:
        x0 = max(0, int(b[0]))
        x1 = min(len(hist) - 1, int(b[2]))
        for x in range(x0, x1 + 1):
            hist[x] += 1

    # Determine valley threshold dynamically based on row count
    num_rows = len(table_rows)
    if num_rows <= 3:
        threshold = 0
    else:
        threshold = min(3, max(1, int(num_rows * 0.2)))

    valleys = []
    in_valley = False
    v_start = min_x

    for x in range(min_x + 5, max_x - 5):
        # A column separator channel has low text density
        if hist[x] <= threshold:
            if not in_valley:
                in_valley = True
                v_start = x
        else:
            if in_valley:
                in_valley = False
                if (x - v_start) >= 3:
                    valleys.append((v_start + x - 1) / 2.0)

    # Merge valleys that are too close (< 25 points apart)
    merged_valleys = []
    for v in valleys:
        if not merged_valleys or v - merged_valleys[-1] >= 25.0:
            merged_valleys.append(v)

    return merged_valleys


def is_block_inside_any_table(block_bbox: List[float], tables: List[TableStructure]) -> bool:
    """
    Checks if a text block is located inside a detected table bounding box.
    """
    if not block_bbox or len(block_bbox) < 4 or not tables:
        return False

    bx0, by0, bx1, by1 = block_bbox
    b_mid_x = (bx0 + bx1) / 2.0
    b_mid_y = (by0 + by1) / 2.0

    for t in tables:
        if not t.bbox or len(t.bbox) < 4:
            continue
        tx0, ty0, tx1, ty1 = t.bbox
        if tx0 <= b_mid_x <= tx1 and ty0 <= b_mid_y <= ty1:
            return True

    return False

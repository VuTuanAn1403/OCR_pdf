"""Reconstruct one image-table region from observed OCR blocks."""
from __future__ import annotations

from typing import List, Dict, Optional
from ocr_contracts.table import TableStructure, TableCell
from ocr_contracts.text_block import TextBlock


def extract_table_from_region_blocks(
    engine,
    region_blocks: List[TextBlock],
    page_num: int,
    table_id: str,
    region_bbox: Optional[List[float]] = None
) -> Optional[TableStructure]:
    """
    Reconstructs a table structure from blocks located inside a specific region (e.g. IMAGE_TABLE).
    Requires at least 2 rows and 2 columns of aligned text.
    """
    if not region_blocks or len(region_blocks) < 4:
        return None

    # Sort blocks top-to-bottom, left-to-right
    sorted_blocks = sorted(
        region_blocks,
        key=lambda b: (b.bbox[1] if b.bbox else 0, b.bbox[0] if b.bbox else 0)
    )

    # Group into rows by Y coordinate
    rows: List[List[TextBlock]] = []
    curr_row: List[TextBlock] = []
    curr_y = None

    for b in sorted_blocks:
        if not b.bbox or len(b.bbox) < 4:
            continue
        y_mid = (b.bbox[1] + b.bbox[3]) / 2.0
        if curr_y is None:
            curr_y = y_mid
            curr_row.append(b)
        elif abs(y_mid - curr_y) <= 12.0:
            curr_row.append(b)
        else:
            curr_row.sort(key=lambda x: x.bbox[0])
            rows.append(curr_row)
            curr_row = [b]
            curr_y = y_mid

    if curr_row:
        curr_row.sort(key=lambda x: x.bbox[0])
        rows.append(curr_row)

    if len(rows) < 2:
        return None

    # Compute column boundaries
    col_boundaries = engine._compute_column_boundaries(rows)
    num_cols = len(col_boundaries) + 1 if col_boundaries else max(len(r) for r in rows)
    if num_cols < 2:
        return None

    def get_col_idx(bbox: List[float]) -> int:
        if not col_boundaries or not bbox or len(bbox) < 4:
            return 0
        x_mid = (bbox[0] + bbox[2]) / 2.0
        for idx, bound in enumerate(col_boundaries):
            if x_mid < bound:
                return idx
        return len(col_boundaries)

    # Build cells
    cells: List[TableCell] = []
    for r_idx, r in enumerate(rows):
        col_texts: Dict[int, List[str]] = {}
        col_boxes: Dict[int, List[float]] = {}
        for b in r:
            c_idx = get_col_idx(b.bbox)
            col_texts.setdefault(c_idx, []).append(b.text.strip())
            if c_idx not in col_boxes:
                col_boxes[c_idx] = list(b.bbox)
            else:
                col_boxes[c_idx][0] = min(col_boxes[c_idx][0], b.bbox[0])
                col_boxes[c_idx][1] = min(col_boxes[c_idx][1], b.bbox[1])
                col_boxes[c_idx][2] = max(col_boxes[c_idx][2], b.bbox[2])
                col_boxes[c_idx][3] = max(col_boxes[c_idx][3], b.bbox[3])

        for c_idx in range(num_cols):
            text_val = " ".join(col_texts.get(c_idx, [])).strip()
            cell_box = col_boxes.get(c_idx, [0.0, 0.0, 0.0, 0.0])
            cells.append(TableCell(
                row_idx=r_idx,
                col_idx=c_idx,
                text=text_val,
                bbox=cell_box,
                confidence=0.90,
                        confidence_source="heuristic",
            ))

    all_boxes = [b.bbox for r in rows for b in r if b.bbox]
    min_x = min(box[0] for box in all_boxes)
    min_y = min(box[1] for box in all_boxes)
    max_x = max(box[2] for box in all_boxes)
    max_y = max(box[3] for box in all_boxes)
    final_bbox = region_bbox or [round(min_x, 2), round(min_y, 2), round(max_x, 2), round(max_y, 2)]

    return TableStructure(
        table_id=table_id,
        page=page_num,
        bbox=final_bbox,
        num_rows=len(rows),
        num_cols=num_cols,
        cells=cells,
        confidence=0.88,
                        confidence_source="heuristic",
    )

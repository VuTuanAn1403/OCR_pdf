"""Build spanning header cells and anchored multiline data rows."""
from __future__ import annotations

from typing import List, Tuple
from ocr_contracts.table import TableCell
from ocr_contracts.text_block import TextBlock
from .columns import ColumnBoundary
from .patterns import FINANCIAL_NUM_RE, ENTITY_BULLET_RE, is_total_line


def build_header_cells(
    header_lines: List[List[TextBlock]],
    columns: List[ColumnBoundary],
) -> Tuple[List[TableCell], int]:
    """
    Builds header cells and computes horizontal colspan based on bounding box overlap.
    """
    cells: List[TableCell] = []

    for row_idx, line in enumerate(header_lines):
        for block in line:
            b_x0, b_x1 = block.bbox[0], block.bbox[2]
            b_mid = (b_x0 + b_x1) / 2.0

            overlapped_cols: List[int] = []
            for col in columns:
                if max(col.x0, b_x0) <= min(col.x1, b_x1) or (col.x0 <= b_mid <= col.x1):
                    overlapped_cols.append(col.col_idx)

            if not overlapped_cols:
                closest_col = min(columns, key=lambda c: abs(c.x0 - b_x0))
                overlapped_cols = [closest_col.col_idx]

            min_c = min(overlapped_cols)
            max_c = max(overlapped_cols)
            col_span = max(1, max_c - min_c + 1)

            cells.append(
                TableCell(
                    row_idx=row_idx,
                    col_idx=min_c,
                    row_span=1,
                    col_span=col_span,
                    text=block.text.strip(),
                    bbox=block.bbox,
                    column_header=True,
                )
            )

    return cells, len(header_lines)


def build_data_cells(
    data_lines: List[List[TextBlock]],
    columns: List[ColumnBoundary],
    start_row: int,
) -> Tuple[List[TableCell], int]:
    """
    Builds data cells with anchor-based multi-line wrapping.
    """
    logical_rows: List[List[List[TextBlock]]] = []
    current_logical_row: List[List[TextBlock]] = []

    for line in data_lines:
        line_text = " ".join(b.text.strip() for b in line).strip()
        has_bullet = bool(ENTITY_BULLET_RE.match(line_text))
        has_total = is_total_line(line_text)
        num_tokens = [b for b in line if FINANCIAL_NUM_RE.match(b.text.strip())]

        prev_has_nums = (
            any(FINANCIAL_NUM_RE.match(b.text.strip()) for l in current_logical_row for b in l)
            if current_logical_row else False
        )

        is_new_anchor = (
            has_bullet
            or has_total
            or (len(num_tokens) >= 1 and prev_has_nums)
        )

        if is_new_anchor:
            if current_logical_row:
                logical_rows.append(current_logical_row)
            current_logical_row = [line]
        else:
            if current_logical_row:
                current_logical_row.append(line)
            else:
                current_logical_row = [line]

    if current_logical_row:
        logical_rows.append(current_logical_row)

    cells: List[TableCell] = []

    for l_r_idx, row_lines in enumerate(logical_rows):
        abs_row = start_row + l_r_idx
        all_row_blocks = [b for l in row_lines for b in l]
        grid: List[List[TextBlock]] = [[] for _ in columns]

        for b in all_row_blocks:
            owners = [col for col in columns if col.x0 <= b.bbox[0] and b.bbox[2] <= col.x1]
            if len(owners) != 1:
                return [], 0
            grid[owners[0].col_idx].append(b)

        for col_idx in range(len(columns)):
            cell_blocks = grid[col_idx]
            if cell_blocks:
                cell_blocks.sort(key=lambda x: (x.bbox[1], x.bbox[0]))
                cell_text = " ".join(x.text.strip() for x in cell_blocks).strip()
                c_x0 = min(x.bbox[0] for x in cell_blocks)
                c_y0 = min(x.bbox[1] for x in cell_blocks)
                c_x1 = max(x.bbox[2] for x in cell_blocks)
                c_y1 = max(x.bbox[3] for x in cell_blocks)
                cell_bbox = [c_x0, c_y0, c_x1, c_y1]
            else:
                cell_text = ""
                cell_bbox = []

            cells.append(
                TableCell(
                    row_idx=abs_row,
                    col_idx=col_idx,
                    row_span=1,
                    col_span=1,
                    text=cell_text,
                    bbox=cell_bbox,
                )
            )

    return cells, len(logical_rows)

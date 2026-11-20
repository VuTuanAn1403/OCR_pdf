"""Read vector tables for explicitly permitted legacy callers."""
from __future__ import annotations

from typing import List, Any, Optional
import re
import pymupdf
from ocr_contracts.table import TableStructure, TableCell


def extract_vector_tables(engine, page: pymupdf.Page, page_num: int) -> List[TableStructure]:
    tables: List[TableStructure] = []
    try:
        drawings = page.get_drawings()
        if not drawings:
            return []
        tab_finder = page.find_tables()
        raw_tabs = list(tab_finder.tables)
        if not raw_tabs:
            return []

        # Sort tables geometrically in top-to-bottom reading order
        sorted_tabs = sorted(raw_tabs, key=lambda t: (float(t.bbox[1]), float(t.bbox[0])))

        for idx, tab in enumerate(sorted_tabs):
            t = engine._extract_single_vector_table(page, tab, page_num, idx + 1, raw_tabs)
            if t:
                tables.append(t)
    except Exception:
        pass

    return tables


def extract_single_vector_table(
    engine,
    page: pymupdf.Page,
    tab: Any,
    page_num: int,
    idx: int,
    all_tabs: List[Any],
) -> Optional[TableStructure]:
    import re
    header_cells = [c for c in tab.rows[0].cells if c]
    if len(header_cells) < 2:
        return None
    col_spans = [(c[0], c[2]) for c in header_cells]
    num_cols = len(col_spans)

    t_x0, t_y0, t_x1, t_y1 = [float(c) for c in tab.bbox]

    # Require vector table to have at least 2 explicit rows with vector borders.
    # If tab.row_count < 2, PyMuPDF only found a loose outline and cannot reliably
    # split borderless multi-line data rows, which leads to collapsed 1-row tables.
    # Delegate borderless tables to structured text block detection instead.
    if tab.row_count < 2:
        return None

    # Extract all words inside [t_x0, t_y0, t_x1, t_y1]
    all_words = page.get_text("words", clip=pymupdf.Rect(t_x0 - 5.0, t_y0, t_x1 + 5.0, t_y1))
    if not all_words:
        return None

    lines = []
    for w in sorted(all_words, key=lambda x: (x[1], x[0])):
        y_mid = (w[1] + w[3]) / 2.0
        matched = None
        for cl in lines:
            cl_y = sum((x[1] + x[3]) / 2.0 for x in cl) / len(cl)
            if abs(y_mid - cl_y) <= 8.0:
                matched = cl
                break
        if matched is not None:
            matched.append(w)
        else:
            lines.append([w])

    if len(lines) < 2:
        return None

    logical_rows = []
    header_names = tab.header.names if tab.header else [f"Col {i}" for i in range(num_cols)]
    clean_headers = [re.sub(r"\s+", " ", h or "").strip() for h in header_names]
    while len(clean_headers) < num_cols:
        clean_headers.append("")
    logical_rows.append(clean_headers[:num_cols])

    h_bottom = tab.rows[0].bbox[3]
    curr_data_row = None

    for line in lines:
        line_words = sorted(line, key=lambda x: x[0])
        line_y0 = min(w[1] for w in line_words)
        if line_y0 < h_bottom - 2.0:
            continue

        cols = [""] * num_cols
        for w in line_words:
            w_mid = (w[0] + w[2]) / 2.0
            best_c = 0
            for ci, (cx0, cx1) in enumerate(col_spans):
                if cx0 - 8 <= w_mid <= cx1 + 8:
                    best_c = ci
                    break
            cols[best_c] = (cols[best_c] + " " + w[4]).strip()

        first_col = cols[0].strip()
        is_new_row = bool(first_col and re.match(r"^(\d+|[A-Z]|Tổng|Cộng)\b", first_col, re.IGNORECASE))
        if is_new_row or curr_data_row is None:
            curr_data_row = cols
            logical_rows.append(curr_data_row)
        else:
            for ci in range(num_cols):
                if cols[ci]:
                    curr_data_row[ci] = (curr_data_row[ci] + " " + cols[ci]).strip()

    if len(logical_rows) < 2:
        return None

    cells = []
    for r_idx, r in enumerate(logical_rows):
        for c_idx, val in enumerate(r):
            if val:
                cells.append(TableCell(
                    row_idx=r_idx,
                    col_idx=c_idx,
                    text=val,
                    bbox=[round(t_x0, 2), round(t_y0, 2), round(t_x1, 2), round(t_y1, 2)]
                ))

    return TableStructure(
        table_id=f"page_{page_num}_vector_table_{idx}",
        page=page_num,
        bbox=[round(t_x0, 2), round(t_y0, 2), round(t_x1, 2), round(t_y1, 2)],
        num_rows=len(logical_rows),
        num_cols=num_cols,
        cells=cells,
        confidence=0.95,
                        confidence_source="heuristic",
    )

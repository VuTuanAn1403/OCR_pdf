"""Detect generic OCR tables while retaining existing rejection and review semantics."""
from __future__ import annotations

from typing import List, Dict, Any, Optional, Tuple
import re
from ocr_contracts.table import TableStructure, TableCell
from ocr_contracts.text_block import TextBlock
from .patterns import looks_like_document_index
from .ocr_cells import _cell_pieces


def detect_table_from_ocr_blocks(
    engine,
    blocks: List[TextBlock],
    page_num: int,
    table_id: Optional[str] = None,
    document_index_page: bool = False,
    rejections: Optional[List[Dict[str, Any]]] = None,
) -> Optional[TableStructure]:
    if not blocks or len(blocks) < 5:
        return None

    # 1. Group blocks into horizontal lines (rows)
    sorted_blocks = sorted(blocks, key=lambda b: (b.bbox[1] if b.bbox else 0, b.bbox[0] if b.bbox else 0))
    rows = []
    curr_row = []
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

    # 2. Iterate through candidate header rows until a valid table is found
    candidate_header_indices = [r_idx for r_idx, r in enumerate(rows) if engine._is_table_header_line(r)]
    if not candidate_header_indices:
        return None

    for raw_header_idx in candidate_header_indices:
        header_idx = raw_header_idx
        # Check upward 1-5 rows above header_idx for parent header lines (e.g. "Nguyên giá", "Tài sản cố định", "Giá trị còn lại") or earlier aligned rows
        scan_up_limit = max(0, header_idx - 5)
        for cand_idx in range(header_idx - 1, scan_up_limit - 1, -1):
            cand_row = rows[cand_idx]
            cand_text = " ".join(b.text.lower() for b in cand_row).strip()
            if cand_text.endswith(":") or cand_text.startswith(("triển khai", "căn cứ", "kết quả", "theo", "chúng tôi", "ngày", "lưu ý", "ghi chú")):
                break
            is_parent_hdr = any(kw in cand_text for kw in ("nguyên giá", "tài sản cố định", "giá trị còn lại", "hao mòn", "khoản mục", "chỉ tiêu", "tổng", "tài sản", "nguồn vốn", "đầu tư"))

            # Also check if cand_row is an earlier data row of the same table
            ref_row = rows[header_idx]
            is_aligned_row = False
            if len(ref_row) >= 2 and cand_row:
                cand_aligned_blocks = [b for b in cand_row if b.bbox and any(abs(b.bbox[0] - rb.bbox[0]) <= 25.0 for rb in ref_row if rb.bbox)]
                if cand_aligned_blocks and not cand_text.endswith("."):
                    is_aligned_row = True

            if is_parent_hdr or is_aligned_row:
                y_gap = rows[cand_idx + 1][0].bbox[1] - cand_row[-1].bbox[3] if (rows[cand_idx + 1][0].bbox and cand_row[-1].bbox) else 999
                if y_gap <= 30.0:
                    header_idx = cand_idx
                else:
                    break
            else:
                break

        # 3. Collect following table rows
        import re
        table_rows = [rows[header_idx]]
        for r_idx in range(header_idx + 1, len(rows)):
            r = rows[r_idx]
            full_line = " ".join(b.text.strip() for b in r).strip()
            full_line_lower = full_line.lower()

            # Break if footer signatures or report metadata are reached
            if any(term in full_line_lower for term in ("báo cáo thường niên", "theo giấy chứng nhận")):
                break
            if len(r) == 1 and any(term in full_line_lower for term in ("kế toán trưởng", "người lập biểu")):
                w_single = (r[0].bbox[2] - r[0].bbox[0]) if r[0].bbox else 0
                if w_single > 180 or len(full_line) > 35:
                    break

            is_pure_numeric = bool(re.match(r"^[\d\s\.\,\(\)\-\%NAna\/]+$", full_line))
            has_financial_num = bool(re.search(r"\d+[\.,]\d+", full_line))

            # Break if a new section heading or subsection is encountered (must not be numeric!)
            if not is_pure_numeric and re.match(r"^\s*(\d{1,2}(\.\d{1,2}){0,2}\s*[\)\.\/\-]|[IVXLCDM]+\s*[\)\.\/\-]|phần\s+[IVXLCDM]+)\s+\D", full_line, re.IGNORECASE):
                break

            # Break if line is a narrative paragraph following the table
            if len(r) == 1 and not is_pure_numeric and not has_financial_num:
                w = (r[0].bbox[2] - r[0].bbox[0]) if r[0].bbox else 0
                if w > 250 or len(full_line) > 50 or full_line.startswith(("Kết quả", "Ghi chú:", "Lưu ý:", "Theo ")):
                    break

            # Break if line enters a chart region (e.g. single number tick marks forming vertical chart axis)
            if len(r) == 1 and re.match(r"^\d{1,3}([.,]\d{3})*([.,]\d+)?$", full_line):
                w = (r[0].bbox[2] - r[0].bbox[0]) if r[0].bbox else 0
                if w < 70:
                    break

            # Check if this row is the summary/total row "Cộng" / "Tổng cộng" (avoid breaking on indicators like "Tổng tài sản")
            is_total_row = bool(
                re.match(r"^\s*([0o\*\-\•]\s+)?(tổng\s+cộng|total|cộng\b\s*$|cộng\s*\(|tổng\s+nợ\s+phải\s+trả)", full_line_lower)
                and not any(k in full_line_lower for k in ("tài sản", "chi phí", "doanh thu", "vốn", "số lượng", "nhân sự", "lao động", "nguồn"))
            )

            table_rows.append(r)

            if is_total_row:
                if re.search(r"tổng\s+nợ\s+phải\s+trả|tổng\s+cộng\b", full_line_lower):
                    break
                has_more_data = False
                if r_idx + 1 < len(rows):
                    next_r = rows[r_idx + 1]
                    if any(re.search(r"\d+[\.,]\d+", b.text) for b in next_r) or len(next_r) >= 2:
                        has_more_data = True
                if not has_more_data:
                    break

        # Allow 1-row table if 3+ columns, otherwise at least 2 rows
        min_rows = 1 if (len(table_rows) > 0 and len(table_rows[0]) >= 3) else 2
        if len(table_rows) < min_rows:
            continue

        # Filter outlier blocks on the far left (e.g. sidebar title blocks)
        if len(table_rows) >= 3:
            cand_x0s = [r[0].bbox[0] for r in table_rows if r and r[0].bbox]
            if cand_x0s:
                import statistics
                med_x0 = statistics.median(cand_x0s)
                cleaned_table_rows = []
                for r in table_rows:
                    cleaned_r = [b for b in r if b.bbox and b.bbox[2] >= med_x0 - 30.0]
                    if cleaned_r:
                        cleaned_table_rows.append(cleaned_r)
                if cleaned_table_rows:
                    table_rows = cleaned_table_rows

        all_boxes = [b.bbox for r in table_rows for b in r if b.bbox]
        min_x = min(box[0] for box in all_boxes)
        min_y = min(box[1] for box in all_boxes)
        max_x = max(box[2] for box in all_boxes)
        max_y = max(box[3] for box in all_boxes)
        t_bbox = [round(min_x, 2), round(min_y, 2), round(max_x, 2), round(max_y, 2)]

        # Helper to split trailing numbers from a text block


        # 4. Extract logical cells row by row
        cells: List[TableCell] = []
        bbox_methods: Dict[int, str] = {}
        max_col_found = 0

        # V8-080: a cell keeps the geometry of the OCR line it came from,
        # cell text is never rewritten, and a token left out of the grid
        # is recorded rather than dropped silently.
        stripped_tokens: List[Dict[str, Any]] = []
        for r_idx, r in enumerate(table_rows):
            r_sorted = sorted(r, key=lambda b: (b.bbox[0] if b.bbox else 0, b.bbox[1] if b.bbox else 0))
            row_items: List[Tuple[str, List[float], str]] = []
            for b in r_sorted:
                txt = b.text.strip()
                if not txt:
                    continue
                row_items.extend(_cell_pieces(txt, b.bbox))

            # Leading OCR bullet artifacts stay out of the grid but on record.
            while row_items and row_items[0][0] in ("O", "0", "e", "•", "-"):
                text, box, _ = row_items.pop(0)
                stripped_tokens.append({"row_idx": r_idx, "text": text, "bbox": box,
                                        "reason": "LEADING_BULLET_ARTIFACT",
                                        "numeric_like": text in ("0", "-")})

            for c_idx, (cell_text, cell_box, method) in enumerate(row_items):
                if cell_text:
                    cells.append(TableCell(
                        row_idx=r_idx,
                        col_idx=c_idx,
                        text=cell_text,
                        bbox=cell_box,
                        confidence=0.95,
                        confidence_source="heuristic",
                    ))
                    bbox_methods[id(cells[-1])] = method
                    if c_idx > max_col_found:
                        max_col_found = c_idx

        texts_before_merge = {id(cell): cell.text for cell in cells}
        cells = engine._merge_wrapped_numeric_rows(cells)
        if engine._looks_like_signature_block(cells):
            continue
        if document_index_page and looks_like_document_index(cells):
            # Later header candidates retry sub-ranges of the same rows;
            # record each contents area once.
            if rejections is not None and not any(
                    r["bbox"][0] <= t_bbox[0] and r["bbox"][1] <= t_bbox[1]
                    and r["bbox"][2] >= t_bbox[2] and r["bbox"][3] >= t_bbox[3] for r in rejections):
                rejections.append({"bbox": t_bbox, "reason": "document_index_layout",
                                   "rows": len(table_rows), "method": "contents-title-page-refs-v1"})
            continue
        num_rows = max((cell.row_idx for cell in cells), default=-1) + 1
        num_cols = max_col_found + 1
        if num_cols < 2 and len(table_rows) < 2:
            continue

        # Guard: Table must have at least 2 active columns populated with text
        active_cols = set(c.col_idx for c in cells if c.text.strip())
        if len(active_cols) < 2:
            continue

        # Guard: Check if detected table is actually a multi-column narrative paragraph
        data_cells = [c for c in cells if c.row_idx > 0]
        if data_cells:
            long_cells = sum(1 for c in data_cells if len(c.text.strip()) > 35)
            if (long_cells / len(data_cells)) > 0.40:
                continue

            long_narrative_cells = sum(1 for c in data_cells if len(c.text.strip()) > 45 and any(p in c.text.lower() for p in ("công ty", "người lao động", "thực hiện", "nghị định", "quy định", "theo đó", "kết quả", "chăm sóc", "đảm bảo", "duy trì", "thận trọng")))
            if long_narrative_cells >= 2 and (long_narrative_cells / len(data_cells)) >= 0.15:
                continue

            # Guard: Check if data rows only populate 1-2 columns while header had many columns (chart axis mismatch)
            active_data_cols = len(set(c.col_idx for c in data_cells))
            if num_cols >= 4 and active_data_cols <= 2 and long_narrative_cells >= 1:
                continue

        # Guard: Check if detected table is an equidistant linear chart axis scale (e.g. 60.0, 55.0, 50.0...)
        if len(cells) >= 4 and max_col_found <= 2:
            col_vals = [c.text.strip() for c in cells if c.col_idx == 0 and re.match(r"^\d+([.,]\d+)?$", c.text.strip())]
            if len(col_vals) >= 4:
                try:
                    nums = [float(v.replace(",", ".")) for v in col_vals]
                    diffs = [round(abs(nums[i] - nums[i+1]), 2) for i in range(len(nums)-1)]
                    if len(set(diffs)) <= 1:
                        continue
                except ValueError:
                    pass

        tid = table_id or f"page_{page_num}_scanned_table"
        cell_geometry = [{"row_idx": cell.row_idx, "col_idx": cell.col_idx,
                          "bbox_method": ("MERGED_WRAPPED_LABEL" if cell.text != texts_before_merge[id(cell)]
                                          else bbox_methods[id(cell)])} for cell in cells]
        numeric_stripped = any(token["numeric_like"] for token in stripped_tokens)
        return TableStructure(
            table_id=tid,
            page=page_num,
            bbox=t_bbox,
            num_rows=num_rows,
            num_cols=num_cols,
            cells=cells,
            confidence=0.92,
            confidence_source="heuristic",
            # A "0" or "-" left out of the grid may be a value, not a bullet.
            needs_review=numeric_stripped,
            provenance={"heuristic_cells": {
                "method": "heuristic-source-line-cells-v1",
                "cell_geometry": cell_geometry,
                "stripped_tokens": stripped_tokens,
                "stripped_tokens_row_space": "before_wrapped_row_merge",
                **({"review_reason": "NUMERIC_LIKE_TOKEN_STRIPPED"} if numeric_stripped else {}),
            }},
        )

    return None

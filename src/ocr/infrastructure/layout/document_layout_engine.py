from typing import List, Dict, Any, Optional
import pymupdf
from src.ocr.domain.models.table import TableStructure, TableCell
from src.ocr.domain.models.text_block import TextBlock

TABLE_HEADER_KEYWORDS = [
    "stt", "chỉ tiêu", "tên công trình", "đv tính", "đơn vị tính", "đơn vị",
    "kế hoạch", "thực hiện", "tỷ lệ", "quy mô", "tổng mức",
    "giá trị", "đầu tư", "ghi chú", "số lượng", "thành tiền"
]

class DocumentLayoutEngine:
    """
    Analyzes document layout to detect genuine tables with explicit row/column boundaries.
    Prevents false positive tables on standard text/paragraph pages.
    """
    def __init__(self):
        pass

    def extract_tables(
        self,
        page: pymupdf.Page,
        blocks: List[TextBlock],
        page_num: int
    ) -> List[TableStructure]:
        """
        Extracts genuine tables from page:
        1. Checks vector-defined tables from PyMuPDF.
        2. If none, checks structured OCR blocks using geometric alignment and table keywords.
        """
        # 1. Vector tables
        vector_tables = self._extract_vector_tables(page, page_num)
        if vector_tables:
            return vector_tables

        # 2. Scanned / OCR aligned table detection
        ocr_table = self._detect_table_from_ocr_blocks(blocks, page_num)
        if ocr_table:
            return [ocr_table]

        return []

    def _extract_vector_tables(self, page: pymupdf.Page, page_num: int) -> List[TableStructure]:
        tables: List[TableStructure] = []
        try:
            tab_finder = page.find_tables()
            for idx, tab in enumerate(tab_finder.tables):
                t_bbox = [round(c, 2) for c in tab.bbox]
                df_data = tab.extract()
                if not df_data or len(df_data) < 2:
                    continue

                num_rows = len(df_data)
                num_cols = len(df_data[0]) if num_rows > 0 else 0
                if num_cols < 2:
                    continue

                non_empty = sum(1 for row in df_data for val in row if val and str(val).strip())
                if non_empty < 4:
                    continue

                cells: List[TableCell] = []
                for r_idx, row in enumerate(df_data):
                    for c_idx, val in enumerate(row):
                        cell_text = str(val or "").strip()
                        cells.append(TableCell(
                            row_idx=r_idx,
                            col_idx=c_idx,
                            text=cell_text,
                            bbox=t_bbox,
                            confidence=1.0
                        ))

                tables.append(TableStructure(
                    table_id=f"page_{page_num}_vector_table_{idx+1}",
                    page=page_num,
                    bbox=t_bbox,
                    num_rows=num_rows,
                    num_cols=num_cols,
                    cells=cells,
                    confidence=0.95
                ))
        except Exception:
            pass

        return tables

    def _detect_table_from_ocr_blocks(self, blocks: List[TextBlock], page_num: int) -> Optional[TableStructure]:
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

        # 2. Find a row containing table header keywords and multiple columns
        header_idx = -1
        for r_idx, r in enumerate(rows):
            row_text = " ".join(b.text.lower() for b in r)
            matches = sum(1 for kw in TABLE_HEADER_KEYWORDS if kw in row_text)
            if matches >= 2 and len(r) >= 2:
                header_idx = r_idx
                break

        if header_idx == -1:
            return None

        # Check if the row immediately preceding header_idx contains top header fragments (e.g. "Tổng", "Giá trị", "Đã")
        # Must NOT be a narrative sentence (e.g. ending in ':') and must NOT be full width narrative text
        if header_idx > 0:
            prev_row = rows[header_idx - 1]
            prev_text = " ".join(b.text.lower() for b in prev_row).strip()
            if not prev_text.endswith(":") and not prev_text.startswith(("triển khai", "căn cứ", "kết quả", "theo")):
                w_prev = max((b.bbox[2] - b.bbox[0]) for b in prev_row if b.bbox) if prev_row else 0
                if len(prev_row) >= 2 and w_prev < 150:
                    if any(kw in prev_text for kw in ("tổng", "giá trị", "đã", "mức", "khối lượng", "đầu tư")):
                        if rows[header_idx][0].bbox and prev_row[0].bbox and rows[header_idx][0].bbox[1] - prev_row[0].bbox[1] <= 20.0:
                            header_idx = header_idx - 1

        # 3. Collect following table rows
        import re
        table_rows = [rows[header_idx]]
        for r_idx in range(header_idx + 1, len(rows)):
            r = rows[r_idx]
            full_line = " ".join(b.text.strip() for b in r).strip()
            full_line_lower = full_line.lower()

            # Break if footer signatures or report metadata are reached
            if any(term in full_line_lower for term in ("báo cáo thường niên", "công ty cổ phần", "theo giấy chứng nhận")):
                break

            # Break if a new section heading or subsection is encountered (e.g. "3.2/-", "2)", "I/")
            if re.match(r"^\s*(\d+(\.\d+)*\s*[\)\.\/\-]|[IVXLCDM]+\s*[\)\.\/\-]|phần\s+[IVXLCDM]+)", full_line, re.IGNORECASE):
                break

            # Break if line is a narrative paragraph following the table
            if len(r) == 1:
                w = (r[0].bbox[2] - r[0].bbox[0]) if r[0].bbox else 0
                if w > 250 or len(full_line) > 50 or full_line.startswith(("Kết quả", "Ghi chú:", "Lưu ý:", "Theo ")):
                    break

            # Check if this row is the summary/total row "Cộng" / "Tổng cộng"
            is_total_row = bool(re.match(r"^\s*(cộng|tổng\s+cộng|tổng)\b", full_line_lower))

            table_rows.append(r)

            if is_total_row:
                # Table ends after the total row
                break

        if len(table_rows) < 3:
            return None

        # 4. Build column grid using geometric X boundaries
        col_boundaries = self._compute_column_boundaries(table_rows)
        num_cols = len(col_boundaries) + 1 if col_boundaries else max(len(r) for r in table_rows)
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

        all_boxes = [b.bbox for r in table_rows for b in r if b.bbox]
        min_x = min(box[0] for box in all_boxes)
        min_y = min(box[1] for box in all_boxes)
        max_x = max(box[2] for box in all_boxes)
        max_y = max(box[3] for box in all_boxes)
        t_bbox = [round(min_x, 2), round(min_y, 2), round(max_x, 2), round(max_y, 2)]

        # 5. Build logical rows by merging multi-line continuation rows
        stt_regex = re.compile(r"^([0-9]+|[A-Z]|cộng|tổng|tổng\s+cộng)$", re.IGNORECASE)
        logical_rows_data: List[Dict[int, str]] = []
        logical_bboxes: List[List[float]] = []
        in_data_zone = False

        for r_idx, r in enumerate(table_rows):
            r_sorted = sorted(r, key=lambda b: b.bbox[0] if b.bbox else 0)
            line_col_text: Dict[int, str] = {}
            for b in r_sorted:
                c_idx = get_col_idx(b.bbox) if col_boundaries else min(r_sorted.index(b), num_cols - 1)
                txt = b.text.strip()
                if not txt:
                    continue
                # Clean pipe diameter OCR artifacts: P180 HDPE -> D180 HDPE
                txt = re.sub(r"\b[Pp](\d{2,4}\s*HDPE)\b", r"D\1", txt)
                txt = re.sub(r"\b12(\d{2,3}\s*HDPE)\b", r"D\1", txt)
                if c_idx in line_col_text:
                    line_col_text[c_idx] += " " + txt
                else:
                    line_col_text[c_idx] = txt

            col0 = line_col_text.get(0, "").strip()
            col1 = line_col_text.get(1, "").strip()

            # Check if this line is data (has data numbers, STT marker, or no header keywords)
            has_data_number = any(re.search(r"\b\d+[\.,]\d+\b", txt) for txt in line_col_text.values())
            has_stt = bool(col0 and stt_regex.match(col0)) or bool(re.match(r"^\d+\s+[A-ZÀ-Ỹ]", col1))
            is_header_keywords = any(kw in " ".join(line_col_text.values()).lower() for kw in TABLE_HEADER_KEYWORDS)

            # Detect transition from multi-line header to data rows
            if not in_data_zone:
                if has_stt or has_data_number or (r_idx > 0 and not is_header_keywords):
                    in_data_zone = True
                elif len(logical_rows_data) == 0:
                    logical_rows_data.append(dict(line_col_text))
                    logical_bboxes.append(r[0].bbox if r else [])
                    continue
                else:
                    # Merge additional header line into header row (row 0)
                    header = logical_rows_data[0]
                    for c, txt in line_col_text.items():
                        if txt:
                            header[c] = (header.get(c, "") + " " + txt).strip()
                    continue

            # In data zone: decide if this line starts a new logical row
            is_new = False
            if col0 and stt_regex.match(col0):
                is_new = True
            elif col1.lower() in ["cộng", "tổng cộng", "tổng"] or col1.lower().startswith("cộng "):
                is_new = True
            elif logical_rows_data:
                prev = logical_rows_data[-1]
                # Same numeric column collision (prevent separate items from merging)
                c3_col = bool(re.search(r"\d+", prev.get(3, ""))) and bool(re.search(r"\d+", line_col_text.get(3, "")))
                c4_col = bool(re.search(r"\d+", prev.get(4, ""))) and bool(re.search(r"\d+", line_col_text.get(4, "")))
                if c3_col or c4_col:
                    is_new = True

            if is_new or len(logical_rows_data) <= 1:
                logical_rows_data.append(dict(line_col_text))
                logical_bboxes.append(r[0].bbox if r else [])
            else:
                # Continuation line -> merge with previous logical row
                prev = logical_rows_data[-1]
                for c, txt in line_col_text.items():
                    if txt:
                        prev[c] = (prev.get(c, "") + " " + txt).strip()

        # Build TableCell objects from merged logical rows
        cells: List[TableCell] = []
        for r_idx, row_dict in enumerate(logical_rows_data):
            for c_idx, cell_text in row_dict.items():
                if cell_text:
                    cells.append(TableCell(
                        row_idx=r_idx,
                        col_idx=c_idx,
                        text=cell_text,
                        bbox=logical_bboxes[r_idx] if r_idx < len(logical_bboxes) else [],
                        confidence=0.95
                    ))

        return TableStructure(
            table_id=f"page_{page_num}_scanned_table",
            page=page_num,
            bbox=t_bbox,
            num_rows=len(logical_rows_data),
            num_cols=num_cols,
            cells=cells,
            confidence=0.92
        )

    @staticmethod
    def _compute_column_boundaries(table_rows: List[List[TextBlock]]) -> List[float]:
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

        valleys = []
        in_valley = False
        v_start = min_x

        for x in range(min_x + 10, max_x - 10):
            # A column separator channel typically has very low text density (<= 3 overlapping lines)
            if hist[x] <= 3:
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

    @staticmethod
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

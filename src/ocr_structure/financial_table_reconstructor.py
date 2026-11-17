"""
Deterministic Geometric Reconstructor for Financial Statement (BCTC) Tables.

Recovers multi-column borderless tables, multi-level spanning headers,
and wrapped entity descriptions using geometric coordinate projection
and right-aligned numeric clustering without relying on computer-vision models.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ocr_contracts.table import TableCell, TableStructure
from ocr_contracts.text_block import TextBlock


def strip_accents(text: str) -> str:
    """Removes Vietnamese accents and diacritics for robust keyword matching."""
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn").replace("đ", "d").replace("Đ", "D")


# Regular expression matching financial tokens:
# 1. Formatted amounts: 2.974.645.800, 1.000.000.000, 415.591.564, 12,345.67
# 2. Parenthesized negative numbers: (415.591.564)
# 3. Percentages: 38,39%, 0,68%, 100%, %688
# 4. Dates: 30/06/2025, 01/07/2024, 31/12/2024
# 5. Financial nil / dash: -, –, —, N/A
FINANCIAL_NUM_RE = re.compile(
    r"^\(?[0-9]{1,3}(?:[.,][0-9]{3})+(?:[.,][0-9]+)?\)?$|"
    r"^\(?\d+(?:[.,]\d+)?\s*%\)?$|"
    r"^%?\d{2,3}%?$|"
    r"^\(?\d{1,2}/\d{1,2}/\d{4}\)?$|"
    r"^[-–—]$|"
    r"^\b(?:N/A|NA|-)\b$",
    re.IGNORECASE,
)

# Detect if a normalized line or token contains header terminology
HEADER_KEYWORDS_NORM = re.compile(
    r"\b(chi tieu|noi dung|thuyet minh|ma so|tai san|nguon von|gia goc|gia tri|ghi so|du phong|"
    r"ty le|tyle|loi ich|quyen|bieu quyet|dia chi|noi thanh lap|hoat dong|kinh doanh|"
    r"so cuoi|so dau|dau nam|cuoi nam|vnd|usd|stt|nam|ky|ngay|ten cong ty|ten don vi|"
    r"phuong phap von|chu so huu|bieu quyet)\b",
    re.IGNORECASE,
)

ENTITY_BULLET_RE = re.compile(
    r"^\s*([0o\*\-\•\–\—\+]\s+|\d{1,2}\.\s+|[a-z]\)\s+|\([ivxlcdm]+\)\s+|[IVXLCDM]+\.\s+)",
    re.IGNORECASE,
)

TOTAL_ROW_RE = re.compile(
    r"\b(tong cong|tong so|tong no|tong tai san|total)\b",
    re.IGNORECASE,
)

SECTION_TITLE_RE = re.compile(
    r"^\s*([a-z]\)\s+|\d{1,2}\.\s+[A-Za-zÀ-ỹ]|\bphan\b|\bmuc\b)",
    re.IGNORECASE,
)


def is_total_line(line_text: str) -> bool:
    norm = strip_accents(line_text).lower()
    if "cong ty" in norm or "don vi" in norm:
        return False
    if TOTAL_ROW_RE.search(norm):
        return True
    if re.match(r"^\s*cong\s*:?$", norm):
        return True
    return False


@dataclass
class ColumnBoundary:
    col_idx: int
    x0: float
    x1: float
    right_anchor: Optional[float] = None
    is_numeric: bool = False
    title: str = ""


class FinancialTableReconstructor:
    """
    Recovers structured tables from borderless financial report pages.
    """

    def __init__(
        self,
        col_cluster_tolerance: float = 8.0,
        row_y_tolerance: float = 6.0,
        min_numeric_cols: int = 1,
    ):
        self.col_cluster_tolerance = col_cluster_tolerance
        self.row_y_tolerance = row_y_tolerance
        self.min_numeric_cols = min_numeric_cols

    def reconstruct_tables(
        self,
        blocks: List[TextBlock],
        page_width: float,
        page_height: float,
        page_num: int = 1,
    ) -> List[TableStructure]:
        """
        Discovers and reconstructs all financial tables on the page.
        """
        if not blocks or len(blocks) < 4:
            return []

        # Filter out page header metadata (e.g. at very top y < 55)
        # and footer page numbers (solitary digits near bottom)
        body_blocks = []
        for b in blocks:
            if not b.bbox or len(b.bbox) != 4 or not b.text.strip():
                continue
            # Footer page number filter
            if b.text.strip().isdigit() and b.bbox[1] > 0.88 * page_height and abs((b.bbox[0] + b.bbox[2]) / 2.0 - page_width / 2.0) < 150:
                continue
            # Top document title / metadata header
            if b.bbox[3] < 55.0:
                continue
            body_blocks.append(b)

        if not body_blocks:
            return []

        # Segment page into candidate table bands
        candidate_segments = self._segment_table_regions(body_blocks, page_width, page_height)
        tables: List[TableStructure] = []

        for seg_idx, seg_blocks in enumerate(candidate_segments):
            table = self._build_table_from_segment(seg_blocks, page_num, seg_idx + 1)
            if table is not None:
                tables.append(table)

        return tables

    def _segment_table_regions(
        self,
        blocks: List[TextBlock],
        page_width: float,
        page_height: float,
    ) -> List[List[TextBlock]]:
        """
        Divides page blocks into independent table candidate clusters.
        """
        sorted_blocks = sorted(blocks, key=lambda b: (b.bbox[1], b.bbox[0]))
        lines: List[List[TextBlock]] = []
        curr_line: List[TextBlock] = []
        curr_y = -999.0

        for b in sorted_blocks:
            y_mid = (b.bbox[1] + b.bbox[3]) / 2.0
            if curr_y < 0 or abs(y_mid - curr_y) <= self.row_y_tolerance:
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
            is_prose = len(line) == 1 and len(line_text) > 50 and not num_tokens

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

    def _build_table_from_segment(
        self,
        blocks: List[TextBlock],
        page_num: int,
        table_idx: int,
    ) -> Optional[TableStructure]:
        """
        Builds a structured TableStructure from a cohesive cluster of blocks.
        """
        # Separate lines inside segment
        sorted_blocks = sorted(blocks, key=lambda b: (b.bbox[1], b.bbox[0]))
        lines: List[List[TextBlock]] = []
        curr_line: List[TextBlock] = []
        curr_y = -999.0

        for b in sorted_blocks:
            y_mid = (b.bbox[1] + b.bbox[3]) / 2.0
            if curr_y < 0 or abs(y_mid - curr_y) <= self.row_y_tolerance:
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

        # Identify header lines vs data lines
        first_data_idx = -1
        for idx, l in enumerate(lines):
            nums = [b for b in l if FINANCIAL_NUM_RE.match(b.text.strip())]
            has_dates_only = all(re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", b.text) for b in nums) if nums else False

            if nums and not has_dates_only:
                if l[0].bbox[0] < 380.0:
                    first_data_idx = idx
                    break

        if first_data_idx == -1:
            for idx, l in enumerate(lines):
                line_text = " ".join(b.text.strip() for b in l)
                if ENTITY_BULLET_RE.match(line_text) or "cong ty" in strip_accents(line_text).lower():
                    first_data_idx = idx
                    break

        if first_data_idx <= 0:
            first_data_idx = 1 if len(lines) > 1 else 0

        header_lines = lines[:first_data_idx]
        data_lines = lines[first_data_idx:]

        # Discover columns
        numeric_blocks = [
            b for b in blocks
            if FINANCIAL_NUM_RE.match(b.text.strip()) and not re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", b.text.strip())
        ]
        columns = self._discover_columns(blocks, numeric_blocks, header_lines)
        if len(columns) < 2:
            return None

        t_x0 = min(b.bbox[0] for b in blocks)
        t_y0 = min(b.bbox[1] for b in blocks)
        t_x1 = max(b.bbox[2] for b in blocks)
        t_y1 = max(b.bbox[3] for b in blocks)

        # 4. Build Header Cells (including Spanning Colspans)
        header_cells, num_header_rows = self._build_header_cells(header_lines, columns)

        # 5. Build Data Cells (with Anchor-based multi-line text wrapping)
        data_cells, num_data_rows = self._build_data_cells(data_lines, columns, start_row=num_header_rows)

        total_rows = num_header_rows + num_data_rows
        all_cells = header_cells + data_cells

        if not data_cells or total_rows < 2:
            return None

        return TableStructure(
            table_id=f"page_{page_num}_financial_table_{table_idx}",
            page=page_num,
            bbox=[round(t_x0, 2), round(t_y0, 2), round(t_x1, 2), round(t_y1, 2)],
            num_rows=total_rows,
            num_cols=len(columns),
            cells=all_cells,
            confidence=0.98,
            confidence_source="deterministic_geometry",
            engine="deterministic_geometry",
        )

    def _discover_columns(
        self,
        all_blocks: List[TextBlock],
        numeric_blocks: List[TextBlock],
        header_lines: Optional[List[List[TextBlock]]] = None,
    ) -> List[ColumnBoundary]:
        """
        Discovers columns by combining leaf-headers and right-aligned numeric clusters.
        """
        min_x0 = min(b.bbox[0] for b in all_blocks)
        max_x1 = max(b.bbox[2] for b in all_blocks)

        leaf_hdr_blocks: List[TextBlock] = []
        if header_lines:
            best_hdr = max(header_lines, key=lambda hl: len(hl))
            if len(best_hdr) >= 3:
                leaf_hdr_blocks = best_hdr

        # Method 1: Leaf-header anchors (handles mixed text, percentage, and amount columns)
        if leaf_hdr_blocks and len(leaf_hdr_blocks) >= 3:
            first_h_x0 = leaf_hdr_blocks[0].bbox[0]
            col_list_blocks = []
            if first_h_x0 > min_x0 + 80.0:
                col_list_blocks.append((min_x0, first_h_x0 - 5.0, None))
            for hb in leaf_hdr_blocks:
                col_list_blocks.append((hb.bbox[0], hb.bbox[2], hb))

            columns: List[ColumnBoundary] = []
            for c_idx, (cx0, cx1, hb) in enumerate(col_list_blocks):
                if c_idx + 1 < len(col_list_blocks):
                    next_x0 = col_list_blocks[c_idx + 1][0]
                    right_bound = (cx1 + next_x0) / 2.0
                else:
                    right_bound = max_x1 + 15.0
                left_bound = columns[-1].x1 if columns else min_x0 - 5.0
                columns.append(ColumnBoundary(col_idx=c_idx, x0=left_bound, x1=right_bound))
            return columns

        # Method 2: Numeric right-edge clustering (pure numeric statement tables)
        strong_numeric = [
            b for b in numeric_blocks
            if re.search(r"\d{2,}|%", b.text.strip())
        ]
        target_blocks = strong_numeric if len(strong_numeric) >= 2 else numeric_blocks

        x1_values = sorted(b.bbox[2] for b in target_blocks)
        clusters: List[List[float]] = []

        for x1 in x1_values:
            matched = False
            for cl in clusters:
                avg_x1 = sum(cl) / len(cl)
                if abs(x1 - avg_x1) <= self.col_cluster_tolerance:
                    cl.append(x1)
                    matched = True
                    break
            if not matched:
                clusters.append([x1])

        min_cluster_size = 1 if len(target_blocks) <= 4 else 2
        valid_clusters = [cl for cl in clusters if len(cl) >= min_cluster_size]
        valid_clusters.sort(key=lambda cl: sum(cl) / len(cl))

        if not valid_clusters:
            valid_clusters = sorted(clusters, key=lambda cl: sum(cl) / len(cl))

        cluster_ranges: List[Tuple[float, float, float]] = []
        for cl in valid_clusters:
            cl_blocks = [b for b in target_blocks if any(abs(b.bbox[2] - x) <= self.col_cluster_tolerance for x in cl)]
            if cl_blocks:
                c_min_x0 = min(b.bbox[0] for b in cl_blocks)
                c_max_x1 = max(b.bbox[2] for b in cl_blocks)
                c_anchor = sum(cl) / len(cl)
                cluster_ranges.append((c_min_x0, c_max_x1, c_anchor))

        cluster_ranges.sort(key=lambda r: r[2])

        merged_ranges: List[Tuple[float, float, float]] = []
        for r_x0, r_x1, r_anc in cluster_ranges:
            if not merged_ranges:
                merged_ranges.append((r_x0, r_x1, r_anc))
            else:
                prev_x0, prev_x1, prev_anc = merged_ranges[-1]
                if abs(r_anc - prev_anc) <= 12.0:
                    merged_ranges[-1] = (min(prev_x0, r_x0), max(prev_x1, r_x1), (prev_anc + r_anc) / 2.0)
                else:
                    merged_ranges.append((r_x0, r_x1, r_anc))

        cluster_ranges = merged_ranges
        first_num_x0 = cluster_ranges[0][0] if cluster_ranges else min_x0 + 100.0

        left_blocks = [b for b in all_blocks if b.bbox[2] <= first_num_x0]
        desc_max_x1 = max((b.bbox[2] for b in left_blocks), default=first_num_x0 - 20.0)

        columns = []
        text_before_nums = [
            b for b in all_blocks
            if b.bbox[0] >= min_x0 + 80.0 and b.bbox[2] <= first_num_x0 - 5.0
            and not FINANCIAL_NUM_RE.match(b.text.strip())
        ]
        has_middle_text_col = False
        mid_text_x0 = 0.0
        mid_text_x1 = 0.0

        if text_before_nums:
            mid_x0s = [b.bbox[0] for b in text_before_nums]
            if len(mid_x0s) >= 2:
                mid_text_x0 = min(mid_x0s) - 5.0
                mid_text_x1 = max(b.bbox[2] for b in text_before_nums) + 5.0
                has_middle_text_col = True

        if has_middle_text_col:
            columns.append(ColumnBoundary(col_idx=0, x0=min_x0 - 5.0, x1=mid_text_x0))
            columns.append(ColumnBoundary(col_idx=1, x0=mid_text_x0, x1=mid_text_x1))
        else:
            split_x = (desc_max_x1 + first_num_x0) / 2.0
            columns.append(ColumnBoundary(col_idx=0, x0=min_x0 - 5.0, x1=split_x))

        for idx, (c_x0, c_x1, c_anc) in enumerate(cluster_ranges):
            col_left = columns[-1].x1
            if idx + 1 < len(cluster_ranges):
                next_x0 = cluster_ranges[idx + 1][0]
                col_right = (c_x1 + next_x0) / 2.0
            else:
                col_right = c_x1 + 15.0

            columns.append(
                ColumnBoundary(
                    col_idx=len(columns),
                    x0=col_left,
                    x1=col_right,
                    right_anchor=c_anc,
                    is_numeric=True,
                )
            )

        return columns

    def _build_header_cells(
        self,
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

    def _build_data_cells(
        self,
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
                b_mid = (b.bbox[0] + b.bbox[2]) / 2.0
                assigned = False
                for col in columns:
                    if col.x0 <= b_mid <= col.x1:
                        grid[col.col_idx].append(b)
                        assigned = True
                        break
                if not assigned:
                    closest_col = min(columns, key=lambda c: min(abs(c.x0 - b.bbox[0]), abs(c.x1 - b.bbox[2])))
                    grid[closest_col.col_idx].append(b)

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
                    cell_bbox = [0.0, 0.0, 0.0, 0.0]

                cells.append(
                    TableCell(
                        row_idx=abs_row,
                        col_idx=col_idx,
                        row_span=1,
                        col_span=1,
                        text=cell_text,
                        bbox=cell_bbox,
                        is_header=False,
                    )
                )

        return cells, len(logical_rows)

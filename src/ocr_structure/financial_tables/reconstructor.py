"""Financial grid orchestration adapted from OCR_pdf commit 37ca301."""
from __future__ import annotations

import math
import re
from typing import List, Optional
from ocr_contracts.table import TableStructure
from ocr_contracts.text_block import TextBlock
from ocr_contracts.geometry import is_oriented_line_candidate
from .patterns import FINANCIAL_NUM_RE, HEADER_KEYWORDS_NORM, ENTITY_BULLET_RE, strip_accents
from .segmentation import group_rows, segment_table_regions
from .columns import discover_columns
from .cells import build_header_cells, build_data_cells
from .provenance import bind_sources


class FinancialTableReconstructor:
    """Coordinate source-preserving financial table reconstruction."""

    def __init__(
        self,
        col_cluster_tolerance: float = 8.0,
        row_y_tolerance: float = 6.0,
        min_numeric_cols: int = 2,
    ):
        if (not math.isfinite(col_cluster_tolerance) or col_cluster_tolerance <= 0
                or not math.isfinite(row_y_tolerance) or row_y_tolerance <= 0
                or not isinstance(min_numeric_cols, int) or min_numeric_cols < 1):
            raise ValueError("Reconstruction tolerances and minimum numeric columns must be positive")
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
        if (not blocks or len(blocks) < 4 or not math.isfinite(page_width)
                or not math.isfinite(page_height) or min(page_width, page_height) <= 0):
            return []

        # Whole observed horizontal OCR blocks only; never split a line into guessed cells.
        body_blocks = []
        for b in blocks:
            if (len(b.bbox) != 4 or not b.text.strip()
                    or any(not math.isfinite(v) for v in b.bbox)
                    or b.bbox[0] >= b.bbox[2] or b.bbox[1] >= b.bbox[3]
                    or b.source in ("native", "vector", "docling")
                    or is_oriented_line_candidate(b.recognition_provenance)):
                continue
            body_blocks.append(b)

        if not body_blocks:
            return []

        # Segment page into candidate table bands
        candidate_segments = self._segment_table_regions(body_blocks, page_width, page_height)
        tables: List[TableStructure] = []

        for seg_idx, seg_blocks in enumerate(candidate_segments):
            table = self._build_table_from_segment(seg_blocks, page_num, seg_idx + 1)
            if table is not None and self._bind_sources(table, seg_blocks, blocks):
                tables.append(table)

        return tables

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
        lines = group_rows(blocks, self.row_y_tolerance)

        # Identify header lines vs data lines
        first_data_idx = -1
        for idx, l in enumerate(lines):
            nums = [b for b in l if FINANCIAL_NUM_RE.match(b.text.strip())]
            has_dates_only = all(re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", b.text) for b in nums) if nums else False

            if nums and not has_dates_only:
                first_data_idx = idx
                break

        if first_data_idx == -1:
            for idx, l in enumerate(lines):
                line_text = " ".join(b.text.strip() for b in l)
                if ENTITY_BULLET_RE.match(line_text) or "cong ty" in strip_accents(line_text).lower():
                    first_data_idx = idx
                    break

        # An entity anchor may precede its numeric continuation by a physical line.
        while first_data_idx > 0:
            previous = " ".join(b.text.strip() for b in lines[first_data_idx - 1])
            if (ENTITY_BULLET_RE.match(previous)
                    and not HEADER_KEYWORDS_NORM.search(strip_accents(previous))):
                first_data_idx -= 1
            else:
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
        # Require repeated amount/ratio evidence, not years or a contents-page number list.
        strong = [b for b in numeric_blocks if re.search(r"\d[.,]\d|\d\s*%", b.text)]
        occupied = {col.col_idx for col in columns if sum(
            col.x0 <= b.bbox[0] and b.bbox[2] <= col.x1 for b in strong) >= 2}
        if (len(columns) < 2 or len(occupied) < self.min_numeric_cols
                or not any(HEADER_KEYWORDS_NORM.search(strip_accents(b.text)) for line in header_lines for b in line)):
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
            bbox=[t_x0, t_y0, t_x1, t_y1],
            num_rows=total_rows,
            num_cols=len(columns),
            cells=all_cells,
            confidence=None,
            confidence_source="unavailable",
            engine="deterministic_geometry",
            needs_review=True,
            provenance={"method": "financial-geometry-v1", "quality_acceptance": "NOT_EVALUATED",
                        "upstream_commit": "37ca301304d84bfc4cc0d2cca3a296d513bb8ec7"},
        )

    def _segment_table_regions(self, blocks, page_width, page_height):
        return segment_table_regions(blocks, page_width, page_height, self.row_y_tolerance)

    def _discover_columns(self, all_blocks, numeric_blocks, header_lines=None):
        return discover_columns(all_blocks, numeric_blocks, header_lines,
                                col_cluster_tolerance=self.col_cluster_tolerance)

    def _build_header_cells(self, header_lines, columns):
        return build_header_cells(header_lines, columns)

    def _build_data_cells(self, data_lines, columns, start_row):
        return build_data_cells(data_lines, columns, start_row)

    _bind_sources = staticmethod(bind_sources)

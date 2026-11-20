"""Stable table layout API and page-level orchestration."""
from __future__ import annotations

from typing import List, Dict, Any, Optional, Tuple
import re
import unicodedata
import pymupdf
from ocr_contracts.geometry import is_oriented_line_candidate
from ocr_contracts.table import TableStructure, TableCell
from ocr_contracts.text_block import TextBlock
from ocr_contracts.region import Region, ContentType
from .table_layout.patterns import TABLE_HEADER_KEYWORDS, has_document_index_title, looks_like_document_index
from .table_layout.vector import extract_vector_tables, extract_single_vector_table
from .table_layout.row_rules import is_table_header_line, looks_like_signature_block, merge_wrapped_numeric_rows
from .table_layout.geometry import compute_column_boundaries, is_block_inside_any_table
from .table_layout.image_regions import extract_table_from_region_blocks
from .table_layout.ocr import detect_table_from_ocr_blocks


class DocumentLayoutEngine:
    """Route financial, vector, image-region and generic OCR table extraction."""

    def __init__(self):
        pass

    def extract_tables(
        self,
        page: pymupdf.Page,
        blocks: List[TextBlock],
        page_num: int,
        allow_vector_tables: bool = True,
        regions: Optional[List[Region]] = None,
        rejections: Optional[List[Dict[str, Any]]] = None,
    ) -> List[TableStructure]:
        """
        Extracts genuine tables from page:
        1. Checks vector-defined tables from PyMuPDF.
        2. Reconstructs tables from IMAGE_TABLE regions (Phase C).
        3. Checks remaining structured OCR blocks using geometric alignment and table keywords.

        Heuristic candidates rejected as a contents page are appended to
        ``rejections``; their blocks stay text.
        """
        tables: List[TableStructure] = []
        document_index_page = has_document_index_title(blocks)
        # The heuristic derives horizontal rows and has no vertical-cell alignment.
        blocks = [block for block in blocks if not is_oriented_line_candidate(block.recognition_provenance)]

        # 1. Vector tables
        if allow_vector_tables:
            tables.extend(self._extract_vector_tables(page, page_num))

        # Preserve whole observed OCR blocks in borderless financial grids before
        # image-region and generic heuristics. Wide two-up pages keep their existing routing.
        from ocr_structure.financial_table_reconstructor import FinancialTableReconstructor
        try:
            page_width = float(page.rect.width)
            page_height = float(page.rect.height)
        except (AttributeError, TypeError, ValueError):
            page_width = max((b.bbox[2] for b in blocks if len(b.bbox) == 4), default=0.0) + 1
            page_height = max((b.bbox[3] for b in blocks if len(b.bbox) == 4), default=0.0) + 1
        if page_width <= 900 and not document_index_page:
            candidates = [b for b in blocks if not self.is_block_inside_any_table(b.bbox, tables)]
            tables.extend(FinancialTableReconstructor().reconstruct_tables(
                candidates, page_width, page_height, page_num))

        # 2. IMAGE_TABLE regions (Phase C)
        if regions:
            for idx, r in enumerate(regions):
                if r.content_type == ContentType.IMAGE_TABLE:
                    bb = r.bbox
                    region_blocks = []
                    for b in blocks:
                        if b.bbox and len(b.bbox) >= 4:
                            bx_mid = (b.bbox[0] + b.bbox[2]) / 2.0
                            by_mid = (b.bbox[1] + b.bbox[3]) / 2.0
                            if bb.contains_point(bx_mid, by_mid):
                                region_blocks.append(b)

                    if region_blocks and not self.is_block_inside_any_table(bb.to_list(), tables):
                        t = self.extract_table_from_region_blocks(
                            region_blocks,
                            page_num,
                            table_id=f"page_{page_num}_image_table_{idx+1}",
                            region_bbox=bb.to_list(),
                        )
                        if t:
                            tables.append(t)

        # 3. Scanned / OCR aligned table detection from remaining blocks
        # Support Two-Up page subpage splitting (e.g. HAR p40 Left: Tài Sản, Right: Nguồn Vốn)
        is_two_up = False
        split_x = 0.0
        if page and hasattr(page, "rect"):
            try:
                p_width = float(page.rect.width)
                if p_width > 900:
                    is_two_up = True
                    split_x = p_width / 2.0
            except (TypeError, ValueError):
                pass

        if is_two_up:
            # Check if this page contains a wide spanning table (e.g. Statement of Changes in Equity, BCTC)
            # where numeric columns span continuously across the midline without an independent text column on the right.
            import re
            sorted_all = sorted(blocks, key=lambda b: (b.bbox[1] if b.bbox else 0, b.bbox[0] if b.bbox else 0))
            phys_rows = []
            curr_row = []
            curr_y = None
            for b in sorted_all:
                if not b.bbox or len(b.bbox) < 4:
                    continue
                y_mid = (b.bbox[1] + b.bbox[3]) / 2.0
                if curr_y is None:
                    curr_y = y_mid
                    curr_row.append(b)
                elif abs(y_mid - curr_y) <= 10.0:
                    curr_row.append(b)
                else:
                    phys_rows.append(curr_row)
                    curr_row = [b]
                    curr_y = y_mid
            if curr_row:
                phys_rows.append(curr_row)

            spanning_rows = 0
            independent_rows = 0
            for r in phys_rows:
                left_b = [b for b in r if b.bbox[2] < split_x]
                right_b = [b for b in r if b.bbox[0] >= split_x]
                if left_b and right_b:
                    right_texts = [b.text.strip() for b in right_b]
                    right_numeric = all(re.match(r"^[\d\s\.\,\(\)\-\%]+$", t) for t in right_texts if t)
                    if right_numeric:
                        spanning_rows += 1
                    else:
                        independent_rows += 1

            if spanning_rows >= 8 and spanning_rows > independent_rows:
                subpage_block_groups = [blocks]
            else:
                subpage_block_groups = [
                    [b for b in blocks if b.bbox and (b.bbox[0] + b.bbox[2]) / 2.0 < split_x],
                    [b for b in blocks if b.bbox and (b.bbox[0] + b.bbox[2]) / 2.0 >= split_x],
                ]
        else:
            subpage_block_groups = [blocks]

        for s_blocks in subpage_block_groups:
            for _ in range(5):  # Detect up to 5 tables per subpage/page
                candidate_blocks = [b for b in s_blocks if not self.is_block_inside_any_table(b.bbox, tables)]
                if len(candidate_blocks) < 5:
                    break
                ocr_table = self._detect_table_from_ocr_blocks(
                    candidate_blocks,
                    page_num,
                    table_id=f"page_{page_num}_table_{len(tables)+1}",
                    document_index_page=document_index_page,
                    rejections=rejections,
                )
                if not ocr_table:
                    break
                tables.append(ocr_table)

        return tables

    _extract_vector_tables = extract_vector_tables
    _extract_single_vector_table = extract_single_vector_table
    _is_table_header_line = staticmethod(is_table_header_line)
    _detect_table_from_ocr_blocks = detect_table_from_ocr_blocks
    _looks_like_signature_block = staticmethod(looks_like_signature_block)
    _merge_wrapped_numeric_rows = staticmethod(merge_wrapped_numeric_rows)
    _compute_column_boundaries = staticmethod(compute_column_boundaries)
    is_block_inside_any_table = staticmethod(is_block_inside_any_table)
    extract_table_from_region_blocks = extract_table_from_region_blocks

from typing import List
from ocr_contracts.text_block import TextBlock
from ocr_document.xy_cut import xy_cut_order

# `heuristic` is the established sorter; `xycut` is the C3.4 candidate.
READING_ORDER_METHODS = ("heuristic", "xycut")


class ReadingOrderSorter:
    """
    Sorts text blocks into natural human reading order based on geometric layout,
    multi-column detection, and header/footer separation.
    """
    def __init__(self, page_width: float = 595.0, page_height: float = 842.0,
                 method: str = "heuristic", min_gap: float = 3.0, classify_furniture: bool = False):
        if method not in READING_ORDER_METHODS:
            raise ValueError(f"reading_order.method must be one of {', '.join(READING_ORDER_METHODS)}")
        self.page_width = page_width
        self.page_height = page_height
        self.method = method
        self.min_gap = min_gap
        self.classify_furniture = classify_furniture

    def sort_blocks(self, blocks: List[TextBlock]) -> List[TextBlock]:
        if not blocks:
            return []

        # 1. Separate Header, Footer, Page Number, and Body
        headers: List[TextBlock] = []
        footers: List[TextBlock] = []
        page_numbers: List[TextBlock] = []
        body: List[TextBlock] = []

        for block in blocks:
            # A formula near a page edge is still mathematical content.
            # Geometric header/footer guessing must not change its type.
            if block.block_type == "formula":
                body.append(block)
                continue
            bbox = block.bbox
            if not bbox or len(bbox) < 4:
                body.append(block)
                continue

            x0, y0, x1, y1 = bbox

            # Model labels are proposals until the document pass accepts them.
            confirmed = self.classify_furniture or block.role_status == "ACCEPTED"
            if confirmed and block.block_type in ("header", "running_header"):
                headers.append(block)
                continue
            elif confirmed and block.block_type in ("footer", "running_footer"):
                footers.append(block)
                continue
            elif confirmed and block.block_type == "page_number":
                page_numbers.append(block)
                continue

            if self.classify_furniture:
                # Preserve the V6.1 policy only on the explicit legacy path.
                import re
                section = bool(re.match(r"^\s*(\d+(\.\d+)*\s*[\)\.\/\-]|[IVXLCDM]+\s*[\)\.\/\-]|phần\s+[IVXLCDM]+)", block.text, re.I))
                short = len(block.text.splitlines()) <= 2 and len(block.text.strip()) <= 80
                if not section and short and block.page > 1 and y1 <= self.page_height * .10:
                    block.block_type = "header"
                    headers.append(block)
                    continue
                if not section and short and y0 >= self.page_height * .90:
                    block.block_type = "footer"
                    footers.append(block)
                    continue
            body.append(block)

        # Sort headers, footers and page numbers top-down, left-right
        sorted_headers = self._sort_column(headers)
        sorted_footers = self._sort_column(footers + page_numbers)

        # 2. Analyze Body for multi-column structure
        if self.method == "xycut":
            sorted_body = xy_cut_order(body, self._sort_column, self.min_gap)
        else:
            sorted_body = self._sort_body_blocks(body)

        # 3. Combine in reading sequence: Header -> Body -> Footer
        result = sorted_headers + sorted_body + sorted_footers

        # Update line_index
        for idx, b in enumerate(result):
            b.line_index = idx

        return result

    def _sort_body_blocks(self, blocks: List[TextBlock]) -> List[TextBlock]:
        """
        Sorts body blocks respecting multi-column layouts using dynamic
        Gutter Detection. Supports 2-column and N-column layouts.
        """
        if len(blocks) < 4:
            return self._sort_column(blocks)

        # Step 1: Separate spanning blocks (full-width titles/paragraphs)
        narrow_blocks = []
        spanning_blocks = []

        for b in blocks:
            x0, y0, x1, y1 = b.bbox
            width = x1 - x0
            if width > self.page_width * 0.65:
                spanning_blocks.append(b)
            else:
                narrow_blocks.append(b)

        # Step 2: Detect gutters (vertical white-space gaps) dynamically
        gutters = self._detect_gutters(narrow_blocks, min_gutter_width=max(self.min_gap, 6.0))

        if not gutters:
            # No column structure detected → single column sort
            return self._sort_column(blocks)

        # Step 3: Filter out margin gutters (no content beyond them)
        # A gutter touching the rightmost content edge is margin, not a column separator
        if narrow_blocks:
            valid_gutters = []
            for gs, ge in gutters:
                # Keep gutter only if there are blocks on BOTH sides
                has_left = any((b.bbox[0] + b.bbox[2]) / 2 < gs for b in narrow_blocks)
                has_right = any((b.bbox[0] + b.bbox[2]) / 2 > ge for b in narrow_blocks)
                if has_left and has_right:
                    valid_gutters.append((gs, ge))
            gutters = valid_gutters

        if not gutters:
            return self._sort_column(blocks)

        # Step 4: Build column ranges from gutters
        # Sort gutters left to right
        gutters.sort(key=lambda g: g[0])

        # Create column boundaries: [0, gutter1_start] [gutter1_end, gutter2_start] ...
        columns: list = []  # List[List[TextBlock]]
        for i in range(len(gutters) + 1):
            columns.append([])

        # Assign each narrow block to the correct column
        for b in narrow_blocks:
            center_x = (b.bbox[0] + b.bbox[2]) / 2.0
            col_idx = 0
            for i, (gs, ge) in enumerate(gutters):
                if center_x > ge:
                    col_idx = i + 1
                elif center_x >= gs:
                    # Center is inside a gutter → spanning
                    col_idx = -1
                    break
            if col_idx == -1:
                spanning_blocks.append(b)
            else:
                b.column_index = col_idx
                columns[col_idx].append(b)

        # Step 5: Verify multi-column layout is meaningful
        populated_cols = [c for c in columns if len(c) >= 2]
        if len(populated_cols) < 2:
            # Not a real multi-column layout
            return self._sort_column(blocks)

        # Step 6: Sort each column independently, then merge with spanning
        sorted_columns = [self._sort_column(col) for col in columns if col]
        sorted_spanning = self._sort_column(spanning_blocks)

        # For 2 columns, use existing merge logic
        if len(sorted_columns) == 2:
            return self._merge_columns_with_spanning(
                sorted_columns[0], sorted_columns[1], sorted_spanning
            )

        # For N columns, merge all columns sequentially with spanning blocks
        return self._merge_multi_columns_with_spanning(sorted_columns, sorted_spanning)

    def _detect_gutters(self, blocks: List[TextBlock], min_gutter_width: float = 8.0) -> list:
        """
        Detect vertical gutters (white-space gaps) between columns by analyzing
        the x-coverage histogram of all blocks (1pt resolution).

        Returns list of (gutter_start, gutter_end) tuples, sorted left to right.
        Only considers gaps in the inner page region (ignoring 12% margins).
        """
        if not blocks:
            return []

        # Create occupancy histogram (1pt bins across page width)
        page_w = int(self.page_width) + 1
        occupancy = [0] * page_w

        for b in blocks:
            x0, _, x1, _ = b.bbox
            bin_start = max(0, int(x0))
            bin_end = min(page_w - 1, int(x1))
            for i in range(bin_start, bin_end + 1):
                occupancy[i] += 1

        # Find continuous gaps (occupancy == 0) in the inner region
        # Ignore page margins (first 12% and last 12%)
        margin = int(self.page_width * 0.12)

        gutters = []
        gap_start = None

        for x in range(margin, page_w - margin):
            if occupancy[x] == 0:
                if gap_start is None:
                    gap_start = x
            else:
                if gap_start is not None:
                    gap_width = x - gap_start
                    if gap_width >= min_gutter_width:
                        gutters.append((float(gap_start), float(x)))
                    gap_start = None

        # Handle gap extending to end of scan range
        if gap_start is not None:
            gap_width = (page_w - margin) - gap_start
            if gap_width >= min_gutter_width:
                gutters.append((float(gap_start), float(page_w - margin)))

        return gutters

    def _sort_column(self, blocks: List[TextBlock]) -> List[TextBlock]:
        """
        Sorts blocks top-to-bottom. Blocks on approximately the same horizontal line
        (within 6 points vertically) are sorted left-to-right.
        """
        if not blocks:
            return []

        # Sort by (y0, x0) initially
        blocks_sorted = sorted(blocks, key=lambda b: (b.bbox[1] if b.bbox else 0, b.bbox[0] if b.bbox else 0))

        # Group into lines
        lines: List[List[TextBlock]] = []
        current_line: List[TextBlock] = []
        current_y = None
        y_tolerance = 6.0  # Points

        for b in blocks_sorted:
            y0 = b.bbox[1] if b.bbox else 0
            if current_y is None:
                current_y = y0
                current_line.append(b)
            elif abs(y0 - current_y) <= y_tolerance:
                current_line.append(b)
            else:
                # Sort line left-to-right
                current_line.sort(key=lambda x: x.bbox[0] if x.bbox else 0)
                lines.append(current_line)
                current_line = [b]
                current_y = y0

        if current_line:
            current_line.sort(key=lambda x: x.bbox[0] if x.bbox else 0)
            lines.append(current_line)

        # Flatten
        return [b for line in lines for b in line]

    def _merge_columns_with_spanning(
        self,
        left_col: List[TextBlock],
        right_col: List[TextBlock],
        spanning: List[TextBlock]
    ) -> List[TextBlock]:
        if not spanning:
            return left_col + right_col

        result = []
        # Sort spanning blocks by y0
        sorted_span = sorted(spanning, key=lambda b: b.bbox[1])
        remaining_left = list(left_col)
        remaining_right = list(right_col)

        for span in sorted_span:
            span_y = span.bbox[1]
            # Take any left and right blocks before this spanning block
            left_before = [b for b in remaining_left if b.bbox[3] <= span_y]
            right_before = [b for b in remaining_right if b.bbox[3] <= span_y]

            result.extend(left_before)
            result.extend(right_before)
            result.append(span)

            remaining_left = [b for b in remaining_left if b.bbox[3] > span_y]
            remaining_right = [b for b in remaining_right if b.bbox[3] > span_y]

        result.extend(remaining_left)
        result.extend(remaining_right)
        return result

    def _merge_multi_columns_with_spanning(
        self,
        sorted_columns: list,
        spanning: List[TextBlock]
    ) -> List[TextBlock]:
        """
        Merge N sorted columns with spanning blocks, interleaving by vertical position.
        Columns are read left-to-right: all of column 0 before column 1, etc.,
        with spanning blocks inserted at their vertical position.
        """
        if not spanning:
            result = []
            for col in sorted_columns:
                result.extend(col)
            return result

        result = []
        sorted_span = sorted(spanning, key=lambda b: b.bbox[1])
        remaining = [list(col) for col in sorted_columns]

        for span in sorted_span:
            span_y = span.bbox[1]
            # Flush all column blocks above this spanning block
            for col in remaining:
                before = [b for b in col if b.bbox[3] <= span_y]
                result.extend(before)
                col[:] = [b for b in col if b.bbox[3] > span_y]
            result.append(span)

        # Flush remaining blocks from all columns
        for col in remaining:
            result.extend(col)

        return result

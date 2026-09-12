from typing import List
from src.ocr.domain.models.text_block import TextBlock

class ReadingOrderSorter:
    """
    Sorts text blocks into natural human reading order based on geometric layout,
    multi-column detection, and header/footer separation.
    """
    def __init__(self, page_width: float = 595.0, page_height: float = 842.0):
        self.page_width = page_width
        self.page_height = page_height

    def sort_blocks(self, blocks: List[TextBlock]) -> List[TextBlock]:
        if not blocks:
            return []

        # 1. Separate Header, Footer, and Body
        headers: List[TextBlock] = []
        footers: List[TextBlock] = []
        body: List[TextBlock] = []

        header_cutoff = self.page_height * 0.08
        footer_cutoff = self.page_height * 0.92

        for block in blocks:
            bbox = block.bbox
            if not bbox or len(bbox) < 4:
                body.append(block)
                continue

            x0, y0, x1, y1 = bbox
            import re
            is_section_number = bool(re.match(r"^\s*(\d+(\.\d+)*\s*[\)\.\/\-]|[IVXLCDM]+\s*[\)\.\/\-]|phần\s+[IVXLCDM]+)", block.text, re.IGNORECASE))
            is_real_header = (block.page > 1) and (not is_section_number) and (
                y1 <= header_cutoff
                and any(kw in block.text.lower() for kw in ("báo cáo thường niên", "công ty cổ phần", "tổng công ty", "cộng hòa"))
            )
            if is_real_header:
                block.block_type = "header"
                headers.append(block)
            elif y0 >= footer_cutoff:
                block.block_type = "footer"
                footers.append(block)
            else:
                body.append(block)

        # Sort headers and footers top-down, left-right
        sorted_headers = self._sort_column(headers)
        sorted_footers = self._sort_column(footers)

        # 2. Analyze Body for multi-column structure
        sorted_body = self._sort_body_blocks(body)

        # 3. Combine in reading sequence: Header -> Body -> Footer
        result = sorted_headers + sorted_body + sorted_footers

        # Update line_index
        for idx, b in enumerate(result):
            b.line_index = idx

        return result

    def _sort_body_blocks(self, blocks: List[TextBlock]) -> List[TextBlock]:
        if len(blocks) < 4:
            return self._sort_column(blocks)

        mid_x = self.page_width / 2.0
        # Check if blocks naturally separate into two distinct columns
        left_col = []
        right_col = []
        spanning_blocks = []

        for b in blocks:
            x0, y0, x1, y1 = b.bbox
            width = x1 - x0
            if width > self.page_width * 0.65:
                # Spans most of the page (title or wide paragraph)
                spanning_blocks.append(b)
            elif x1 <= mid_x + 20:
                left_col.append(b)
            elif x0 >= mid_x - 20:
                right_col.append(b)
            else:
                spanning_blocks.append(b)

        # Is two-column layout prominent?
        if len(left_col) >= 2 and len(right_col) >= 2:
            # Group into vertical bands if there are spanning titles
            # For simplicity and robustness, sort left column then right column
            sorted_left = self._sort_column(left_col)
            sorted_right = self._sort_column(right_col)
            sorted_spanning = self._sort_column(spanning_blocks)

            # Interleave spanning blocks by vertical position
            return self._merge_columns_with_spanning(sorted_left, sorted_right, sorted_spanning)

        return self._sort_column(blocks)

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

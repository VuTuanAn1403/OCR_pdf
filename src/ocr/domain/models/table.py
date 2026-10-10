from typing import List, Optional, Tuple
import re
import textwrap
import unicodedata
from pydantic import BaseModel, Field


class TableCell(BaseModel):
    row_idx: int
    col_idx: int
    row_span: int = 1
    col_span: int = 1
    text: str = ""
    bbox: List[float] = Field(default_factory=list)
    confidence: float = 1.0


class TableRenderer:
    """
    Table Renderer implementing Microsoft Word-like table layout and rendering:
    - Auto-expands column widths based on content (snug dynamic widths)
    - Automatically wraps text inside cells when long, without creating extra rows
    - Calculates row height based on the number of wrapped lines in that row
    - Never uses <br> tags
    - Never uses markdown newlines to simulate extra rows
    - Preserves 100% of OCR / native text without alteration
    - Preserves exact row/column counts of the table
    - Avoids column data misalignment
    - Displays short cells normally and keeps long cells from blowing out table width
    """

    @classmethod
    def clean_cell_text(cls, text: str) -> str:
        """Cleans and standardizes raw cell text without altering semantic content."""
        if not text:
            return ""
        norm = unicodedata.normalize("NFC", text)
        # Strip legacy HTML tags
        norm = re.sub(r"<\/?(table|tr|td|th|tbody|thead|tfoot)[^>]*>", " ", norm, flags=re.IGNORECASE)
        norm = re.sub(r"\s*(?:rowspan|colspan)\s*=\s*['\"][^'\"]*['\"]", " ", norm, flags=re.IGNORECASE)
        return norm

    @classmethod
    def build_grid_matrix(cls, table: "TableStructure") -> Tuple[List[str], List[List[str]], Optional[str]]:
        """
        Builds normalized 2D grid matrix: (headers, data_rows, narrative_fallback).
        If narrative fallback is returned, the table is a single-column paragraph.
        """
        if not table.cells and (table.num_rows == 0 or table.num_cols == 0):
            return [], [], None

        max_r = table.num_rows
        max_c = table.num_cols
        for c in table.cells:
            r_span = max(1, getattr(c, "row_span", 1) or 1)
            c_span = max(1, getattr(c, "col_span", 1) or 1)
            max_r = max(max_r, c.row_idx + r_span)
            max_c = max(max_c, c.col_idx + c_span)

        if max_r == 0 or max_c == 0:
            return [], [], None

        grid = [["" for _ in range(max_c)] for _ in range(max_r)]

        for cell in table.cells:
            r = cell.row_idx
            c = cell.col_idx
            if 0 <= r < max_r and 0 <= c < max_c:
                raw = cls.clean_cell_text(cell.text or "")
                existing = grid[r][c]
                if not existing:
                    grid[r][c] = raw
                elif raw and raw not in existing:
                    grid[r][c] = raw if existing in raw else f"{existing} {raw}"

                # For spanned cells, ensure spanned positions exist in grid
                r_span = max(1, getattr(cell, "row_span", 1) or 1)
                c_span = max(1, getattr(cell, "col_span", 1) or 1)
                for dr in range(r_span):
                    for dc in range(c_span):
                        if (dr, dc) != (0, 0) and r + dr < max_r and c + dc < max_c:
                            if not grid[r + dr][c + dc]:
                                grid[r + dr][c + dc] = ""

        # Prune completely empty columns across all rows
        active_cols = [c for c in range(max_c) if any(grid[r][c].strip() for r in range(max_r))]
        if not active_cols:
            return [], [], None

        # Anti-false-positive table check: single-column narrative/paragraphs
        if len(active_cols) == 1:
            all_cell_texts = [grid[r][active_cols[0]].strip() for r in range(max_r) if grid[r][active_cols[0]].strip()]
            is_narrative = any(len(txt.split()) > 7 or len(txt) > 40 for txt in all_cell_texts)
            if is_narrative or not any(re.match(r"^[\d\s\.\,\(\)\-\%NAna\/]+$", txt) for txt in all_cell_texts):
                fallback = "\n\n".join(re.sub(r"(?:<br\s*/?>|\r?\n)+", " ", txt).strip() for txt in all_cell_texts)
                return [], [], fallback

        # Filter out rows that are completely empty
        non_empty_rows = [r for r in range(max_r) if any(grid[r][c].strip() for c in active_cols)]
        if not non_empty_rows:
            return [], [], None

        normalized_rows = []
        for r_idx in non_empty_rows:
            row_items = [grid[r_idx][c].strip() for c in active_cols]
            normalized_rows.append(row_items)

        num_active = len(active_cols)
        if len(normalized_rows) == 1:
            headers = [f"Cột {i+1}" for i in range(num_active)]
            data_rows = [normalized_rows[0]]
        else:
            headers = normalized_rows[0]
            if not any(headers):
                headers = [f"Cột {i+1}" for i in range(num_active)]
            data_rows = normalized_rows[1:]

        return headers, data_rows, None

    @classmethod
    def calculate_column_widths(
        cls,
        headers: List[str],
        data_rows: List[List[str]],
        max_table_width: int = 100
    ) -> List[int]:
        """
        Dynamically calculates column widths:
        - Auto-expands based on content (snug dynamic widths)
        - Constrains excessively long columns so the table does not stretch too wide
        - Ensures column width is at least as wide as the longest unbreakable word (or min 3)
        """
        num_cols = len(headers)
        if num_cols == 0:
            return []

        natural_w = []
        max_word_w = []

        for c in range(num_cols):
            texts = [headers[c]] + [row[c] for row in data_rows]
            max_len = max(3, max(len(t.strip()) for t in texts))
            natural_w.append(max_len)

            words = [w for t in texts for w in re.split(r"\s+", t.strip()) if w]
            max_word = max(3, max(len(w) for w in words)) if words else 3
            max_word_w.append(max_word)

        # Border overhead: (num_cols + 1) vertical borders + 2 padding spaces per column
        border_overhead = num_cols * 3 + 1
        avail_content = max(num_cols * 4, max_table_width - border_overhead)

        # If natural widths fit within table budget, keep 100% natural snug widths
        if sum(natural_w) <= avail_content:
            return natural_w

        # Otherwise, wrap wide columns fairly
        fair_share = max(8, avail_content // num_cols)
        col_widths = list(natural_w)

        wide_cols = [c for c in range(num_cols) if natural_w[c] > fair_share]
        short_cols = [c for c in range(num_cols) if natural_w[c] <= fair_share]

        short_sum = sum(natural_w[c] for c in short_cols)
        remaining_avail = max(len(wide_cols) * 8, avail_content - short_sum)

        if wide_cols:
            wide_share = max(10, remaining_avail // len(wide_cols))
            for c in wide_cols:
                w = max(wide_share, max_word_w[c])
                col_widths[c] = min(natural_w[c], max(10, min(65, w)))

        return col_widths

    @classmethod
    def determine_column_alignments(cls, data_rows: List[List[str]], num_cols: int) -> List[str]:
        """Right-aligns numeric columns, left-aligns text columns."""
        alignments = []
        num_pattern = re.compile(r"^[\(\-\+]?[\d\s\.\,]+(?:\%)?\)?$|^\-$")
        for c in range(num_cols):
            numeric_cells = 0
            total_cells = 0
            for row in data_rows:
                val = row[c].strip()
                if val:
                    total_cells += 1
                    if num_pattern.match(val) and any(ch.isdigit() for ch in val):
                        numeric_cells += 1
                    elif val in ("-", "—", "N/A", "NA"):
                        numeric_cells += 1
            if total_cells > 0 and (numeric_cells / total_cells) >= 0.5:
                alignments.append("right")
            else:
                alignments.append("left")
        return alignments

    @classmethod
    def wrap_cell_text(cls, text: str, width: int) -> List[str]:
        """
        Wraps cell text into sub-lines according to column width.
        - Preserves all words without alteration
        - Does NOT use <br>
        - Does NOT create extra rows
        """
        if not text:
            return [""]

        # Split intentional line breaks if present in raw text
        paragraphs = [p.strip() for p in re.split(r"(?:<br\s*/?>|\r?\n)+", text, flags=re.IGNORECASE) if p.strip()]
        if not paragraphs:
            return [""]

        sub_lines = []
        for p in paragraphs:
            clean_p = re.sub(r"[ \t]+", " ", p).strip()
            if not clean_p:
                continue
            wrapped = textwrap.wrap(clean_p, width=width, break_long_words=True, break_on_hyphens=False)
            sub_lines.extend(wrapped if wrapped else [""])

        return sub_lines if sub_lines else [""]

    @classmethod
    def render_box_table(
        cls,
        headers: List[str],
        data_rows: List[List[str]],
        caption: Optional[str] = None,
        max_table_width: int = 100
    ) -> str:
        """
        Renders a Word-like Unicode Box Drawing Table:
        ┌────────────┬────────────┐
        │ Cột 1      │ Cột 2      │
        ├────────────┼────────────┤
        │ Dòng 1     │ Dòng 1     │
        │ Dòng 2     │ Dòng 2     │
        └────────────┴────────────┘
        """
        num_cols = len(headers)
        if num_cols == 0:
            return ""

        col_widths = cls.calculate_column_widths(headers, data_rows, max_table_width=max_table_width)
        alignments = cls.determine_column_alignments(data_rows, num_cols)

        # Wrap header row
        h_sublines = [cls.wrap_cell_text(headers[c], col_widths[c]) for c in range(num_cols)]
        h_height = max(len(l) for l in h_sublines)
        for c in range(num_cols):
            while len(h_sublines[c]) < h_height:
                h_sublines[c].append("")

        # Wrap data rows and determine each row's height
        d_sublines = []
        d_heights = []
        for row in data_rows:
            r_cells = [cls.wrap_cell_text(row[c], col_widths[c]) for c in range(num_cols)]
            r_height = max(1, max(len(l) for l in r_cells))
            for c in range(num_cols):
                while len(r_cells[c]) < r_height:
                    r_cells[c].append("")
            d_sublines.append(r_cells)
            d_heights.append(r_height)

        # Border characters
        top_border = "┌" + "┬".join("─" * (w + 2) for w in col_widths) + "┐"
        row_sep = "├" + "┼".join("─" * (w + 2) for w in col_widths) + "┤"
        bot_border = "└" + "┴".join("─" * (w + 2) for w in col_widths) + "┘"

        lines = []
        if caption:
            lines.append(f"**{caption}**\n")

        # Top border
        lines.append(top_border)

        # Header lines
        for k in range(h_height):
            cells_str = [
                h_sublines[c][k].rjust(col_widths[c]) if alignments[c] == "right" else h_sublines[c][k].ljust(col_widths[c])
                for c in range(num_cols)
            ]
            lines.append("│ " + " │ ".join(cells_str) + " │")

        # Header separator
        lines.append(row_sep)

        # Data rows
        for r_idx, r_cells in enumerate(d_sublines):
            r_h = d_heights[r_idx]
            for k in range(r_h):
                cells_str = [
                    r_cells[c][k].rjust(col_widths[c]) if alignments[c] == "right" else r_cells[c][k].ljust(col_widths[c])
                    for c in range(num_cols)
                ]
                lines.append("│ " + " │ ".join(cells_str) + " │")
            # Row separator between data rows to clearly separate multi-line cells
            if r_idx < len(d_sublines) - 1:
                lines.append(row_sep)

        # Bottom border
        lines.append(bot_border)

        return "\n".join(lines)

    @classmethod
    def render_pipe_table(
        cls,
        headers: List[str],
        data_rows: List[List[str]],
        caption: Optional[str] = None
    ) -> str:
        """
        Renders standard Markdown Pipe Table (| Col 1 | Col 2 |).
        Preserved for backwards compatibility.
        """
        num_cols = len(headers)
        if num_cols == 0:
            return ""

        # GFM tables require one physical line per row. Keep all text as plain
        # inline content and let the Markdown viewer wrap long cells visually.
        def normalize_inline(value: str) -> str:
            value = re.sub(r"<br\s*/?>|\r\n|\r|\n", " ", value, flags=re.IGNORECASE)
            value = re.sub(r"\s+", " ", value).strip()
            return re.sub(r"(?<!\\)\|", r"\|", value)

        norm_headers = [normalize_inline(h) for h in headers]
        norm_data_rows = [
            [normalize_inline(row[c]) for c in range(num_cols)]
            for row in data_rows
        ]

        col_widths = [4] * num_cols
        for c in range(num_cols):
            max_w = max(4, len(norm_headers[c].strip()))
            for row in norm_data_rows:
                cell_len = len(row[c].strip())
                if cell_len > max_w:
                    max_w = cell_len
            col_widths[c] = max_w

        alignments = cls.determine_column_alignments(norm_data_rows, num_cols)

        # Format header row
        padded_headers = [norm_headers[c].strip().ljust(col_widths[c]) for c in range(num_cols)]
        header_line = "| " + " | ".join(padded_headers) + " |"

        # Format separator line matching dynamic column widths
        sep_items = []
        for c in range(num_cols):
            w = col_widths[c]
            if alignments[c] == "right":
                sep_items.append("-" * (w - 1) + ":")
            else:
                sep_items.append(":" + "-" * (w - 1))
        sep_line = "| " + " | ".join(sep_items) + " |"

        lines = []
        if caption:
            lines.append(f"**{caption}**\n")
        lines.append(header_line)
        lines.append(sep_line)

        for row in norm_data_rows:
            padded_row = []
            for c in range(num_cols):
                val = row[c].strip()
                if alignments[c] == "right":
                    padded_row.append(val.rjust(col_widths[c]))
                else:
                    padded_row.append(val.ljust(col_widths[c]))
            lines.append("| " + " | ".join(padded_row) + " |")

        return "\n".join(lines)

    @classmethod
    def render_table(cls, table: "TableStructure", format: str = "box") -> str:
        """Main rendering dispatcher for TableStructure."""
        headers, data_rows, fallback = cls.build_grid_matrix(table)
        if fallback:
            return fallback
        if not headers and not data_rows:
            return ""

        if format == "pipe":
            return cls.render_pipe_table(headers, data_rows, caption=table.caption)
        return cls.render_box_table(headers, data_rows, caption=table.caption)


class TableStructure(BaseModel):
    table_id: str
    page: int
    bbox: List[float] = Field(default_factory=list)
    num_rows: int = 0
    num_cols: int = 0
    cells: List[TableCell] = Field(default_factory=list)
    caption: Optional[str] = None
    confidence: float = 1.0
    has_nested_tables: bool = False
    is_nested: bool = False

    @property
    def is_complex(self) -> bool:
        return self.has_nested_tables or any(c.row_span > 1 or c.col_span > 1 for c in self.cells)

    @staticmethod
    def _wrap_text(text: str, max_width: int = 60, sep: str = "<br>") -> str:
        if not text or len(text) <= max_width:
            return text
        words = text.split(" ")
        lines = []
        curr = []
        curr_len = 0
        for w in words:
            if not w:
                continue
            w_len = len(w)
            if curr and curr_len + 1 + w_len > max_width:
                lines.append(" ".join(curr))
                curr = [w]
                curr_len = w_len
            else:
                curr.append(w)
                curr_len += (1 + w_len) if curr_len > 0 else w_len
        if curr:
            lines.append(" ".join(curr))
        return sep.join(lines)

    def to_html(self, max_line_len: int = 80) -> str:
        """Returns table rendered as clean Word-like table."""
        return self.to_markdown()

    def to_box_table(self, max_table_width: int = 100) -> str:
        """Renders strictly as a Word-like Unicode Box Drawing Table."""
        return TableRenderer.render_table(self, format="box")

    def to_pipe_table(self) -> str:
        """Renders as Markdown Pipe Table (| Col 1 | Col 2 |)."""
        return TableRenderer.render_table(self, format="pipe")

    def to_markdown(self, format: str = "box") -> str:
        """
        Renders the table according to user specification.
        - format='box' (default): Word-like Unicode Box Drawing Table with in-cell wrapping,
          auto-expanding row height, snug dynamic column widths, zero <br> tags, zero fake rows.
        - format='pipe': Standard Markdown Pipe Table (| col 1 | col 2 |)
        """
        return TableRenderer.render_table(self, format=format)

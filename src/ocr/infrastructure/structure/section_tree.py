"""Document-level section tree and paragraphs (proposal V6 C2.4, heuristic-v1).

No layout model is selected yet (decision D1), so headings come from what the
extraction already provides: font size relative to the document body, bold,
upper case, Vietnamese numbering, DLA block types and an optional table of
contents. Every rule errs towards rejecting: a missed heading only becomes a
paragraph, while a false heading pollutes the outline (H3).

Known limits, recorded in structure.json: OCR lines carry no font size, so on
scanned pages headings come from numbering and keywords only; tables are not
merged across pages; no learned text correction is applied.
"""
from __future__ import annotations

import difflib
import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from src.ocr.infrastructure.structure.ids import Item

METHOD = "heuristic-v1"
DOCUMENT_ROLE_METHOD = "heuristic-v2-document-roles"
ACCEPT_SCORE = 3.0
MAX_LEVEL = 4
# Lower bound of font size / body size for levels 1..3; below is level 4.
LEVEL_SIZE_BANDS = (2.6, 1.6, 1.25)
TOC_MATCH_RATIO = 0.88
PAGE_BODY_MIN_CHARS = 300  # enough body text to trust a page's own font median
REPEAT_WINDOW_PAGES = 4    # same title again within this many pages = running title
RUNNING_TITLE_TOP_RATIO = 0.15
_EXCLUDED_TYPES = {"header", "footer", "running_header", "running_footer", "page_number",
                   "caption", "formula", "list"}
_SENTENCE_END = (".", ":", ";", "?", "!")
_BULLETS = ("-", "+", "*", "•", "–", "●", "▪")
_VI_LETTERS = set("ăâđêôơưáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")
_JOB_TITLE = re.compile(r"^(chủ tịch|phó chủ tịch|tổng giám đốc|phó tổng giám đốc|giám đốc|kế toán trưởng|"
                        r"thành viên|trưởng ban|người đại diện|thư ký)\b", re.IGNORECASE)
_ROMAN = re.compile(r"^([IVXLC]+)\s*[.)\-–:]\s+(?=\S)")
_ARABIC = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\s*[.)\-–:]?\s+(?=[^\d\s])")
_UPPER_ALPHA = re.compile(r"^([A-ZĐ])\s*[.)]\s+(?=\S)")
_KEYWORD = re.compile(r"^(phần|chương|mục|điều|phụ lục)\s+([IVXLC]+|\d+|[A-Z])\b", re.IGNORECASE)
_AMOUNT_ONLY = re.compile(r"[\d\s.,%()/:\-–+]*(?:đồng|vnd|vnđ|usd|triệu|tỷ|tỉ|nghìn|ngàn|lần|%)?[\d\s.,%()/:\-–+]*",
                          re.IGNORECASE)
_DATE = re.compile(r"\b\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}\b|ngày\s+\d+\s+tháng\s+\d+", re.IGNORECASE)
_PAGE_NUMBER = re.compile(r"^\s*(trang\s+)?\d{1,4}(\s*/\s*\d{1,4})?\s*$", re.IGNORECASE)
_TOC_LINE = re.compile(r"^(.{4,120}?)(?:\s*\.{2,}\s*|\s+)(\d{1,4})\s*$")
_TOC_TITLE = re.compile(r"\b(mục lục|table of contents|contents)\b", re.IGNORECASE)
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


@dataclass
class Line:
    item_id: str
    page: int
    text: str
    bbox: List[float]
    font_size: Optional[float]
    is_bold: bool
    block_type: str
    source: str
    confidence: Optional[float] = None
    quality_score: Optional[float] = None
    confidence_source: Optional[str] = None
    text_raw: Optional[str] = None
    correction_source: Optional[str] = None
    page_height: Optional[float] = None
    structure_needs_review: bool = False
    column_id: Optional[str] = None
    article_group_id: Optional[str] = None

    @property
    def height(self) -> float:
        return max(1.0, self.bbox[3] - self.bbox[1]) if len(self.bbox) == 4 else 1.0


@dataclass
class Heading:
    lines: List[Line]
    score: float
    signals: List[str]
    numbering: Optional[Tuple[str, Tuple[int, ...]]]
    size_ratio: Optional[float] = None  # font size / reference body size
    title_en: Optional[str] = None
    toc_confirmed: bool = False
    level: int = 0

    @property
    def text(self) -> str:
        return " ".join(line.text for line in self.lines)

    @property
    def page(self) -> int:
        return self.lines[0].page

    @property
    def font_size(self) -> Optional[float]:
        return self.lines[0].font_size

    @property
    def item_ids(self) -> List[str]:
        return [line.item_id for line in self.lines]


@dataclass
class TocInfo:
    pages: List[int]
    entries: List[Tuple[str, int]]
    page_offset: Optional[int] = None


@dataclass
class Paragraph:
    kind: str  # "paragraph" | "list_item"
    lines: List[Line]
    text: str

    @property
    def item_ids(self) -> List[str]:
        return [line.item_id for line in self.lines]


@dataclass
class Section:
    index: int
    heading: Heading
    parent: Optional[int] = None


@dataclass
class DocStructure:
    body_font_size: Optional[float]
    sections: List[Section]
    toc: Optional[TocInfo]
    # Reading-order sequence of the document body: ("heading", Section),
    # ("paragraph", Paragraph) or ("item", Item) for tables/formulas/images.
    flow: List[Tuple[str, object]] = field(default_factory=list)
    dropped: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------- features --

def normalize_title(text: str) -> str:
    # Remove case-sensitive Roman/letter prefixes before folding case.
    text = unicodedata.normalize("NFC", text).strip()
    text = re.sub(r"\.{2,}\s*\d*\s*$", "", text)
    for pattern in (_KEYWORD, _ROMAN, _ARABIC, _UPPER_ALPHA):
        text = pattern.sub("", text, count=1)
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.casefold().split())


def _roman_value(token: str) -> int:
    values = [_ROMAN_VALUES[c] for c in token]
    return sum(-v if i + 1 < len(values) and v < values[i + 1] else v for i, v in enumerate(values))


def parse_numbering(text: str) -> Optional[Tuple[str, Tuple[int, ...]]]:
    """("keyword"|"roman"|"upper_alpha"|"arabic", path) or None."""
    text = text.lstrip()
    match = _KEYWORD.match(text)
    if match:
        token = match.group(2).upper()
        value = int(token) if token.isdigit() else _roman_value(token) if set(token) <= set(_ROMAN_VALUES) else 0
        return "keyword", (value,)
    match = _ROMAN.match(text)
    if match:
        return "roman", (_roman_value(match.group(1)),)
    match = _ARABIC.match(text)
    if match:
        rest = text[match.end():]
        if re.match(r"\d{4}\b", rest) or re.match(r"\d+[.,]\d{3}", rest):
            return None
        return "arabic", tuple(int(part) for part in match.group(1).split("."))
    match = _UPPER_ALPHA.match(text)
    if match:
        return "upper_alpha", (ord(match.group(1)),)
    return None


def _letters(text: str) -> List[str]:
    return [c for c in text if c.isalpha()]


def _upper_ratio(text: str) -> float:
    letters = _letters(text)
    return sum(c.isupper() for c in letters) / len(letters) if letters else 0.0


def _is_english(text: str) -> bool:
    letters = [c.lower() for c in _letters(text)]
    return len(letters) >= 4 and sum(c in _VI_LETTERS for c in letters) <= 0.02 * len(letters) \
        and all(ord(c) < 128 for c in letters)


def _translation_candidate(line: Line, body: Optional[float] = None) -> bool:
    text = line.text.strip()
    if not _is_english(text) or len(text.split()) > 12 or text.endswith(_SENTENCE_END):
        return False
    positive_style = (line.block_type in ("title", "section_header") or line.is_bold
                      or _upper_ratio(text) >= 0.8
                      or bool(body and line.font_size and line.font_size / body >= 1.1))
    return positive_style and (body is None or score_line(line, body)[0] >= ACCEPT_SCORE - 1.0)


def rejection_reason(line: Line, previous: Sequence[Line] = ()) -> Optional[str]:
    """Reason a line cannot be a heading, or None. `previous` holds up to two
    preceding lines (a signature block may put a stamp between title and name)."""
    if len(line.bbox) != 4:
        return "unplaced"
    text = line.text.strip()
    numbering = parse_numbering(text)
    if _is_bullet(text):
        return "bullet"
    if _PAGE_NUMBER.match(text):
        return "page_number_like"
    stripped = normalize_title(text) if numbering else text
    if _AMOUNT_ONLY.fullmatch(stripped):
        return "amount_or_number"
    digits = sum(c.isdigit() for c in text)
    if digits > 0.5 * max(1, len(_letters(text))):
        return "mostly_digits"
    if _DATE.search(text) and len(text.split()) <= 6:
        return "date"
    if len(_letters(text)) < 3 and not numbering:
        return "too_short"
    if len(text.split()) > 25 or text.endswith(","):
        return "sentence_like"
    if re.search(r"\.{3,}\s*\d+$", text):
        return "toc_line"
    if text.endswith(":") and len(text.split()) <= 3:
        return "label"
    words = text.split()
    if (any(_JOB_TITLE.match(p.text.strip()) for p in previous) and 2 <= len(words) <= 5
            and not numbering and all(w[:1].isupper() for w in words) and not any(c.isdigit() for c in text)):
        return "person_name"
    if re.match(r"^(ông|bà)\s", text, re.IGNORECASE) and len(words) <= 6:
        return "person_name"
    return None


def score_line(line: Line, body: Optional[float]) -> Tuple[float, List[str]]:
    text = line.text.strip()
    score, signals = 0.0, []

    def add(points: float, signal: str) -> None:
        nonlocal score
        score += points
        signals.append(signal)

    # DLA labels are coarse (any short line high on page 1 is "title"), so
    # they support other evidence but cannot make a heading on their own.
    if line.block_type in ("title", "section_header"):
        add(1.0, f"block_type:{line.block_type}")
    numbering = parse_numbering(text)
    if numbering:
        add(2.5 if numbering[0] == "keyword" else 2.0, f"numbering:{numbering[0]}")
    ratio = line.font_size / body if line.font_size and body else None
    if ratio is not None and ratio >= 1.25:
        add(2.0, f"size_ratio:{ratio:.2f}")
    elif ratio is not None and ratio >= 1.10:
        add(1.0, f"size_ratio:{ratio:.2f}")
    if line.is_bold and (ratio is None or ratio >= 0.95):
        add(1.0, "bold")
    if _upper_ratio(text) >= 0.8 and len(_letters(text)) >= 4:
        add(1.0, "upper")
    if len(text) <= 90:
        add(0.5, "short")
    if len(text) > 140:
        add(-3.0, "long")
    elif len(text.split()) > 12 and _upper_ratio(text) < 0.5:
        add(-1.0, "long_mixed_case")  # numbered sentences are list items, not headings
    if text.endswith((".", ";", ",")) and not re.fullmatch(r"[\dIVXLC.]+", text):
        add(-1.5, "sentence_end")
    return score, signals


def body_font_size(lines: Sequence[Line]) -> Optional[float]:
    """Character-weighted median font size of native body lines."""
    weighted: List[float] = []
    for line in lines:
        if line.font_size and line.block_type not in _EXCLUDED_TYPES:
            weighted.extend([line.font_size] * max(1, min(len(line.text), 200)))
    return statistics.median(weighted) if weighted else None


# --------------------------------------------------------------------- TOC --

def detect_toc(pages: Sequence[Sequence[Line]], max_page_index: int = 8) -> Optional[TocInfo]:
    """A table of contents among the first pages: a "MỤC LỤC"/"CONTENTS" title
    plus >= 4 entries, or >= 6 entries alone. An entry is a line ending in a
    page number, or a text line with a bare page number to its right."""
    toc_pages: List[int] = []
    entries: List[Tuple[str, int]] = []
    for lines in list(pages)[:max_page_index]:
        found = _toc_entries(lines)
        titled = any(_TOC_TITLE.search(line.text) for line in lines[: max(3, len(lines) // 3)])
        titled = titled or any(line.block_type.casefold().replace("-", "_") == "document_index"
                              for line in lines)
        if len(found) >= (4 if titled else 6):
            toc_pages.append(lines[0].page)
            entries.extend(found)
    return TocInfo(toc_pages, entries) if toc_pages else None


def _toc_entries(lines: Sequence[Line]) -> List[Tuple[str, int]]:
    return [(title, page) for title, page, _ in _toc_entries_with_sources(lines)]


def _toc_entries_with_sources(lines: Sequence[Line]) -> List[Tuple[str, int, List[str]]]:
    entries = []
    numbers = [line for line in lines if len(line.bbox) == 4 and re.fullmatch(r"\d{1,3}", line.text.strip())]
    for line in lines:
        text = line.text.strip()
        if line in numbers:
            continue
        match = _TOC_LINE.match(text)
        if match and len(_letters(match.group(1))) >= 3:
            entries.append((normalize_title(match.group(1)), int(match.group(2)), [line.item_id]))
            continue
        if len(_letters(text)) < 3 or len(line.bbox) != 4:
            continue
        mid = (line.bbox[1] + line.bbox[3]) / 2
        same_row = [n for n in numbers if n.bbox[0] >= line.bbox[2] - 1
                    and abs((n.bbox[1] + n.bbox[3]) / 2 - mid) <= 0.6 * line.height]
        if same_row:
            nearest = min(same_row, key=lambda n: n.bbox[0] - line.bbox[2])
            entries.append((normalize_title(text), int(nearest.text), [line.item_id, nearest.item_id]))
    return entries


def apply_toc(accepted: List[Heading], near_misses: List[Heading], toc: TocInfo) -> List[Heading]:
    """Mark single-line candidates that match a TOC entry, promote matching
    near misses (a TOC match alone never creates a heading), and estimate the
    printed-page offset. Returns the newly promoted near misses."""
    offsets: List[int] = []
    promoted: List[Heading] = []
    for heading in [*accepted, *near_misses]:
        norm = normalize_title(heading.text)
        best, best_page = 0.0, None
        for title, printed in toc.entries:
            ratio = difflib.SequenceMatcher(None, norm, title).ratio() if title and norm else 0.0
            if ratio > best:
                best, best_page = ratio, printed
        if best < TOC_MATCH_RATIO:
            continue
        heading.toc_confirmed = True
        heading.signals.append(f"toc:{best:.2f}")
        offsets.append(heading.page - best_page)
        if heading.score < ACCEPT_SCORE:
            heading.score += 2.0
            promoted.append(heading)
    if len(offsets) >= 3:
        common = statistics.mode(offsets)
        if offsets.count(common) >= 0.6 * len(offsets):
            toc.page_offset = common
    return promoted


# ---------------------------------------------------------------- headings --

def _same_style(a: Line, b: Line) -> bool:
    if (a.font_size is None) != (b.font_size is None):
        return False
    if a.font_size is not None and abs(a.font_size - b.font_size) > max(0.5, 0.08 * max(a.font_size, b.font_size)):
        return False
    return a.is_bold == b.is_bold


def _aligned(a: Line, b: Line) -> bool:
    if len(a.bbox) != 4 or len(b.bbox) != 4:
        return False
    left = abs(a.bbox[0] - b.bbox[0]) <= 6
    centered = abs((a.bbox[0] + a.bbox[2]) / 2 - (b.bbox[0] + b.bbox[2]) / 2) <= 8
    return left or centered


def _adjacent(a: Line, b: Line) -> bool:
    if len(a.bbox) != 4 or len(b.bbox) != 4:
        return False
    # Tight heading leading makes consecutive line boxes overlap (negative gap).
    return a.page == b.page and -0.5 * a.height <= b.bbox[1] - a.bbox[3] <= 0.8 * a.height


def merge_headings(candidates: List[Tuple[int, Heading]]) -> List[Heading]:
    """Merge broken heading lines and attach English translations. Entries are
    (line position, heading); only directly consecutive lines can merge."""
    merged: List[Tuple[int, Heading]] = []
    for position, heading in candidates:
        if merged:
            last_position, last = merged[-1]
            previous, current = last.lines[-1], heading.lines[0]
            consecutive = position == last_position + 1 and _adjacent(previous, current) and _aligned(previous, current)
            if consecutive and heading.numbering is None and last.title_en is None:
                smaller = (current.font_size is not None and previous.font_size is not None
                           and current.font_size <= 0.95 * previous.font_size)
                if _translation_candidate(current) and not _is_english(last.text) and (
                        smaller or current.font_size is None):
                    last.title_en = current.text
                    last.lines.append(current)
                    merged[-1] = (position, last)
                    continue
                if _same_style(previous, current) and not previous.text.rstrip().endswith("."):
                    last.lines.append(current)
                    last.signals = sorted(set(last.signals) | set(heading.signals))
                    last.toc_confirmed = last.toc_confirmed or heading.toc_confirmed
                    merged[-1] = (position, last)
                    continue
        merged.append((position, heading))
    return [heading for _, heading in merged]


def size_level(ratio: Optional[float]) -> Optional[int]:
    """Level from font size relative to the body, in log-spaced bands. Annual
    reports use many slightly different heading sizes (17 on 20 ADP pages), so
    ranking distinct sizes runs out of levels; bands do not."""
    if ratio is None:
        return None
    for level, lower in enumerate(LEVEL_SIZE_BANDS, 1):
        if ratio >= lower:
            return level
    return MAX_LEVEL


def assign_levels(headings: List[Heading]) -> None:
    """Combine size bands with keyword/numbering hierarchy. Same-depth
    numbered siblings share a level; descending never skips a level."""
    previous = 0
    numbering_anchors: Dict[str, Tuple[int, int]] = {}
    keyword_rank = {"phần": 0, "phụ lục": 0, "chương": 1, "mục": 2, "điều": 3}
    present_ranks = sorted({keyword_rank[match.group(1).casefold()]
                            for heading in headings if (match := _KEYWORD.match(heading.text.lstrip()))})
    for heading in headings:
        level = size_level(heading.size_ratio)
        kind, path = heading.numbering or (None, ())
        if level is None:
            level = 1 if kind in ("keyword", "roman") else 2
        if kind == "keyword":
            match = _KEYWORD.match(heading.text.lstrip())
            rank = keyword_rank[match.group(1).casefold()]
            level = present_ranks.index(rank) + 1
            numbering_anchors.clear()
        elif kind:
            depth = len(path)
            if kind in numbering_anchors:
                first_depth, first_level = numbering_anchors[kind]
                level = max(1, first_level + depth - first_depth)
            else:
                level = max(level, depth) if kind == "arabic" else level
                # Keep siblings at the same level, including OCR headings
                # without font metadata. Previously successive "1.", "2."
                # could become a parent and child merely due to clipping.
                numbering_anchors[kind] = (depth, min(level, MAX_LEVEL, previous + 1))
        level = min(level, MAX_LEVEL, previous + 1)
        heading.level = max(1, level)
        previous = heading.level


# -------------------------------------------------------------- paragraphs --

def _is_bullet(text: str) -> bool:
    text = text.lstrip()
    if not text or text[0] not in _BULLETS:
        return False
    if len(text) == 1:
        return True
    if not text[1].isspace():
        return False
    # Minus/plus before a value is a numeric sign, not a list marker.
    rest = text[1:].lstrip()
    return not (text[0] in "-+–" and rest[:1].isdigit())


def _starts_new_paragraph(previous: Line, line: Line) -> bool:
    if len(previous.bbox) != 4 or len(line.bbox) != 4:
        return True
    if (previous.column_id != line.column_id or previous.article_group_id != line.article_group_id):
        return True
    if line.page not in (previous.page, previous.page + 1):
        # Selected-page extraction may omit intervening pages. Their text is
        # unknown; joining across that gap would invent a continuous sentence.
        return True
    if _is_bullet(line.text) or parse_numbering(line.text):
        return True
    if previous.font_size and line.font_size and abs(previous.font_size - line.font_size) > 1.5:
        return True
    gap = line.bbox[1] - previous.bbox[3]
    if line.page != previous.page or gap < 0:
        # Page or column break: continue only an unfinished sentence.
        return previous.text.rstrip().endswith(_SENTENCE_END) or not _starts_lowercase(line.text)
    if gap > 0.6 * previous.height:
        return True
    if line.bbox[0] - previous.bbox[0] > 12 and previous.text.rstrip().endswith(_SENTENCE_END):
        return True
    return previous.text.rstrip().endswith(_SENTENCE_END) and not _starts_lowercase(line.text)


def _starts_lowercase(text: str) -> bool:
    letters = _letters(text)
    return bool(letters) and letters[0].islower()


def _join(lines: Sequence[Line]) -> str:
    text = ""
    for line in lines:
        part = line.text.strip()
        if not text:
            text = part
        elif re.search(r"[^\W\d_]-$", text, re.UNICODE) and _starts_lowercase(part):
            text = text[:-1] + part
        else:
            text = f"{text} {part}"
    return text


# ------------------------------------------------------------------- build --

def _line(item: Item, needs_review: bool = False) -> Line:
    block = item.block
    return Line(item_id=item.id, page=item.page, text=block.text, bbox=item.bbox,
                font_size=block.font_size, is_bold=bool(block.is_bold), block_type=block.block_type,
                source=block.source, confidence=block.confidence, quality_score=block.quality_score,
                confidence_source=block.confidence_source, text_raw=getattr(block, "text_raw", None),
                correction_source=getattr(block, "correction_source", None), page_height=item.page_height,
                structure_needs_review=needs_review or len(item.bbox) != 4 or bool(block.review_reason_codes), column_id=block.column_id,
                article_group_id=block.article_group_id)


def _overlapping_tables(item: Item, tables: Sequence[Item]) -> List[Item]:
    if len(item.bbox) != 4:
        return []
    x0, y0, x1, y1 = item.bbox
    area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if not area:
        return []
    return [t for t in tables if len(t.bbox) == 4 and (
        max(0.0, min(x1, t.bbox[2]) - max(x0, t.bbox[0]))
        * max(0.0, min(y1, t.bbox[3]) - max(y0, t.bbox[1])) / area >= 0.8
    )]


def _inside_table(item: Item, tables: Sequence[Item]) -> bool:
    # Geometry alone cannot prove that the table model retained this text.
    # Require an exact normalized phrase match, preserving numeric punctuation.
    text = " ".join(unicodedata.normalize("NFC", item.block.text).casefold().split())
    if not text:
        return False
    for table in _overlapping_tables(item, tables):
        cells = " ".join(cell.text for cell in sorted(table.table.cells, key=lambda c: (c.row_idx, c.col_idx)))
        cells = " ".join(unicodedata.normalize("NFC", cells).casefold().split())
        if cells and f" {text} " in f" {cells} ":
            return True
    return False


def build_structure(items_by_page: Sequence[Sequence[Item]], *, layout_method: str = "legacy") -> DocStructure:
    dropped: Dict[str, str] = {}
    # 1. Body text lines in reading order; everything else stays an item.
    sequence: List[Tuple[str, object]] = []
    for items in items_by_page:
        tables = [item for item in items if item.kind == "t"]
        for item in items:
            if item.kind != "b":
                sequence.append(("item", item))
                continue
            block = item.block
            peripheral = ("header", "footer", "running_header", "running_footer", "page_number") if layout_method == "legacy" else ("running_header", "running_footer", "page_number")
            if block.block_type in peripheral:
                dropped[item.id] = block.block_type
            elif not block.text.strip():
                dropped[item.id] = "empty"
            elif _inside_table(item, tables):
                dropped[item.id] = "table_cell"
            elif block.block_type == "formula":
                sequence.append(("item", item))
            else:
                sequence.append(("line", _line(item, needs_review=bool(_overlapping_tables(item, tables)))))

    lines = [value for kind, value in sequence if kind == "line"]
    body = body_font_size(lines)
    pages = _lines_by_page(lines)
    toc = detect_toc(pages)
    toc_pages = set(toc.pages) if toc else set()
    toc_item_ids = {item_id for page_lines in pages if page_lines[0].page in toc_pages
                    for _, _, refs in _toc_entries_with_sources(page_lines) for item_id in refs}
    # A text-heavy page set in a larger font than the document body (e.g. a
    # 12pt cover letter in a 7pt report) is measured against its own body.
    reference: Dict[int, Optional[float]] = {}
    for page_lines in pages:
        page_body = body_font_size(page_lines)
        chars = sum(len(line.text) for line in page_lines)
        use_page = page_body and body and chars >= PAGE_BODY_MIN_CHARS and page_body > body
        reference[page_lines[0].page] = page_body if use_page else body

    # 2. Single-line heading candidates, scored in reading order.
    accepted: List[Tuple[int, Heading]] = []
    near_misses: List[Tuple[int, Heading]] = []
    recent: List[Line] = []
    for position, (kind, value) in enumerate(sequence):
        if kind != "line":
            recent = []
            continue
        line = value
        if line.item_id in toc_item_ids or (line.page in toc_pages and _TOC_TITLE.search(line.text)):
            # A TOC page can also contain an introduction. Suppress index
            # rows, not every line on the page.
            dropped[line.item_id] = "toc_entry"
            continue
        reason = rejection_reason(line, recent) if line.block_type not in _EXCLUDED_TYPES else line.block_type
        recent = (recent + [line])[-2:]
        if reason:
            continue
        page_reference = reference.get(line.page, body)
        score, signals = score_line(line, page_reference)
        ratio = line.font_size / page_reference if line.font_size and page_reference else None
        heading = Heading([line], score, signals, parse_numbering(line.text), size_ratio=ratio)
        if score >= ACCEPT_SCORE:
            accepted.append((position, heading))
        elif score >= ACCEPT_SCORE - 1.5:
            near_misses.append((position, heading))

    if toc:
        promoted = {id(h) for h in apply_toc([h for _, h in accepted], [h for _, h in near_misses], toc)}
        accepted = sorted(accepted + [(p, h) for p, h in near_misses if id(h) in promoted], key=lambda pair: pair[0])
    headings = merge_headings(accepted)
    _attach_translations(headings, sequence, reference)
    if layout_method == "legacy":
        headings = _drop_repeated_titles(headings, dropped)
    assign_levels(headings)

    # 3. Sections with parents, and the reading-order flow with paragraphs.
    sections: List[Section] = []
    stack: List[Section] = []
    heading_by_line = {h.lines[0].item_id: h for h in headings}
    heading_lines = {line.item_id for h in headings for line in h.lines}
    flow: List[Tuple[str, object]] = []
    paragraph: List[Line] = []

    def flush() -> None:
        if not paragraph:
            return
        text = _join(paragraph)
        if not text.strip("".join(_BULLETS) + " "):
            # A lone bullet glyph or rule: decoration, not content.
            for line in paragraph:
                dropped[line.item_id] = "decoration"
        else:
            kind = "list_item" if _is_bullet(paragraph[0].text) else "paragraph"
            flow.append(("paragraph", Paragraph(kind, list(paragraph), text)))
        paragraph.clear()

    for kind, value in sequence:
        if kind == "item":
            flush()
            flow.append(("item", value))
            continue
        line = value
        if line.item_id in heading_by_line:
            flush()
            heading = heading_by_line[line.item_id]
            while stack and stack[-1].heading.level >= heading.level:
                stack.pop()
            section = Section(len(sections) + 1, heading, stack[-1].index if stack else None)
            sections.append(section)
            stack.append(section)
            flow.append(("heading", section))
            continue
        if line.item_id in heading_lines or line.item_id in dropped:
            continue
        # A bare bullet glyph on its own line prefixes the next line.
        if paragraph and paragraph[-1].text.strip() in _BULLETS:
            paragraph.append(line)
            continue
        if paragraph and _starts_new_paragraph(paragraph[-1], line):
            flush()
        paragraph.append(line)
    flush()
    return DocStructure(body, sections, toc, flow, dropped)


def _attach_translations(headings: List[Heading], sequence: Sequence[Tuple[str, object]],
                         reference: Optional[Dict[int, Optional[float]]] = None) -> None:
    """An English line set directly under a Vietnamese heading, in a smaller
    font, is its translation even when it scores too low to be a heading."""
    position = {value.item_id: i for i, (kind, value) in enumerate(sequence) if kind == "line"}
    heading_lines = {line.item_id for h in headings for line in h.lines}
    for heading in headings:
        if heading.title_en is not None or _is_english(heading.text):
            continue
        last = heading.lines[-1]
        following = position[last.item_id] + 1
        if following >= len(sequence) or sequence[following][0] != "line":
            continue
        candidate: Line = sequence[following][1]
        smaller = (candidate.font_size is None or last.font_size is None
                   or candidate.font_size <= 0.95 * last.font_size)
        if (candidate.item_id not in heading_lines
                and _translation_candidate(candidate, (reference or {}).get(candidate.page)) and smaller
                and _adjacent(last, candidate) and _aligned(last, candidate)):
            heading.title_en = candidate.text
            heading.lines.append(candidate)
            heading_lines.add(candidate.item_id)


def _drop_repeated_titles(headings: List[Heading], dropped: Dict[str, str]) -> List[Heading]:
    """Reports repeat the section title at the top of every page of a section
    (twice on two-up spreads). Suppression requires the repeat to be near the
    page top and in the same style; a matching body heading is preserved."""
    last_seen: Dict[str, Heading] = {}
    kept: List[Heading] = []
    for heading in headings:
        # Numbering identifies genuine new sections with the same title.
        # Keep it here; normalize_title intentionally strips it for TOC matches.
        key = " ".join(unicodedata.normalize("NFC", heading.text).casefold().split())
        seen = last_seen.get(key)
        last_seen[key] = heading
        line = heading.lines[0]
        near_top = bool(line.page_height and line.bbox[1] <= RUNNING_TITLE_TOP_RATIO * line.page_height)
        if (key and seen is not None and near_top and _same_style(seen.lines[0], line)
                and 0 < heading.page - seen.page <= REPEAT_WINDOW_PAGES):
            for line in heading.lines:
                dropped[line.item_id] = "repeated_heading"
            continue
        kept.append(heading)
    return kept


def _lines_by_page(lines: Sequence[Line]) -> List[List[Line]]:
    pages: Dict[int, List[Line]] = {}
    for line in lines:
        pages.setdefault(line.page, []).append(line)
    return [pages[page] for page in sorted(pages)]

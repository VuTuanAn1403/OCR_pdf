"""Financial token and header predicates; no source-text rewriting."""
from __future__ import annotations

import re
import unicodedata


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

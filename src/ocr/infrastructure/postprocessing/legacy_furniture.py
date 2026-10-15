"""Frozen V6.1 furniture policy for reproducible legacy runs."""
from typing import Dict
from src.ocr.domain.models.document import ExtractedDocument

def resolve_legacy_furniture(doc: ExtractedDocument) -> None:
    """
    Detects repeated header and footer strings across distinct pages (>= 30% distinct page frequency).
    Marks matching blocks as running_header or running_footer (Proposal A2 / Review V5 Item 9).
    Enforces distinct page counting, position verification, and math.ceil rounding.
    """
    total_pages = len(doc.pages)
    if total_pages < 3:
        return

    import unicodedata
    import re
    import math
    from collections import defaultdict

    def _norm(text: str) -> str:
        t = unicodedata.normalize("NFC", text.strip().lower())
        t = re.sub(r"\d+", "", t)
        t = re.sub(r"[^\w\s]", "", t)
        # Whitespace-free key: letter-spaced variants such as
        # "BÁO CÁ O THƯỜNG NIÊN" must match the same running header (H6).
        return "".join(t.split())

    # Count DISTINCT pages where header or footer appears
    header_pages: Dict[str, set] = defaultdict(set)
    footer_pages: Dict[str, set] = defaultdict(set)

    for p in doc.pages:
        p_h = float(getattr(p, "height", 0) or 0)
        for b in p.blocks:
            if b.block_type == "formula":
                continue
            norm_str = _norm(b.text)
            if not norm_str or len(norm_str) < 4:
                continue

            b_ymid = ((b.bbox[1] + b.bbox[3]) / 2.0) if (b.bbox and len(b.bbox) >= 4) else None

            # Position checks: Header within top 15%, Footer within bottom 15%
            if b.block_type == "header":
                if p_h <= 0 or b_ymid is None or b_ymid <= 0.15 * p_h:
                    header_pages[norm_str].add(p.page_num)
            elif b.block_type == "footer":
                if p_h <= 0 or b_ymid is None or b_ymid >= 0.85 * p_h:
                    footer_pages[norm_str].add(p.page_num)

    # Threshold using math.ceil(total_pages * 0.30)
    min_distinct_pages = max(2, math.ceil(total_pages * 0.30))
    running_headers = {k for k, p_set in header_pages.items() if len(p_set) >= min_distinct_pages}
    running_footers = {k for k, p_set in footer_pages.items() if len(p_set) >= min_distinct_pages}

    for p in doc.pages:
        p_h = float(getattr(p, "height", 0) or 0)
        for b in p.blocks:
            norm_str = _norm(b.text)
            b_ymid = ((b.bbox[1] + b.bbox[3]) / 2.0) if (b.bbox and len(b.bbox) >= 4) else None
            if b.block_type == "header" and norm_str in running_headers:
                if p_h <= 0 or b_ymid is None or b_ymid <= 0.15 * p_h:
                    b.block_type = "running_header"
            elif b.block_type == "footer" and norm_str in running_footers:
                if p_h <= 0 or b_ymid is None or b_ymid >= 0.85 * p_h:
                    b.block_type = "running_footer"

"""Single document-context authority for running furniture decisions.

Conservative baseline: exact NFC/case/whitespace matching with stable geometry,
distinct physical pages and local template support. No OCR correction, digit
erasure or independent title suppression happens here.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import statistics
from collections import defaultdict

from src.ocr.infrastructure.structure.source_identity import valid_bbox

METHOD = "document_context_v2"
_UNIT = re.compile(r"^\s*(đơn vị tính|đvt)\s*[:：]", re.I)
_CONTINUED = re.compile(r"\(\s*tiếp(?:\s+theo)?\s*\)", re.I)
_SECTION = re.compile(r"^\s*(?:(?:chương|phần|mục|điều|phụ lục)\b|\d+(?:\.\d+)*[.)]\s|[IVXLC]+[.)]\s)", re.I)
_ROMAN = r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
_PAGE = re.compile(r"^(?:trang\s+)?(?:\d{1,4}|(?=[ivxlcdm])" + _ROMAN + r")(?:\s*/\s*\d{1,4})?$", re.I)
_LABELLED_PAGE = re.compile(r"\btrang\s+\d{1,4}(?:\s*/\s*\d{1,4})?\b", re.I)


def is_page_number_text(text):
    return bool(_PAGE.fullmatch(text.strip()))


def _key(text):
    # Only a labelled page number is variable. Years/codes remain significant.
    text = _LABELLED_PAGE.sub("trang #", unicodedata.normalize("NFC", text).casefold())
    return " ".join(text.split())


def protect_table_continuation_lines(pages):
    for page in pages:
        for block in page.blocks:
            if _UNIT.search(block.text):
                reason = "TABLE_UNIT"
            elif _CONTINUED.search(block.text):
                reason = "TABLE_CONTINUATION_TITLE"
            elif block.block_type == "table_cell":
                reason = "TABLE_COLUMN_HEADER"
            else:
                continue
            block.protected_from_furniture = True
            block.protection_reason = reason
            block.table_candidate_id = block.table_candidate_id or f"p{page.page_num}-table-provisional"
            if "TABLE_OWNERSHIP_PROVISIONAL" not in block.review_reason_codes:
                block.review_reason_codes.append("TABLE_OWNERSHIP_PROVISIONAL")


class DocumentFurnitureResolver:
    def __init__(self, *, min_pages=3, window=12, support_ratio=.6, position_tolerance=.025):
        if min_pages < 3 or window < min_pages or not 0 < support_ratio <= 1 or position_tolerance <= 0:
            raise ValueError("Invalid furniture evidence thresholds")
        self.min_pages, self.window = min_pages, window
        self.support_ratio, self.position_tolerance = support_ratio, position_tolerance

    def resolve(self, doc):
        protect_table_continuation_lines(doc.pages)
        groups = defaultdict(list)
        context = []
        for page in doc.pages:
            body_sizes = [b.font_size for b in page.blocks if b.font_size and len(b.text.strip()) >= 30]
            body_size = statistics.median(body_sizes) if body_sizes else None
            physical = str(page.metadata.get("physical_page_id", f"p{page.page_num:04d}"))
            logical = str(page.metadata.get("logical_page_id", f"p{page.page_num:04d}"))
            for block in page.blocks:
                if block.role_initial is None:
                    block.role_initial = block.block_type
                # Recompute from initial evidence when selected pages/context change.
                block.block_type = block.role_initial
                if block.block_type in ("running_header", "running_footer"):
                    block.block_type = block.block_type.removeprefix("running_")
                block.role_final, block.role_source = block.block_type, METHOD
                block.role_status, block.visibility = "KEPT", "visible"
                block.suppression_reason, block.template_id = None, None
                block.support_page_ids, block.role_reasons = [], []
                block.review_reason_codes = [c for c in block.review_reason_codes
                                             if c not in ("HEADER_BODY_CONFLICT", "PAGE_NUMBER_BODY_CONFLICT")]
                context.append((physical, logical, block.source_id, block.text,
                                block.bbox, block.role_initial, block.protected_from_furniture,
                                block.font_size, block.is_bold, page.width, page.height))
                if block.protected_from_furniture:
                    if block.block_type in ("header", "footer", "page_number"):
                        block.block_type = "paragraph"
                    block.role_final = block.block_type
                    block.role_reasons = [block.protection_reason]
                    continue
                if block.block_type == "page_number" and not is_page_number_text(block.text):
                    # A coarse/model label must not hide a stock code or word
                    # that fails the actual pagination syntax.
                    block.block_type = block.role_final = "paragraph"
                    block.role_status = "NEEDS_REVIEW"
                    block.role_reasons = ["INVALID_PAGE_NUMBER_TEXT"]
                    block.review_reason_codes.append("PAGE_NUMBER_BODY_CONFLICT")
                    continue
                if not valid_bbox(block.bbox) or page.height <= 0 or page.width <= 0:
                    continue
                mid = (block.bbox[1] + block.bbox[3]) / (2 * page.height)
                edge = "header" if mid <= .20 else "footer" if mid >= .80 else None
                semantic = block.block_type in ("header", "footer", "page_number")
                styled = bool(block.is_bold or (body_size and block.font_size and block.font_size >= 1.25 * body_size))
                heading = bool(mid <= .15 and (styled or block.block_type in ("title", "section_header")))
                if (block.block_type in ("footnote", "caption", "formula", "table_cell")
                        or _SECTION.search(block.text) or block.block_type in ("title", "section_header") and not heading):
                    if semantic:
                        block.block_type = block.role_final = "paragraph"
                        block.role_status = "NEEDS_REVIEW"
                        block.review_reason_codes.append("HEADER_BODY_CONFLICT")
                    continue
                if edge and is_page_number_text(block.text):
                    # Unlabelled four-digit values can be years/business data.
                    bare = block.text.strip()
                    if semantic or bare.lower().startswith("trang") or (mid <= .06 or mid >= .94) and not re.fullmatch(r"\d{4}", bare):
                        block.block_type = block.role_final = "page_number"
                        block.visibility, block.role_status = "metadata", "ACCEPTED"
                        block.role_reasons = ["PAGE_NUMBER_PATTERN_AT_EDGE"]
                    continue
                role = block.block_type if block.block_type in ("header", "footer") else edge
                if not role or len(block.text.strip()) < 4 or len(block.text) > 160 or len(block.text.splitlines()) > 2:
                    continue
                # A semantic proposal can lie outside the initial margin band.
                key = _key(block.text)
                groups[(role, key, heading)].append((page, block, physical, logical, mid,
                                           block.bbox[0] / page.width,
                                           (block.bbox[3] - block.bbox[1]) / page.height,
                                           block.font_size, bool(block.is_bold)))
                block.role_reasons = ["FURNITURE_CANDIDATE"]
        for (role, key, heading), occurrences in groups.items():
            for page, block, physical, logical, mid, left, height, font, bold in occurrences:
                semantic = block.role_initial in ("header", "footer", "page_number", "running_header", "running_footer")
                compatible = [o for o in occurrences if abs(o[4] - mid) <= self.position_tolerance
                         and abs(o[5] - left) <= self.position_tolerance
                         and abs(o[6] - height) <= .01
                         and (not heading or o[8] == bold and (font is None and o[7] is None
                              or font is not None and o[7] is not None and abs(font - o[7]) <= max(.5, .05 * font)))]
                local = [o for o in compatible if abs(o[0].page_num - page.page_num) <= self.window]
                # Try full template first, then parity template. Subpages on the
                # same physical sheet never inflate independent support.
                accepted = []
                for parity in (None, page.page_num % 2):
                    support = [o for o in local if parity is None or o[0].page_num % 2 == parity]
                    if not support:
                        continue
                    low, high = min(o[0].page_num for o in support), max(o[0].page_num for o in support)
                    eligible = {str(p.metadata.get("physical_page_id", f"p{p.page_num:04d}")) for p in doc.pages
                                if low <= p.page_num <= high and (parity is None or p.page_num % 2 == parity)}
                    distinct = {o[2] for o in support}
                    if len(distinct) >= self.min_pages and len(distinct) / max(1, len(eligible)) >= self.support_ratio:
                        accepted = support
                        break
                if accepted:
                    # Repeated section titles keep the first actual section
                    # heading. Only subsequent same-style margin occurrences
                    # become running furniture; numbered/body headings stay out.
                    if heading:
                        segments = []
                        for number in sorted({o[0].page_num for o in compatible}):
                            if not segments or number - segments[-1][-1] > 4:
                                segments.append([number])
                            else:
                                segments[-1].append(number)
                        anchor = next(s[0] for s in segments if page.page_num in s)
                        if page.page_num == anchor:
                            block.role_reasons = ["REPEATED_SECTION_TITLE_ANCHOR", "STABLE_STYLE"]
                            block.support_page_ids = sorted({o[3] for o in accepted})
                            continue
                    block.block_type = block.role_final = f"running_{role}"
                    block.visibility, block.role_status = "suppressed", "ACCEPTED"
                    block.suppression_reason = "DISTINCT_PAGE_TEMPLATE_REPEAT"
                    block.support_page_ids = sorted({o[3] for o in accepted})
                    block.role_reasons = ["EXACT_TEXT", "STABLE_GEOMETRY", "DISTINCT_PHYSICAL_PAGES"]
                    block.template_id = hashlib.sha256(f"{role}:{key}:{round(mid, 2)}:{round(left, 2)}".encode()).hexdigest()[:16]
                elif semantic and len({o[2] for o in occurrences}) >= self.min_pages and len({o[2] for o in compatible}) < self.min_pages:
                    block.role_status = "NEEDS_REVIEW"
                    block.review_reason_codes.append("HEADER_BODY_CONFLICT")
        parameters = {"min_pages": self.min_pages, "window": self.window, "support_ratio": self.support_ratio,
                      "position_tolerance": self.position_tolerance}
        payload = json.dumps({"evidence": context, "method": METHOD, "parameters": parameters}, ensure_ascii=False, sort_keys=True)
        report = {"method": METHOD,
                  "context_scope": "full_document" if {p.page_num for p in doc.pages} == set(range(1, doc.total_pages + 1)) else "selected_pages",
                  "context_page_ids": [str(p.metadata.get("logical_page_id", f"p{p.page_num:04d}")) for p in doc.pages],
                  "context_hash": hashlib.sha256(payload.encode()).hexdigest(),
                  "quality_acceptance": "NOT_EVALUATED",
                  "parameters": parameters}
        doc.manifest["furniture"] = report
        return report

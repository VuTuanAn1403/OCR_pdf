"""Single document-context authority for running furniture decisions.

Content guards precede cross-page token association. The learned templates,
region boundaries and pagination offsets remain inspectable; OCR text is never
rewritten to make a template match.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import statistics

from src.ocr.infrastructure.structure.source_identity import valid_bbox
from src.ocr.infrastructure.postprocessing.page_association import Occurrence, PageAssociation

METHOD = "document_context_v3"
_UNIT = re.compile(r"^\s*(đơn vị tính|đvt)\s*[:：]", re.I)
_CONTINUED = re.compile(r"\(\s*tiếp(?:\s+theo)?\s*\)", re.I)
_SECTION = re.compile(r"^\s*(?:(?:chương|phần|mục|điều|phụ lục)\b|\d+(?:\.\d+)*[.)]\s|\d+(?:\.\d+)+\s|[IVXLCDM]+[.)]\s|[A-Z][.)]\s)", re.I)
_ROMAN = r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
_PAGE = re.compile(r"^(?:trang\s+)?(?:\d{1,4}|(?=[ivxlcdm])" + _ROMAN + r")(?:\s*/\s*\d{1,4})?$", re.I)
_LIST = re.compile(r"^\s*(?:[-*•–+]|\(\d+\)|\d+\))\s+")
_CAPTION = re.compile(r"^\s*(?:hình|bảng|ảnh|sơ đồ|biểu đồ|figure|table|fig\.)\s+\d+[:.\-]", re.I)
_PROPOSALS = {"header", "footer", "running_header", "running_footer", "page_number"}


def is_page_number_text(text):
    return bool(_PAGE.fullmatch(text.strip()))


def _key(text):
    # A variable number is normalized only AFTER a pagination sequence fits.
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


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
    def __init__(self, *, min_pages=3, window=12, support_ratio=.6, position_tolerance=.025, edge_ratio=.20,
                 token_similarity_threshold=.96, learning_pages=20):
        from src.ocr.runtime.config import furniture_settings
        parameters = furniture_settings({"furniture": dict(min_pages=min_pages, window=window,
            support_ratio=support_ratio, position_tolerance=position_tolerance, edge_ratio=edge_ratio,
            token_similarity_threshold=token_similarity_threshold, learning_pages=learning_pages)})
        for name, value in parameters.items():
            if name != "method":
                setattr(self, name, value)

    def resolve(self, doc):
        protect_table_continuation_lines(doc.pages)
        occurrences = []
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
                semantic = block.role_initial in _PROPOSALS
                # An initial model/old-margin label is evidence, not authority
                # to remove content. Keep it in role_initial for the audit.
                if semantic:
                    block.block_type = ("caption" if _CAPTION.search(block.text) else
                                        "list" if _LIST.search(block.text) else
                                        "section_header" if _SECTION.search(block.text) else "paragraph")
                block.role_final, block.role_source = block.block_type, METHOD
                block.role_status, block.visibility = ("NEEDS_REVIEW" if semantic else "KEPT"), "visible"
                block.suppression_reason, block.template_id = None, None
                block.support_page_ids, block.role_reasons = [], []
                block.review_reason_codes = [c for c in block.review_reason_codes
                                             if c not in ("HEADER_BODY_CONFLICT", "PAGE_NUMBER_BODY_CONFLICT")]
                if semantic:
                    block.role_reasons = ["UNCONFIRMED_FURNITURE_PROPOSAL"]
                    block.review_reason_codes.append("PAGE_NUMBER_BODY_CONFLICT" if block.role_initial == "page_number" else "HEADER_BODY_CONFLICT")
                context.append((physical, logical, block.source_id, block.text,
                                block.bbox, block.role_initial, block.protected_from_furniture,
                                block.font_size, block.is_bold, page.width, page.height))
                if block.protected_from_furniture:
                    if block.block_type in ("header", "footer", "page_number"):
                        block.block_type = "paragraph"
                    block.role_final = block.block_type
                    block.role_status = "KEPT"
                    block.review_reason_codes = [c for c in block.review_reason_codes
                                                 if c not in ("HEADER_BODY_CONFLICT", "PAGE_NUMBER_BODY_CONFLICT")]
                    block.role_reasons = [block.protection_reason]
                    continue
                if block.role_initial == "page_number" and not is_page_number_text(block.text):
                    # A coarse/model label must not hide a stock code or word
                    # that fails the actual pagination syntax.
                    block.block_type = block.role_final = "paragraph"
                    block.role_status = "NEEDS_REVIEW"
                    block.role_reasons = ["INVALID_PAGE_NUMBER_TEXT"]
                    continue
                if not valid_bbox(block.bbox) or page.height <= 0 or page.width <= 0:
                    continue
                mid = (block.bbox[1] + block.bbox[3]) / (2 * page.height)
                # Candidate search band, never a decision to suppress text.
                edge = "header" if mid <= self.edge_ratio else "footer" if mid >= 1 - self.edge_ratio else None
                styled = bool(block.is_bold or (body_size and block.font_size and block.font_size >= 1.25 * body_size))
                heading = bool(mid <= .15 and (styled or block.block_type in ("title", "section_header")))
                if (block.block_type in ("footnote", "caption", "formula", "table_cell", "list", "list_item")
                        or _SECTION.search(block.text) or _LIST.search(block.text) or _CAPTION.search(block.text)
                        or block.block_type in ("title", "section_header") and not heading):
                    block.role_reasons = ["CONTENT_ROLE_PROTECTED"]
                    continue
                if edge and is_page_number_text(block.text):
                    bare = block.text.strip()
                    if not re.fullmatch(r"\d{4}", bare):
                        occurrences.append(Occurrence(page, block, physical, logical, edge, False, _key(block.text)))
                        block.role_reasons.append("PAGE_NUMBER_SEQUENCE_CANDIDATE")
                    continue
                role = edge
                if not role or len(block.text.strip()) < 4 or len(block.text) > 160 or len(block.text.splitlines()) > 2:
                    continue
                # A model proposal never bypasses these candidate checks.
                occurrences.append(Occurrence(page, block, physical, logical, role, heading, _key(block.text)))
                block.role_reasons.append("FURNITURE_CANDIDATE")
        association = PageAssociation(doc, min_pages=self.min_pages, window=self.window, support_ratio=self.support_ratio,
            position_tolerance=self.position_tolerance, token_similarity_threshold=self.token_similarity_threshold,
            learning_pages=self.learning_pages).resolve(occurrences)
        parameters = {"min_pages": self.min_pages, "window": self.window, "support_ratio": self.support_ratio,
                      "position_tolerance": self.position_tolerance, "edge_ratio": self.edge_ratio,
                      "token_similarity_threshold": self.token_similarity_threshold, "learning_pages": self.learning_pages}
        payload = json.dumps({"evidence": context, "method": METHOD, "algorithm_version": association["algorithm_version"],
                              "parameters": parameters}, ensure_ascii=False, sort_keys=True)
        report = {"method": METHOD, **association,
                  "context_scope": "full_document" if {p.page_num for p in doc.pages} == set(range(1, doc.total_pages + 1)) else "selected_pages",
                  "context_page_ids": [str(p.metadata.get("logical_page_id", f"p{p.page_num:04d}")) for p in doc.pages],
                  "context_hash": hashlib.sha256(payload.encode()).hexdigest(),
                  "quality_acceptance": "NOT_EVALUATED",
                  "research_basis": ["https://aclanthology.org/2020.finnlp-1.11/"],
                  "implementation": "conservative_adaptation_not_paper_reproduction",
                  "parameters": parameters}
        doc.manifest["furniture"] = report
        return report

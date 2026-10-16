"""FinSBD-2 sections 5.2--5.3, adapted to OCR line boxes.

The paper describes similar tokens, page-size families, odd/even templates,
20-page learning and pagination offsets. It does not specify our numerical
thresholds. Local adaptation, semantic guards and conservative fuzzy matching
are project extensions, not a reproduction of published benchmark results.
"""
from __future__ import annotations

import hashlib
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher

ALGORITHM_VERSION = "finsbd-page-association-v1"
_TOKEN = re.compile(r"#page|\w+", re.UNICODE)
_LABELLED = re.compile(r"\b(?:trang|page)\s+(\d{1,4}|[ivxlcdm]+)(?:\s*/\s*(\d{1,4}))?\b", re.I)
_DECIMAL = re.compile(r"\b\d{1,4}\b")
_ROMAN = re.compile(r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})", re.I)


@dataclass
class Occurrence:
    page: object
    block: object
    physical: str
    logical: str
    role: str
    heading: bool
    key: str

    @property
    def geometry(self):
        box = self.block.bbox
        return (box[0]/self.page.width, (box[1]+box[3])/(2*self.page.height),
                box[2]/self.page.width, (box[3]-box[1])/self.page.height)


def _one_edit(a, b):
    if abs(len(a)-len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    if len(a) > len(b):
        a, b = b, a
    i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), len(a))
    return a[i:] == b[i+1:]


def token_similarity(a, b):
    """Allow one OCR edit in one long token; never blur years/codes/short words."""
    left, right = _TOKEN.findall(a), _TOKEN.findall(b)
    if not left or len(left) != len(right):
        return 0.
    weight, score, changed = 0, 0., 0
    for x, y in zip(left, right):
        w = max(len(x), len(y))
        weight += w
        if x == y:
            score += w
            continue
        changed += 1
        if (changed > 1 or min(len(x), len(y)) < 5 or any(c.isdigit() for c in x+y)
                or not _one_edit(x, y)):
            return 0.
        score += w * SequenceMatcher(None, x, y, autojunk=False).ratio()
    return score/max(1, weight)


def _same_family(a, b):
    return (all(abs(x-y) <= .02*max(x, y) for x, y in
                ((a.width, b.width), (a.height, b.height)))
            and (a.width > a.height) == (b.width > b.height))


def _compatible(a, b, tolerance):
    if a.role != b.role or not _same_family(a.page, b.page):
        return False
    ga, gb = a.geometry, b.geometry
    if any(abs(ga[i]-gb[i]) > tolerance for i in (0, 1, 2)) or abs(ga[3]-gb[3]) > .01:
        return False
    x, y = a.block.font_size, b.block.font_size
    return (bool(a.block.is_bold) == bool(b.block.is_bold)
            and (x is None and y is None or x is not None and y is not None and abs(x-y) <= max(.5, .05*x)))


def _pagination_value(text):
    if text.isdecimal():
        return int(text), "decimal"
    if not text or not _ROMAN.fullmatch(text):
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    numbers = [values[c] for c in text.upper()]
    return sum(-n if i+1 < len(numbers) and n < numbers[i+1] else n for i, n in enumerate(numbers)), "roman"


@dataclass(frozen=True)
class NumberSlot:
    start: int
    end: int
    value: int
    kind: str
    total: str | None = None
    bare: bool = False


def _slots(key):
    labelled = list(_LABELLED.finditer(key))
    if labelled:
        for match in labelled:
            parsed = _pagination_value(match[1])
            if parsed and parsed[0] > 0:
                yield NumberSlot(*match.span(1), *parsed, match[2], bool(_LABELLED.fullmatch(key)))
        return
    fraction = re.fullmatch(r"(\d{1,4}|[ivxlcdm]+)\s*/\s*(\d{1,4})", key, re.I)
    if fraction and _pagination_value(fraction[1]):
        value, kind = _pagination_value(fraction[1])
        if value > 0:
            yield NumberSlot(*fraction.span(1), value, kind, fraction[2], True)
        return
    # A bare four-digit number is ambiguous with a business year.
    if _pagination_value(key) and not re.fullmatch(r"\d{4}", key):
        value, kind = _pagination_value(key)
        if value > 0:
            yield NumberSlot(0, len(key), value, kind, bare=True)
        return
    # A variable decimal at either end of otherwise stable running text.
    for match in _DECIMAL.finditer(key):
        if len(match[0]) < 4 and (not key[:match.start()].strip() or not key[match.end():].strip()):
            if int(match[0]) > 0:
                yield NumberSlot(*match.span(), int(match[0]), "decimal")


def _variable_key(occurrence, slot):
    return occurrence.key[:slot.start] + "#page" + occurrence.key[slot.end:]


def _standalone(occurrence, slot):
    return slot.bare


class PageAssociation:
    def __init__(self, doc, *, min_pages, window, support_ratio, position_tolerance,
                 token_similarity_threshold, learning_pages):
        self.doc, self.min_pages, self.window = doc, min_pages, window
        self.ratio, self.tolerance = support_ratio, position_tolerance
        self.similarity, self.learning_pages = token_similarity_threshold, learning_pages
        self.templates, self.sequences, self.page_regions = {}, {}, defaultdict(list)
        self.seed_pages = {p.page_num: {q.page_num for q in sorted(
            (q for q in doc.pages if _same_family(p, q)), key=lambda q: q.page_num)[:learning_pages]}
            for p in doc.pages}

    def _support(self, focus, peers):
        # Learn a model from the first 20 pages of each size/orientation family.
        seed = self.seed_pages[focus.page.page_num]
        for scope, pool in (("initial_pages", [o for o in peers if o.page.page_num in seed]),
                            ("local_adaptation", [o for o in peers if abs(o.page.page_num-focus.page.page_num) <= self.window])):
            # A pattern observed exclusively on one parity must not be
            # advertised as a template for both odd and even pages.
            parities = (focus.page.page_num % 2,) if len({o.page.page_num % 2 for o in pool}) == 1 else (None, focus.page.page_num % 2)
            for parity in parities:
                members = [o for o in pool if parity is None or o.page.page_num % 2 == parity]
                if not members:
                    continue
                low, high = min(o.page.page_num for o in members), max(o.page.page_num for o in members)
                # Missing/unprocessed pages cannot inflate the support ratio.
                eligible = high-low+1 if parity is None else (high-low)//2+1
                if len({o.physical for o in members}) >= self.min_pages and len({o.physical for o in members})/eligible >= self.ratio:
                    return members, scope, parity
        return [], None, None

    def _region(self, occurrence, template_id):
        b, p = occurrence.block, occurrence.page
        self.page_regions[p.page_num].append({"role": occurrence.role, "template_id": template_id,
            "bbox": list(b.bbox), "source_id": b.source_id,
            "block_index": next(i for i, block in enumerate(p.blocks, 1) if block is b)})

    def resolve(self, occurrences):
        slots = [(o, slot) for o in occurrences for slot in _slots(o.key)]
        normalized = {id(o.block): o.key for o in occurrences}
        bare = {id(o.block) for o, s in slots if _standalone(o, s)}
        # Pagination must form a sequence with a constant printed/PDF offset.
        for focus, slot in slots:
            variable = _variable_key(focus, slot)
            offset = slot.value-focus.page.page_num
            peers = [(o, s) for o, s in slots if s.kind == slot.kind and s.total == slot.total
                     and s.value-o.page.page_num == offset and _compatible(focus, o, self.tolerance)
                     and token_similarity(variable, _variable_key(o, s)) >= self.similarity]
            support, scope, parity = self._support(focus, [o for o, s in peers])
            if not support:
                continue
            geometry = tuple(round(statistics.median(o.geometry[i] for o in support), 2) for i in (0, 1, 2))
            sid = hashlib.sha256(f"number:{slot.kind}:{offset}:{focus.role}:{variable}:{geometry}:{parity}".encode()).hexdigest()[:16]
            self.sequences[sid] = {"sequence_id": sid, "kind": slot.kind, "printed_minus_pdf": offset,
                "support_page_ids": sorted({o.logical for o in support}), "learning_scope": scope,
                "parity": parity, "template_text": variable}
            normalized[id(focus.block)] = variable
            if _standalone(focus, slot):
                b = focus.block
                b.block_type = b.role_final = "page_number"
                b.role_status, b.visibility = "ACCEPTED", "metadata"
                b.role_reasons = ["SEQUENTIAL_PAGE_NUMBER", "CONSTANT_PRINTED_PDF_OFFSET", "STABLE_GEOMETRY"]
                b.support_page_ids = sorted({o.logical for o in support})
                b.template_id = sid
                b.review_reason_codes = [c for c in b.review_reason_codes if c not in ("HEADER_BODY_CONFLICT", "PAGE_NUMBER_BODY_CONFLICT")]
                self._region(focus, sid)
        grouped = defaultdict(list)
        for occurrence in occurrences:
            if id(occurrence.block) not in bare:
                grouped[(occurrence.role, occurrence.heading)].append(occurrence)
        for focus in occurrences:
            if id(focus.block) in bare:
                if focus.block.role_status != "ACCEPTED":
                    focus.block.role_status = "NEEDS_REVIEW"
                    focus.block.role_reasons = ["PAGE_NUMBER_SEQUENCE_UNCONFIRMED"]
                    focus.block.review_reason_codes = sorted(set(focus.block.review_reason_codes + ["PAGE_NUMBER_BODY_CONFLICT"]))
                continue
            key = normalized[id(focus.block)]
            peers = [o for o in grouped[(focus.role, focus.heading)] if _compatible(focus, o, self.tolerance)
                     and token_similarity(key, normalized[id(o.block)]) >= self.similarity]
            support, scope, parity = self._support(focus, peers)
            b = focus.block
            if not support:
                b.role_reasons.append("INSUFFICIENT_TEMPLATE_SUPPORT")
                continue
            support_ids = sorted({o.logical for o in support})
            # Preserve the first actual section heading of each contiguous run.
            if focus.heading:
                segments = []
                for number in sorted({o.page.page_num for o in peers}):
                    if not segments or number-segments[-1][-1] > 4:
                        segments.append([number])
                    else:
                        segments[-1].append(number)
                anchor = next(segment[0] for segment in segments if focus.page.page_num in segment)
                if focus.page.page_num == anchor:
                    b.role_status, b.role_reasons = "KEPT", ["REPEATED_SECTION_TITLE_ANCHOR"]
                    b.support_page_ids = support_ids
                    b.review_reason_codes = [c for c in b.review_reason_codes if c != "HEADER_BODY_CONFLICT"]
                    continue
            canonical = sorted(Counter(normalized[id(o.block)] for o in support).items(), key=lambda pair: (-pair[1], pair[0]))[0][0]
            geometry = tuple(round(statistics.median(o.geometry[i] for o in support), 2) for i in (0, 1, 2))
            tid = hashlib.sha256(f"{focus.role}:{canonical}:{geometry}:{parity}".encode()).hexdigest()[:16]
            b.block_type = b.role_final = f"running_{focus.role}"
            b.role_status, b.visibility = "ACCEPTED", "suppressed"
            b.suppression_reason = "DISTINCT_PAGE_TEMPLATE_REPEAT"
            b.template_id, b.support_page_ids = tid, support_ids
            b.review_reason_codes = [c for c in b.review_reason_codes if c not in ("HEADER_BODY_CONFLICT", "PAGE_NUMBER_BODY_CONFLICT")]
            b.role_reasons = ["SIMILAR_TOKENS" if any(normalized[id(o.block)] != key for o in support) else "EXACT_NORMALIZED_TEXT",
                "STABLE_GEOMETRY", "STYLE_UNAVAILABLE" if b.font_size is None else "STABLE_STYLE",
                "DISTINCT_PHYSICAL_PAGES", "LEARNED_MARGIN_BOUNDARY"]
            if tid not in self.templates:
                self.templates[tid] = {"template_id": tid, "role": focus.role, "text": canonical,
                    "learning_scope": scope, "parity": parity, "support_page_ids": support_ids}
            self._region(focus, tid)
        for page in self.doc.pages:
            regions = self.page_regions[page.page_num]
            page.metadata["furniture_regions"] = []
            for role in ("header", "footer"):
                matched = [r for r in regions if r["role"] == role]
                if matched:
                    boundary = max(r["bbox"][3] for r in matched) if role == "header" else min(r["bbox"][1] for r in matched)
                    page.metadata["furniture_regions"].append({"role": role, "boundary_y": boundary,
                        "boundary_y_normalized": boundary/page.height, "matched_blocks": matched,
                        "policy": "only_matched_blocks_are_furniture"})
        return {"algorithm_version": ALGORITHM_VERSION, "templates": list(self.templates.values()),
                "page_number_sequences": list(self.sequences.values()),
                "region_boundaries": [{"page": p.page_num, "regions": p.metadata["furniture_regions"]} for p in self.doc.pages]}

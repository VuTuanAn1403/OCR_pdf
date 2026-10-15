"""Conservative recursive bands/columns over atomic content items.

Geometry is evidence for a layout, not proof of article continuity. Ambiguous
article groups and unplaced items remain reviewable. Tables/images/formulas
enter as single nodes and are never split into prose columns.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from src.ocr.infrastructure.structure.source_identity import valid_bbox

METHOD = "region_tree_graph_v1"
_LABEL = re.compile(r"^(mã cổ phiếu|vốn điều lệ|địa chỉ|điện thoại|fax|email|website|"
                    r"ngày sinh|quốc tịch|giới tính|tên công ty)\b", re.I)


@dataclass(frozen=True)
class LayoutItem:
    id: str
    bbox: list[float]
    text: str = ""
    role: str = "paragraph"
    kind: str = "b"


@dataclass
class LayoutResult:
    ordered_ids: list[str]
    tree: dict
    assignments: dict = field(default_factory=dict)
    review: list[dict] = field(default_factory=list)
    layout_family: str = "single_column"
    article_groups: list[dict] = field(default_factory=list)


def _gaps(items, axis):
    intervals = sorted((b.bbox[axis], b.bbox[axis + 2]) for b in items)
    end = intervals[0][1]
    gaps = []
    for start, stop in intervals[1:]:
        if start > end:
            gaps.append((end, start))
        end = max(end, stop)
    return gaps


def _rows(items):
    rows = []
    for item in sorted(items, key=lambda b: (b.bbox[1], b.bbox[0], b.id)):
        center = (item.bbox[1] + item.bbox[3]) / 2
        height = item.bbox[3] - item.bbox[1]
        if rows and abs(center - rows[-1][0]) <= .5 * min(height, rows[-1][1]):
            rows[-1][2].append(item)
        else:
            rows.append((center, height, [item]))
    return [sorted(row, key=lambda b: (b.bbox[0], b.id)) for _, _, row in rows]


def _row_order(items):
    return [b for row in _rows(items) for b in row]


_NUMBER_CELL = re.compile(r"(?:[+−–-]?\s*\(?\d[\d\s.,]*\)?\s*%?|[−–-]|n/a)", re.I)


def _is_borderless_table(items):
    """Aligned labels plus repeated numeric columns, independent of vocabulary.

    Require at least three complete rows. Alignment uses right edges because
    financial amounts can have different widths. Prose columns fail the
    numeric-cell test even when their baselines happen to align.
    """
    text = [b for b in items if b.kind == "b"]
    rows = _rows(text)
    candidates = [row for row in rows if len(row) >= 2
                  and not _NUMBER_CELL.fullmatch(row[0].text.strip())
                  and any(c.isalpha() for c in row[0].text)
                  and all(_NUMBER_CELL.fullmatch(b.text.strip()) for b in row[1:])]
    if len(candidates) < 3 or sum(map(len, candidates)) < .7 * len(text):
        return False
    for seed in candidates:
        tolerance = max(3., .6 * statistics.median(b.bbox[3] - b.bbox[1] for b in seed))
        aligned = [row for row in candidates if len(row) == len(seed)
                   and abs(row[0].bbox[0] - seed[0].bbox[0]) <= tolerance
                   and all(abs(a.bbox[2] - b.bbox[2]) <= tolerance for a, b in zip(row[1:], seed[1:]))]
        if len(aligned) >= 3 and len(aligned) >= .75 * len(candidates):
            return True
    return False


def _is_form(items):
    text = [b for b in items if b.kind == "b"]
    if len(text) < 4:
        return False
    labels = [b for b in text if b.text.rstrip().endswith(":") or _LABEL.search(b.text.strip())]
    toc = any(b.role in ("document_index", "toc") for b in text)
    if not toc and len(labels) < 2:
        return False
    paired = 0
    for left in (text if toc else labels):
        h = left.bbox[3] - left.bbox[1]
        if any(right.bbox[0] >= left.bbox[2]
               and abs(sum(right.bbox[1::2]) - sum(left.bbox[1::2])) <= h
               and (not toc or re.fullmatch(r"\d{1,4}", right.text.strip()))
               for right in text if right.id != left.id):
            paired += 1
    return paired >= 2 and paired * 2 >= len(text) * .6


def _is_background_item(item: LayoutItem, all_items: list[LayoutItem]) -> bool:
    if item.kind != "i" or not valid_bbox(item.bbox):
        return False
    content_items = [b for b in all_items if b.id != item.id and valid_bbox(b.bbox) and b.kind != "i"]
    if not content_items:
        return False
    # Case 1: Image acts as underlay plate containing multiple text/table content blocks
    ibox = item.bbox
    contained = 0
    for b in content_items:
        bbox = b.bbox
        iw = max(0.0, min(ibox[2], bbox[2]) - max(ibox[0], bbox[0]))
        ih = max(0.0, min(ibox[3], bbox[3]) - max(ibox[1], bbox[1]))
        if iw * ih >= 0.7 * (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]):
            contained += 1
    if contained >= 4:
        return True
    # Case 2: Full-page / broad canvas background covering almost all content items
    min_x = min(b.bbox[0] for b in content_items)
    min_y = min(b.bbox[1] for b in content_items)
    max_x = max(b.bbox[2] for b in content_items)
    max_y = max(b.bbox[3] for b in content_items)
    span_w = max_x - min_x
    span_h = max_y - min_y
    if span_w <= 0 or span_h <= 0:
        return False
    item_w = item.bbox[2] - item.bbox[0]
    item_h = item.bbox[3] - item.bbox[1]
    return (item_w >= 0.85 * span_w and item_h >= 0.85 * span_h and
            item.bbox[0] <= min_x + 0.15 * span_w and item.bbox[2] >= max_x - 0.15 * span_w and
            item.bbox[1] <= min_y + 0.15 * span_h and item.bbox[3] >= max_y - 0.15 * span_h)


def infer_region_tree(items: list[LayoutItem], *, min_gap: float = 3.0,
                      max_depth: int = 48, max_nodes: int = 10000) -> LayoutResult:
    if len({b.id for b in items}) != len(items):
        raise ValueError("Duplicate item IDs cannot be ordered")
    placed = [b for b in items if valid_bbox(b.bbox)]
    unplaced = sorted((b for b in items if not valid_bbox(b.bbox)), key=lambda b: b.id)
    backgrounds = [b for b in placed if _is_background_item(b, placed)]
    content_placed = [b for b in placed if b not in backgrounds]
    result = LayoutResult([], {})
    node_count, max_columns = 0, 1

    def walk(group, path, depth, column=None, article="article-1"):
        nonlocal node_count, max_columns
        node_count += 1
        node = {"id": path, "type": "flow", "children": []}
        if not group:
            return node
        height = statistics.median(b.bbox[3] - b.bbox[1] for b in group)
        if depth >= max_depth or node_count + len(group) > max_nodes:
            result.review.append({"code": "LAYOUT_COMPLEXITY_LIMIT", "item_ids": [b.id for b in group]})
            ordered, node["type"] = _row_order(group), "review"
        elif _is_borderless_table(group):
            ordered, node["type"] = _row_order(group), "borderless_table"
        elif _is_form(group):
            ordered, node["type"] = _row_order(group), "form_or_toc"
        else:
            xg = [g for g in _gaps(group, 0) if g[1] - g[0] >= max(min_gap, .35 * height)]
            yg = [g for g in _gaps(group, 1) if g[1] - g[0] >= min_gap]
            middle_y = statistics.median((b.bbox[1] + b.bbox[3]) / 2 for b in group)
            # Equal line gaps split near the median, avoiding a linear-depth
            # tree (and false complexity warnings) on ordinary single columns.
            widest_y = max(yg, key=lambda g: (round(g[1] - g[0], 6), -abs(sum(g) / 2 - middle_y))) if yg else None
            # Persistent gutters take precedence over aligned line/paragraph gaps.
            # A broad blank band can delimit independently typeset sections.
            band = (widest_y is not None and (not xg or
                    widest_y[1] - widest_y[0] > max(3 * height, 2 * max(g[1] - g[0] for g in xg))))
            if xg and not band:
                cuts = [(a + b) / 2 for a, b in xg]
                groups = [[] for _ in range(len(cuts) + 1)]
                for b in group:
                    groups[sum(b.bbox[0] >= cut for cut in cuts)].append(b)
                max_columns = max(max_columns, len(groups))
                node["type"] = "column_group"
                independent = all(any(b.role in ("title", "section_header") for b in g) for g in groups)
                for i, child in enumerate(groups):
                    child_article = f"{path}-article-{i + 1}" if independent else article
                    node["children"].append(walk(child, f"{path}.{i}", depth + 1,
                                                 f"{path}-column-{i + 1}", child_article))
                headed_columns = sum(any(b.role in ("title", "section_header") for b in g) for g in groups)
                if 1 < headed_columns < len(groups):
                    result.review.append({"code": "AMBIGUOUS_ARTICLE_GROUP", "region_id": path,
                                          "item_ids": [b.id for b in group]})
                return node
            if band:
                cut = sum(widest_y) / 2
                above = [b for b in group if b.bbox[3] <= cut]
                below = [b for b in group if b.bbox[1] >= cut]
                if above and below:
                    node["type"] = "horizontal_band"
                    node["children"] = [walk(above, f"{path}.0", depth + 1, column, article),
                                        walk(below, f"{path}.1", depth + 1, column, article)]
                    return node
            ordered = _row_order(group)
        node["item_ids"] = [b.id for b in ordered]
        for b in ordered:
            result.ordered_ids.append(b.id)
            result.assignments[b.id] = {"parent_region_id": path, "column_id": column,
                                       "article_group_id": article}
        return node

    if content_placed:
        result.tree = walk(content_placed, "r", 0)
    elif placed:
        result.tree = walk(placed, "r", 0)
    else:
        result.tree = {"id": "r", "type": "flow", "children": []}
    for b in reversed(backgrounds):
        result.ordered_ids.insert(0, b.id)
        result.assignments[b.id] = {"parent_region_id": "r", "column_id": None,
                                    "article_group_id": None}
    if backgrounds:
        result.tree.setdefault("background_item_ids", []).extend(b.id for b in backgrounds)
    for item in unplaced:
        result.ordered_ids.append(item.id)
        result.assignments[item.id] = {"parent_region_id": "unplaced", "column_id": None,
                                       "article_group_id": None}
        result.review.append({"code": "UNPLACED", "item_ids": [item.id]})
    if unplaced:
        result.tree.setdefault("unplaced_item_ids", []).extend(b.id for b in unplaced)
    result.layout_family = "newspaper_candidate" if max_columns >= 3 else "multi_column" if max_columns > 1 else "single_column"
    articles = sorted({a["article_group_id"] for a in result.assignments.values() if a["article_group_id"]})
    result.article_groups = [{"id": a, "status": "CANDIDATE", "item_ids": [i for i in result.ordered_ids
                              if result.assignments[i]["article_group_id"] == a]} for a in articles]
    return result

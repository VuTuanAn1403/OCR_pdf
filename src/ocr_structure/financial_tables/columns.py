"""Column envelopes from leaf headers and right-aligned numeric clusters."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple
from ocr_contracts.text_block import TextBlock
from .patterns import FINANCIAL_NUM_RE


@dataclass
class ColumnBoundary:
    col_idx: int
    x0: float
    x1: float
    right_anchor: Optional[float] = None
    is_numeric: bool = False
    title: str = ""


def discover_columns(
    all_blocks: List[TextBlock],
    numeric_blocks: List[TextBlock],
    header_lines: Optional[List[List[TextBlock]]] = None,
    *, col_cluster_tolerance: float,
) -> List[ColumnBoundary]:
    """
    Discovers columns by combining leaf-headers and right-aligned numeric clusters.
    """
    min_x0 = min(b.bbox[0] for b in all_blocks)
    max_x1 = max(b.bbox[2] for b in all_blocks)

    leaf_hdr_blocks: List[TextBlock] = []
    if header_lines:
        best_hdr = max(header_lines, key=lambda hl: len(hl))
        if len(best_hdr) >= 3:
            leaf_hdr_blocks = best_hdr

    # Method 1: Leaf-header anchors (handles mixed text, percentage, and amount columns)
    if leaf_hdr_blocks and len(leaf_hdr_blocks) >= 3:
        first_h_x0 = leaf_hdr_blocks[0].bbox[0]
        col_list_blocks = []
        if first_h_x0 > min_x0 + 80.0:
            col_list_blocks.append((min_x0, first_h_x0 - 5.0, None))
        for hb in leaf_hdr_blocks:
            col_list_blocks.append((hb.bbox[0], hb.bbox[2], hb))

        # Headers can be much narrower than their right-aligned values.
        # Fit boundaries between whole observed column envelopes, not through numbers.
        header_ids = {id(b) for line in header_lines for b in line}
        centers = [(x0 + x1) / 2 for x0, x1, _ in col_list_blocks]
        observations = [[] for _ in col_list_blocks]
        for block in all_blocks:
            if id(block) in header_ids:
                continue
            midpoint = (block.bbox[0] + block.bbox[2]) / 2
            distances = [abs(midpoint - center) for center in centers]
            nearest = min(distances)
            owners = [index for index, distance in enumerate(distances) if distance == nearest]
            if len(owners) != 1:
                return []
            observations[owners[0]].append(block)
        envelopes = []
        for index, (x0, x1, header) in enumerate(col_list_blocks):
            observed = observations[index]
            left = [b.bbox[0] for b in observed] + ([x0] if header is not None else [])
            right = [b.bbox[2] for b in observed] + ([x1] if header is not None else [])
            envelopes.append((min(left, default=x0), max(right, default=x1)))
        if any(left[1] >= right[0] for left, right in zip(envelopes, envelopes[1:])):
            return []
        columns: List[ColumnBoundary] = []
        for c_idx, (cx0, cx1, hb) in enumerate(col_list_blocks):
            if c_idx + 1 < len(col_list_blocks):
                right_bound = (envelopes[c_idx][1] + envelopes[c_idx + 1][0]) / 2.0
            else:
                right_bound = max_x1 + 15.0
            left_bound = columns[-1].x1 if columns else min_x0 - 5.0
            columns.append(ColumnBoundary(col_idx=c_idx, x0=left_bound, x1=right_bound))
        return columns

    # Method 2: Numeric right-edge clustering (pure numeric statement tables)
    strong_numeric = [
        b for b in numeric_blocks
        if re.search(r"\d{2,}|%", b.text.strip())
    ]
    target_blocks = strong_numeric if len(strong_numeric) >= 2 else numeric_blocks

    x1_values = sorted(b.bbox[2] for b in target_blocks)
    clusters: List[List[float]] = []

    for x1 in x1_values:
        matched = False
        for cl in clusters:
            avg_x1 = sum(cl) / len(cl)
            if abs(x1 - avg_x1) <= col_cluster_tolerance:
                cl.append(x1)
                matched = True
                break
        if not matched:
            clusters.append([x1])

    min_cluster_size = 1 if len(target_blocks) <= 4 else 2
    valid_clusters = [cl for cl in clusters if len(cl) >= min_cluster_size]
    valid_clusters.sort(key=lambda cl: sum(cl) / len(cl))

    if not valid_clusters:
        valid_clusters = sorted(clusters, key=lambda cl: sum(cl) / len(cl))

    cluster_ranges: List[Tuple[float, float, float]] = []
    for cl in valid_clusters:
        cl_blocks = [b for b in target_blocks if any(abs(b.bbox[2] - x) <= col_cluster_tolerance for x in cl)]
        if cl_blocks:
            c_min_x0 = min(b.bbox[0] for b in cl_blocks)
            c_max_x1 = max(b.bbox[2] for b in cl_blocks)
            c_anchor = sum(cl) / len(cl)
            cluster_ranges.append((c_min_x0, c_max_x1, c_anchor))

    cluster_ranges.sort(key=lambda r: r[2])

    merged_ranges: List[Tuple[float, float, float]] = []
    for r_x0, r_x1, r_anc in cluster_ranges:
        if not merged_ranges:
            merged_ranges.append((r_x0, r_x1, r_anc))
        else:
            prev_x0, prev_x1, prev_anc = merged_ranges[-1]
            if abs(r_anc - prev_anc) <= 12.0:
                merged_ranges[-1] = (min(prev_x0, r_x0), max(prev_x1, r_x1), (prev_anc + r_anc) / 2.0)
            else:
                merged_ranges.append((r_x0, r_x1, r_anc))

    cluster_ranges = merged_ranges
    first_num_x0 = cluster_ranges[0][0] if cluster_ranges else min_x0 + 100.0

    left_blocks = [b for b in all_blocks if b.bbox[2] <= first_num_x0]
    desc_max_x1 = max((b.bbox[2] for b in left_blocks), default=first_num_x0 - 20.0)

    columns = []
    text_before_nums = [
        b for b in all_blocks
        if b.bbox[0] >= min_x0 + 80.0 and b.bbox[2] <= first_num_x0 - 5.0
        and not FINANCIAL_NUM_RE.match(b.text.strip())
    ]
    has_middle_text_col = False
    mid_text_x0 = 0.0
    mid_text_x1 = 0.0

    if text_before_nums:
        mid_x0s = [b.bbox[0] for b in text_before_nums]
        if len(mid_x0s) >= 2:
            mid_text_x0 = min(mid_x0s) - 5.0
            mid_text_x1 = max(b.bbox[2] for b in text_before_nums) + 5.0
            has_middle_text_col = True

    if has_middle_text_col:
        columns.append(ColumnBoundary(col_idx=0, x0=min_x0 - 5.0, x1=mid_text_x0))
        columns.append(ColumnBoundary(col_idx=1, x0=mid_text_x0, x1=mid_text_x1))
    else:
        split_x = (desc_max_x1 + first_num_x0) / 2.0
        columns.append(ColumnBoundary(col_idx=0, x0=min_x0 - 5.0, x1=split_x))

    for idx, (c_x0, c_x1, c_anc) in enumerate(cluster_ranges):
        col_left = columns[-1].x1
        if idx + 1 < len(cluster_ranges):
            next_x0 = cluster_ranges[idx + 1][0]
            col_right = (c_x1 + next_x0) / 2.0
        else:
            col_right = c_x1 + 15.0

        columns.append(
            ColumnBoundary(
                col_idx=len(columns),
                x0=col_left,
                x1=col_right,
                right_anchor=c_anc,
                is_numeric=True,
            )
        )

    return columns

"""V6.2 geometry/identity alignment; does not change historical protocols.

All GT nodes, including short numbers and repeated words, enter one-to-one
alignment. Explicit GT pairs support partial orders without inventing a total
order between independent articles. Thresholds must be locked on dev.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from itertools import combinations
import unicodedata

import numpy as np
from scipy.optimize import linear_sum_assignment

PROTOCOL = "reading_order_v6_2"


def _text(node):
    return " ".join(unicodedata.normalize("NFC", node.get("text", node.get("text_raw", ""))).split())


def _iou(a, b):
    if len(a) != 4 or len(b) != 4:
        return 0.
    intersection = max(0., min(a[2], b[2]) - max(a[0], b[0])) * max(0., min(a[3], b[3]) - max(a[1], b[1]))
    union = max(0., a[2] - a[0]) * max(0., a[3] - a[1]) + max(0., b[2] - b[0]) * max(0., b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.


def evaluate_reading_order(reference, prediction, *, before_pairs=None, iou_threshold=.5,
                           text_threshold=.8, same_evidence=False):
    """Nodes use unique GT `id`, text/bbox, and optional canonical source_id.

    `same_evidence=True` is for pure ordering on frozen source evidence only;
    end-to-end evaluation requires geometry and text similarity. Split/merged
    blocks need externally reviewed token alignment and are not auto-matched.
    """
    if not 0 < iou_threshold <= 1 or not 0 < text_threshold <= 1:
        raise ValueError("Matching thresholds must be in (0, 1]")
    ids = [node["id"] for node in reference]
    if len(set(ids)) != len(ids):
        raise ValueError("GT node IDs must be unique")
    id_to_rank = {item_id: i for i, item_id in enumerate(ids)}
    total_order = before_pairs is None
    pairs = list(combinations(ids, 2)) if total_order else list(before_pairs)
    if any(a not in id_to_rank or b not in id_to_rank or a == b for a, b in pairs):
        raise ValueError("GT precedence references unknown nodes or itself")
    if len(set(pairs)) != len(pairs):
        raise ValueError("GT precedence pairs must be unique")
    n, m = len(reference), len(prediction)
    weights = np.zeros((n, m))
    for i, ref in enumerate(reference):
        for j, pred in enumerate(prediction):
            if same_evidence:
                if ref.get("source_id") and ref["source_id"] == pred.get("source_id"):
                    weights[i, j] = 1.
            else:
                overlap = _iou(ref.get("bbox", []), pred.get("bbox", []))
                similarity = SequenceMatcher(None, _text(ref), _text(pred)).ratio()
                if overlap >= iou_threshold and similarity >= text_threshold:
                    weights[i, j] = (overlap + similarity) / 2
    # Cardinality dominates similarity: never prefer one perfect match over
    # two valid matches. Zero weights stay ineligible after assignment.
    mapping = {}
    if n and m:
        objective = np.where(weights > 0, min(n, m) + 1 + weights, 0)
        rows, columns = linear_sum_assignment(objective, maximize=True)
        mapping = {int(i): int(j) for i, j in zip(rows, columns) if weights[i, j] > 0}
    comparable = [(id_to_rank[a], id_to_rank[b]) for a, b in pairs
                  if id_to_rank[a] in mapping and id_to_rank[b] in mapping]
    correct = sum(mapping[a] < mapping[b] for a, b in comparable)
    precedence = correct / len(comparable) if comparable else None
    coverage = len(mapping) / n if n else None
    used = set(mapping.values())
    duplicates = sum(j not in used and bool((weights[:, j] > 0).any()) for j in range(m))
    adjacent = [(i, i + 1) for i in range(n - 1) if i in mapping and i + 1 in mapping] if total_order else []
    possible_pairs = n * (n - 1) / 2
    return {"protocol": PROTOCOL, "status": "NOT_EVALUATED_EMPTY_GT" if not n else "MEASURED" if comparable else "INSUFFICIENT_MATCHED_PAIRS",
            "reference_nodes": n, "predicted_nodes": m, "matched_nodes": len(mapping),
            "coverage": coverage, "precision": len(mapping) / m if m else 0.,
            "duplicate_predictions": duplicates, "missing_nodes": n - len(mapping),
            "gt_pairs": len(pairs), "matched_pairs": len(comparable), "correct_pairs": correct,
            "annotated_pair_fraction": len(pairs) / possible_pairs if possible_pairs else None,
            "precedence_accuracy": precedence,
            "penalized_order_score": precedence * coverage if precedence is not None and coverage is not None else 0. if n and not mapping else None,
            "adjacency_accuracy": sum(mapping[b] == mapping[a] + 1 for a, b in adjacent) / len(adjacent) if adjacent else None,
            "exact_order_page": (len(mapping) == n == m and all(mapping[i] == i for i in range(n))) if total_order and n else None,
            "alignment": [{"gt_id": ids[i], "prediction_index": j, "similarity": float(weights[i, j])}
                          for i, j in sorted(mapping.items())],
            "matching": {"iou_threshold": iou_threshold, "text_threshold": text_threshold, "same_evidence": same_evidence}}

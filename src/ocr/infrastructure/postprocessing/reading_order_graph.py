"""Deterministic precedence solver with explicit cycle repair evidence."""
from __future__ import annotations

import heapq
from dataclasses import dataclass


@dataclass(frozen=True)
class Edge:
    before: str
    after: str
    hard: bool = True
    score: float = 1.0
    relation: str = "before"


def solve_order(nodes: list[str], edges: list[Edge]) -> tuple[list[str], list[dict]]:
    if len(set(nodes)) != len(nodes):
        raise ValueError("Duplicate graph nodes")
    rank = {node: i for i, node in enumerate(nodes)}
    active = [e for e in edges if e.relation == "before"]
    if any(e.before not in rank or e.after not in rank for e in active):
        raise ValueError("Order edge references an unknown node")
    review = []
    while True:
        successors, indegree = {n: [] for n in nodes}, dict.fromkeys(nodes, 0)
        for edge in active:
            successors[edge.before].append(edge.after)
            indegree[edge.after] += 1
        queue = [rank[n] for n in nodes if indegree[n] == 0]
        heapq.heapify(queue)
        ordered = []
        while queue:
            node = nodes[heapq.heappop(queue)]
            ordered.append(node)
            for successor in successors[node]:
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    heapq.heappush(queue, rank[successor])
        if len(ordered) == len(nodes):
            return ordered, review
        remaining = {n for n in nodes if indegree[n] > 0}

        def reaches(start, target):
            pending, seen = [start], set()
            while pending:
                current = pending.pop()
                if current == target:
                    return True
                if current not in seen:
                    seen.add(current)
                    pending.extend(successors[current])
            return False

        # Only remove an edge actually on a cycle, never an unrelated weak edge
        # leading out of a strongly connected component.
        soft = [e for e in active if not e.hard and e.before in remaining
                and e.after in remaining and reaches(e.after, e.before)]
        if not soft:
            review.append({"code": "ORDER_CYCLE", "hard_conflict": True, "item_ids": sorted(remaining)})
            return ordered + [n for n in nodes if n not in ordered], review
        drop = min(soft, key=lambda e: (e.score, rank[e.before], rank[e.after]))
        active.remove(drop)
        review.append({"code": "ORDER_CYCLE", "hard_conflict": False,
                       "removed_edge": [drop.before, drop.after], "score": drop.score})

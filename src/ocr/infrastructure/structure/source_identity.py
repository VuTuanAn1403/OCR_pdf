"""Canonical evidence identity, deliberately independent of reading order."""
from __future__ import annotations

import hashlib
import math
from collections import Counter

METHOD = "source-id-canonical-v1"


def valid_bbox(box) -> bool:
    return (isinstance(box, (list, tuple)) and len(box) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in box)
            and box[2] > box[0] and box[3] > box[1])


def assign_source_ids(records: list[dict], pdf_hash: str, logical_page_id: str,
                      generation: int = 1) -> dict:
    """Return identity by consumer reference; never use that reference as a tie break.

    A producer_ref must identify immutable native/detector evidence, rather than
    a batch index. Indistinguishable occurrences retain multiplicity and get an
    explicit ambiguity record; their individual identity is not certified.
    """
    prepared = []
    for record in records:
        raw_hash = hashlib.sha256(record.get("text_raw", "").encode("utf-8")).hexdigest()
        box = record.get("bbox", [])
        placed = valid_bbox(box)
        primary = ((0, round(box[1], 1), round(box[0], 1), round(box[3], 1), round(box[2], 1))
                   if placed else (1, 0, 0, 0, 0)) + (record["kind"], raw_hash[:8])
        tie = (raw_hash, tuple(box) if placed else (), str(record.get("producer_ref", "")))
        prepared.append((primary, tie, record, raw_hash, placed))
    prepared.sort(key=lambda row: (row[0], row[1]))
    primary_counts = Counter(row[0] for row in prepared)
    exact_counts = Counter((row[0], row[1]) for row in prepared)
    identities, collisions = {}, []
    for rank, (key, tie, record, raw_hash, placed) in enumerate(prepared, 1):
        reasons = []
        if not placed:
            reasons.append("UNPLACED")
        if exact_counts[(key, tie)] > 1:
            reasons.append("SOURCE_ID_AMBIGUITY")
        identity = {
            "source_id": f"{pdf_hash[:16]}:{logical_page_id}:{record['kind']}{rank:06d}",
            "source_id_method": METHOD, "raw_text_sha8": raw_hash[:8],
            "raw_text_sha256": raw_hash, "evidence_generation": generation,
            "review_reason_codes": reasons,
        }
        identities[record["ref"]] = identity
        if primary_counts[key] > 1:
            collisions.append({"source_id": identity["source_id"], "resolved": not bool(reasons),
                               "tie_break": "full_hash_geometry_producer_ref", "reasons": reasons})
    return {"method": METHOD, "evidence_generation": generation, "items": identities,
            "collisions": collisions,
            "stable": not any("SOURCE_ID_AMBIGUITY" in row["review_reason_codes"]
                              for row in identities.values())}


def lock_text_evidence(blocks, pdf_hash, page_num, metadata):
    """Snapshot every text occurrence before Docling/table absorption or order."""
    logical = metadata.setdefault("logical_page_id", f"p{page_num:04d}")
    records = [{"ref": str(i), "kind": "b", "bbox": b.bbox,
                "text_raw": b.text if b.text_raw is None else b.text_raw,
                "producer_ref": b.producer_ref or ""} for i, b in enumerate(blocks)]
    identity = assign_source_ids(records, pdf_hash, logical, metadata.get("evidence_generation", 1))
    for i, block in enumerate(blocks):
        data = identity["items"][str(i)]
        for key in ("source_id", "source_id_method", "raw_text_sha8", "evidence_generation"):
            setattr(block, key, data[key])
        block.review_reason_codes = sorted(set(block.review_reason_codes + data["review_reason_codes"]))
    metadata["text_evidence"] = {"method": METHOD, "stable": identity["stable"],
                                 "collisions": identity["collisions"],
                                 "items": [b.to_dict() for b in blocks]}

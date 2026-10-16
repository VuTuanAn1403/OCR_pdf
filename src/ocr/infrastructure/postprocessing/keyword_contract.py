"""One literal counter, separate canonical phrases and ordered display rows."""
import hashlib
import json
from pathlib import Path
import unicodedata
from datetime import datetime, timezone

from src.ocr.domain.models.processing import KEYWORD_SCHEMA, POLICY, POLICY_VERSION
from src.ocr.infrastructure.postprocessing.keyword_counter import KeywordCounter


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def dictionary_snapshot(path, version="v1"):
    raw = Path(path).read_bytes()
    display = [unicodedata.normalize("NFC", line.strip()) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
    if not display:
        raise ValueError("Empty keyword dictionary")
    if len(display) > 16383:
        raise ValueError("Keyword columns exceed XLSX capacity")
    canonical, mapping = [], []
    by_key = {}
    for phrase in display:
        key = phrase.casefold()
        if key not in by_key:
            by_key[key] = "kw-" + hashlib.sha256(key.encode()).hexdigest()[:16]
            canonical.append({"id": by_key[key], "keyword": phrase})
        mapping.append(by_key[key])
    if version == "v1" and (len(display) != 88 or len(canonical) != 87):
        raise ValueError("Keyword v1 requires exactly 88 display rows and 87 canonical phrases")
    result = {"schema_version": "keyword-dictionary-v1", "keyword_set_version": version,
              "source_sha256": hashlib.sha256(raw).hexdigest(), "canonical_keywords": canonical,
              "display_columns": display, "display_to_canonical": mapping}
    result["snapshot_sha256"] = digest(result)
    return result


def validate_snapshot(snapshot):
    payload = {k:v for k,v in snapshot.items() if k!="snapshot_sha256"}
    if digest(payload) != snapshot.get("snapshot_sha256"):
        raise ValueError("Dictionary snapshot checksum mismatch")
    canonical = snapshot["canonical_keywords"]
    by_id = {r["id"]:r["keyword"].casefold() for r in canonical}
    if len(by_id) != len(canonical) or len(snapshot["display_columns"]) != len(snapshot["display_to_canonical"]):
        raise ValueError("Invalid dictionary mapping")
    if any(by_id.get(ref) != word.casefold() for ref,word in zip(snapshot["display_to_canonical"],snapshot["display_columns"])):
        raise ValueError("Display phrase mapped to another keyword")


def keyword_identity(text_path, snapshot):
    return {"result_md_sha256": hashlib.sha256(Path(text_path).read_bytes()).hexdigest(),
            "dictionary_sha256": snapshot["snapshot_sha256"], "counting_policy": POLICY,
            "counting_version": POLICY_VERSION}


def count_report(doc_id, pdf_sha256, text_path, snapshot, pages):
    identity = keyword_identity(text_path, snapshot)
    report = KeywordCounter.count_keywords(Path(text_path).read_text(encoding="utf-8-sig"),
        [r["keyword"] for r in snapshot["canonical_keywords"]], doc_id, str(text_path))
    counts = {row["id"]: count.count for row, count in zip(snapshot["canonical_keywords"], report.keywords)}
    display = [counts[ref] for ref in snapshot["display_to_canonical"]]
    return {"schema_version": KEYWORD_SCHEMA, "document_id": doc_id, "pdf_sha256": pdf_sha256,
            "source_file": str(text_path), "identity": identity, "selected_pages": pages,
            "counted_at_utc":datetime.now(timezone.utc).isoformat(),
            "keyword_set_version": snapshot["keyword_set_version"], "canonical_counts": counts,
            "display_counts": display, "total_canonical_matches": sum(counts.values()),
            "total_display_matches": sum(display)}


def validate_report(report, snapshot, text_path, doc_id=None, pdf_sha256=None):
    validate_snapshot(snapshot)
    if (report.get("schema_version") != KEYWORD_SCHEMA or report.get("identity") != keyword_identity(text_path, snapshot)
            or doc_id is not None and report.get("document_id") != doc_id
            or pdf_sha256 is not None and report.get("pdf_sha256") != pdf_sha256):
        raise ValueError("Keyword report identity/schema mismatch")
    counts = report.get("canonical_counts", {})
    if set(counts) != {row["id"] for row in snapshot["canonical_keywords"]}:
        raise ValueError("Missing or extra canonical keyword")
    if any(type(n) is not int or n < 0 for n in counts.values()):
        raise ValueError("Counts must be non-negative integers")
    expected = [counts[key] for key in snapshot["display_to_canonical"]]
    if (any(type(n) is not int or n < 0 for n in report.get("display_counts",[]))
            or any(type(report.get(k)) is not int for k in ("total_canonical_matches","total_display_matches"))
            or report.get("display_counts") != expected or report.get("total_canonical_matches") != sum(counts.values())
            or report.get("total_display_matches") != sum(expected)):
        raise ValueError("Keyword mapping/totals mismatch")
    return True

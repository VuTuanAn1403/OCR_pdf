"""Compatible export ids (C3.7): `{doc_sha16}:p{page:04d}:{kind}{n:04d}`.

kind: b text block, t table, f formula, i preserved image region. `n` counts
per kind per page, in the page's list order (blocks are already reading-order
sorted), so ids depend only on the extraction output and never on how the
structure step later groups or drops elements. Canonical source IDs are
separate and assigned before ordering in source_identity.py. A finalized page
uses its shared ordered_item_ids; legacy callers retain the historical flow.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from src.ocr.domain.models.document import ExtractedDocument
from src.ocr.domain.models.formula import Formula
from src.ocr.domain.models.page import ExtractedPage
from src.ocr.domain.models.region import Region
from src.ocr.domain.models.table import TableStructure
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.infrastructure.export.markdown_exporter import MarkdownExporter


def make_id(doc_sha16: str, page: int, kind: str, n: int) -> str:
    return f"{doc_sha16}:p{page:04d}:{kind}{n:04d}"


@dataclass(frozen=True)
class Item:
    """One addressable element of a page."""
    id: str
    kind: str  # "b" | "t" | "f" | "i"
    page: int
    bbox: List[float]
    block: Optional[TextBlock] = None
    table: Optional[TableStructure] = None
    formula: Optional[Formula] = None
    region: Optional[Region] = None
    page_height: Optional[float] = None


def _bbox(values) -> List[float]:
    return [round(float(v), 2) for v in values] if values and len(values) >= 4 else []


def page_items(doc_sha16: str, page: ExtractedPage, *, ignore_flow: bool = False) -> List[Item]:
    """Items of one page in reading order: text blocks in list order, with
    tables, formulas and images inserted before the first block below them."""
    n = page.page_num
    blocks = [Item(make_id(doc_sha16, n, "b", i), "b", n, _bbox(b.bbox), block=b, page_height=page.height)
              for i, b in enumerate(page.blocks, 1)]
    others = (
        [Item(make_id(doc_sha16, n, "t", i), "t", n, _bbox(t.bbox), table=t) for i, t in enumerate(page.tables, 1)]
        + [Item(make_id(doc_sha16, n, "f", i), "f", n, _bbox(f.bbox), formula=f) for i, f in enumerate(page.formulas, 1)]
        + [Item(make_id(doc_sha16, n, "i", i), "i", n, _bbox(r.bbox.to_list()), region=r)
           for i, r in enumerate(MarkdownExporter.image_regions_to_preserve(page), 1)]
    )
    if not ignore_flow and "ordered_item_ids" in page.metadata:
        by_id = {item.id: item for item in blocks + others}
        flow = page.metadata["ordered_item_ids"]
        if len(flow) != len(by_id) or len(set(flow)) != len(flow) or set(flow) != set(by_id):
            raise ValueError(f"Page {n}: ordered_item_ids loses, duplicates or references unknown items")
        return [by_id[item_id] for item_id in flow]
    ordered = list(blocks)
    # Stable: elements at the same y keep their kind/list order.
    for other in sorted(others, key=lambda item: item.bbox[1] if item.bbox else float("inf")):
        top = other.bbox[1] if other.bbox else float("inf")
        position = next((k for k, item in enumerate(ordered)
                         if item.kind == "b" and item.bbox and item.bbox[1] > top), len(ordered))
        ordered.insert(position, other)
    return ordered


def document_items(doc: ExtractedDocument) -> List[List[Item]]:
    return [page_items(doc.file_hash, page) for page in doc.pages]

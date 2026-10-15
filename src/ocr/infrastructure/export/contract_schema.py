"""Versioned C3.7 JSON Schemas and semantic source checks.

The same Pydantic models generate Draft 2020-12 JSON Schema and validate the
contracts without an extra runtime dependency. Cross-file references, page
ownership and tree consistency require the additional pair validation below.
Additive fields are allowed for backward-compatible schema evolution.
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator


def _valid_bbox(box: List[float]) -> List[float]:
    if box[2] < box[0] or box[3] < box[1]:
        raise ValueError("bbox has reversed coordinates")
    return box


Coordinate = Annotated[float, Field(allow_inf_nan=False)]
BBox = Annotated[List[Coordinate], Field(min_length=4, max_length=4), AfterValidator(_valid_bbox)]


def _evidence_bbox(box):
    if not box:
        return box  # UNPLACED is checked semantically, without a fictitious box.
    if len(box) == 4:
        _valid_bbox(box)
    if len(box) != 4 or box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("Evidence bbox must have positive area, or be empty for UNPLACED")
    return box


EvidenceBBox = Annotated[List[Coordinate], AfterValidator(_evidence_bbox)]
ElementId = Annotated[str, Field(pattern=r"^[^:\s]+:p\d{4}:[btfi]\d{4}$")]
SectionId = Annotated[str, Field(pattern=r"^[^:\s]+:s\d{4}$")]
PageNumber = Annotated[int, Field(ge=1)]
Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)


class Span(ContractModel):
    page: PageNumber
    bbox: EvidenceBBox
    block_ids: Annotated[List[ElementId], Field(min_length=1)]


class TextProvenance(ContractModel):
    text_raw: str
    text_corrected: str
    correction_source: Optional[str]
    source: Annotated[str, Field(min_length=1)]
    device: Annotated[str, Field(min_length=1)]
    confidence: Optional[Confidence]
    confidence_source: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def truthful_provenance(self):
        if self.confidence is None and self.confidence_source != "unavailable":
            raise ValueError("null confidence requires confidence_source=unavailable")
        if self.text_raw != self.text_corrected and not self.correction_source:
            raise ValueError("changed text requires correction_source")
        return self


class Cell(TextProvenance):
    row: Annotated[int, Field(ge=0)]
    col: Annotated[int, Field(ge=0)]
    row_span: Annotated[int, Field(ge=1)]
    col_span: Annotated[int, Field(ge=1)]
    bbox: BBox
    bbox_source: Literal["cell", "table_fallback"]
    spans: Annotated[List[Span], Field(min_length=1)]


class ExtractionItem(TextProvenance):
    id: ElementId
    kind: Literal["b", "t", "f", "i"]
    bbox: EvidenceBBox
    cells: List[Cell] = Field(default_factory=list)


class ExtractionPage(ContractModel):
    page: PageNumber
    width: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    height: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    page_type: str
    coordinate_space: Literal["pdf_points_top_left", "pdf_points_top_left_rotated"]
    transforms: List[Dict[str, Any]]
    items: List[ExtractionItem]


class ExtractionContract(ContractModel):
    schema_version: Literal["extraction-v1", "extraction-v2-layout"]
    doc_id: str
    doc_sha16: str
    pipeline_version: str
    profile: str
    total_pages: Annotated[int, Field(ge=0)]
    pages: List[ExtractionPage]


class DocumentSection(ContractModel):
    section_id: SectionId
    level: Annotated[int, Field(ge=0, le=4)]
    title: str
    title_en: Optional[str]
    parent_id: Optional[SectionId]
    page_start: Optional[PageNumber]
    page_end: Optional[PageNumber]
    heading_item_ids: List[ElementId]
    spans: List[Span]
    chunk_ids: List[ElementId]


class Chunk(TextProvenance):
    chunk_id: ElementId
    section_id: SectionId
    type: Literal["paragraph", "list_item", "table", "formula", "image"]
    lang: Literal["vi", "en", "unknown"]
    spans: Annotated[List[Span], Field(min_length=1)]
    needs_review: bool
    # C3.6 figure links; absent in documents written before them.
    role: Optional[Literal["figure_text", "caption"]] = None
    figure_id: Optional[ElementId] = None


CheckStatus = Literal["CONSISTENT", "VIOLATION", "INCONCLUSIVE"]


class ConsistencyCheck(ContractModel):
    """One flag-only C3.5(b) result; values are never corrected."""
    check: Literal["total_row", "amount_cell", "single_value_cell", "balance_identity"]
    status: CheckStatus


class ContinuationCandidate(ContractModel):
    """C3.5(c) candidate; tables are never merged in this schema version."""
    status: Literal["CANDIDATE"]
    merged: Literal[False]
    previous_table_id: ElementId
    table_id: ElementId
    signals: Annotated[List[str], Field(min_length=1)]


class ConsistencyChecks(ContractModel):
    method: str
    status: Literal["COMPLETED", "FAILED"]
    balance_identity: Optional[List[ConsistencyCheck]] = None
    continuation_candidates: Optional[List[ContinuationCandidate]] = None


class DocumentTable(ContractModel):
    table_id: ElementId
    chunk_id: ElementId
    section_id: SectionId
    pages: Annotated[List[PageNumber], Field(min_length=1)]
    num_rows: Annotated[int, Field(ge=0)]
    num_cols: Annotated[int, Field(ge=0)]
    has_merged_cells: bool
    engine: str
    html_path: Optional[str]
    cells: List[Cell]
    # Optional so documents written before the C3.5 checks stay valid.
    needs_review: Optional[bool] = None
    arithmetic_checks: Optional[List[ConsistencyCheck]] = None
    continuation: Optional[ContinuationCandidate] = None


class DocumentFormula(ContractModel):
    formula_id: ElementId
    chunk_id: ElementId
    section_id: SectionId


class ImageCaption(ContractModel):
    chunk_id: ElementId
    position: Literal["above", "below"]
    gap_pt: Annotated[float, Field(ge=0, allow_inf_nan=False)]


class DocumentImage(ContractModel):
    image_id: ElementId
    chunk_id: ElementId
    section_id: SectionId
    asset: Optional[str]
    caption: Optional[ImageCaption] = None
    figure_text_chunk_ids: Optional[List[ElementId]] = None


class FigureLinks(ContractModel):
    method: str
    status: Literal["COMPLETED", "FAILED"]


class DocumentContract(ContractModel):
    schema_version: Literal["doc-v1", "doc-v2-layout"]
    doc_id: str
    doc_sha16: str
    source: Dict[str, Any]
    extraction: Literal["extraction.json"]
    sections: List[DocumentSection]
    chunks: List[Chunk]
    tables: List[DocumentTable]
    formulas: List[DocumentFormula]
    images: List[DocumentImage]
    consistency_checks: Optional[ConsistencyChecks] = None
    figure_links: Optional[FigureLinks] = None


class StructureSection(ContractModel):
    section_id: SectionId
    level: Annotated[int, Field(ge=1, le=4)]
    title: str
    title_en: Optional[str]
    parent_id: Optional[SectionId]
    page_start: PageNumber
    page_end: PageNumber
    heading_item_ids: Annotated[List[ElementId], Field(min_length=1)]
    spans: Annotated[List[Span], Field(min_length=1)]
    needs_review: bool


class DroppedItem(ContractModel):
    item_id: ElementId
    reason: Annotated[str, Field(min_length=1)]


class StructureContract(ContractModel):
    schema_version: Literal["structure-v1", "structure-v2-layout"]
    doc_sha16: str
    method: Annotated[str, Field(min_length=1)]
    params: Dict[str, Any]
    body_font_size: Optional[Coordinate]
    toc: Optional[Dict[str, Any]]
    sections: List[StructureSection]
    dropped: List[DroppedItem]


def contract_json_schemas(*, include_layout: bool = False) -> Dict[str, Dict[str, Any]]:
    """Return portable JSON Schemas with explicit dialect and version IDs."""
    result = {}
    versions = [("extraction-v1", ExtractionContract), ("doc-v1", DocumentContract), ("structure-v1", StructureContract)]
    if include_layout:
        versions += [("extraction-v2-layout", ExtractionContract), ("doc-v2-layout", DocumentContract),
                     ("structure-v2-layout", StructureContract)]
    for name, model in versions:
        schema = model.model_json_schema()
        schema["properties"]["schema_version"] = {"const": name, "type": "string"}
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:ocr-pdf:{name}"
        result[name] = schema
    return result


def validate_extraction(extraction: Dict[str, Any]) -> List[str]:
    """Check extraction semantics even when the other contracts are absent."""
    try:
        source = ExtractionContract.model_validate(extraction)
    except ValidationError as exc:
        return [f"extraction.json {'.'.join(map(str, e['loc']))}: {e['msg']}"
                for e in exc.errors(include_url=False, include_input=False)]
    errors = []
    known = {}
    pages = set()
    for page in source.pages:
        extras = page.model_extra or {}
        flow = extras.get("ordered_item_ids")
        if flow is not None:
            item_ids = [item.id for item in page.items]
            if flow != item_ids or len(set(flow)) != len(flow):
                errors.append(f"extraction.json page {page.page}: ordered_item_ids differs from item sequence or duplicates items")
            indices = [(item.model_extra or {}).get("reading_order_index") for item in page.items]
            if indices != list(range(len(page.items))):
                errors.append(f"extraction.json page {page.page}: reading_order_index is not contiguous")
            source_ids = [(item.model_extra or {}).get("source_id") for item in page.items]
            if None in source_ids or len(set(source_ids)) != len(source_ids):
                errors.append(f"extraction.json page {page.page}: source_id missing or duplicated")
            for item in page.items:
                meta = item.model_extra or {}
                if meta.get("visibility") == "suppressed" and (
                        meta.get("role_final") not in ("running_header", "running_footer")
                        or meta.get("role_status") != "ACCEPTED" or meta.get("protected_from_furniture")
                        or len(set(meta.get("support_page_ids", []))) < 3):
                    errors.append(f"extraction.json {item.id}: invalid furniture suppression")
            current_sources = set(source_ids)
            dispositions = extras.get("source_dispositions") or {}
            for archived in (extras.get("text_evidence") or {}).get("items", []):
                sid = archived.get("source_id")
                disposition = dispositions.get(sid, {})
                if sid not in current_sources:
                    owners = [item for item in page.items if item.kind in ("t", "f")
                              and archived.get("source_id") in (item.model_extra or {}).get("derived_from_source_ids", [])]
                    if (len(owners) != 1 or disposition.get("status") != (
                            "REPLACED_BY_TABLE" if owners[0].kind == "t" else "REPLACED_BY_FORMULA")):
                        errors.append(f"extraction.json archived source {sid}: missing source disposition/mapping")
        if page.page in pages:
            errors.append(f"extraction.json duplicate page {page.page}")
        pages.add(page.page)
        if page.page > source.total_pages:
            errors.append(f"extraction.json page {page.page} exceeds total_pages")
        for item in page.items:
            if not item.bbox and "UNPLACED" not in (item.model_extra or {}).get("review_reason_codes", []):
                errors.append(f"extraction.json {item.id}: missing bbox requires UNPLACED review")
            if item.id in known:
                errors.append(f"extraction.json duplicate element id {item.id}")
            known[item.id] = (page.page, item)
            if not item.id.startswith(f"{source.doc_sha16}:p{page.page:04d}:{item.kind}"):
                errors.append(f"extraction.json {item.id} does not belong to its document/page/kind")
            if item.cells and item.kind != "t":
                errors.append(f"extraction.json {item.id} has cells but is not a table")
            coordinates = set()
            for cell in item.cells:
                if (cell.row, cell.col) in coordinates:
                    errors.append(f"extraction.json table {item.id} has duplicate cell coordinates")
                coordinates.add((cell.row, cell.col))
                for span in cell.spans:
                    if span.page != page.page or any(ref != item.id for ref in span.block_ids):
                        errors.append(f"extraction.json table {item.id} cell does not reference its owning table/page")
                if cell.bbox_source == "table_fallback" and cell.bbox != item.bbox:
                    errors.append(f"extraction.json table {item.id} fallback cell bbox differs from table bbox")
    return errors


def validate_contract_set(extraction: Dict[str, Any], document: Dict[str, Any],
                          structure: Dict[str, Any]) -> List[str]:
    errors = validate_contract_pair(extraction, document)
    try:
        tree = StructureContract.model_validate(structure)
    except ValidationError as exc:
        return errors + [f"structure.json {'.'.join(map(str, e['loc']))}: {e['msg']}"
                         for e in exc.errors(include_url=False, include_input=False)]
    if errors:
        return errors
    doc = DocumentContract.model_validate(document)
    source = ExtractionContract.model_validate(extraction)
    modern = source.schema_version == "extraction-v2-layout"
    if tree.schema_version != ("structure-v2-layout" if modern else "structure-v1"):
        errors.append("structure.json schema differs from extraction.json layout version")
    if doc.schema_version != ("doc-v2-layout" if modern else "doc-v1"):
        errors.append("document.json schema differs from extraction.json layout version")
    if tree.doc_sha16 != source.doc_sha16:
        errors.append("structure.json belongs to a different document")
    headings = {s.section_id: s for s in doc.sections if s.level > 0}
    if len({s.section_id for s in tree.sections}) != len(tree.sections):
        errors.append("structure.json duplicate section ids")
    if set(headings) != {s.section_id for s in tree.sections}:
        errors.append("structure.json sections differ from document.json")
    fields = ("level", "title", "title_en", "parent_id", "page_start", "page_end", "heading_item_ids", "spans")
    for section in tree.sections:
        peer = headings.get(section.section_id)
        if peer and any(getattr(section, field) != getattr(peer, field) for field in fields):
            errors.append(f"structure.json section {section.section_id} differs from document.json")
    known = {item.id for page in source.pages for item in page.items}
    consumed = {ref for section in doc.sections for span in section.spans for ref in span.block_ids}
    consumed.update(ref for chunk in doc.chunks for span in chunk.spans for ref in span.block_ids)
    dropped = {item.item_id for item in tree.dropped}
    if len(dropped) != len(tree.dropped):
        errors.append("structure.json duplicate dropped item ids")
    for item in tree.dropped:
        if item.item_id not in known or item.item_id in consumed:
            errors.append(f"structure.json dropped item {item.item_id} has an invalid source or is also consumed")
    for ref in sorted(known - consumed - dropped):
        errors.append(f"document.json loses extraction item {ref} without a recorded reason")
    # Cells copied to the document must preserve their source table contents.
    tables = {item.id: item for page in source.pages for item in page.items if item.kind == "t"}
    for table in doc.tables:
        peer = tables.get(table.table_id)
        if peer and table.cells != peer.cells:
            errors.append(f"document.json table {table.table_id} cells differ from extraction.json")
    return errors


def validate_contract_pair(extraction: Dict[str, Any], document: Dict[str, Any]) -> List[str]:
    """Return structural/schema/source errors, suitable for the artifact validator."""
    errors: List[str] = validate_extraction(extraction)
    parsed = []
    for name, model, payload in (("extraction.json", ExtractionContract, extraction),
                                 ("document.json", DocumentContract, document)):
        try:
            parsed.append(model.model_validate(payload))
        except ValidationError as exc:
            errors.extend(f"{name} {'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                          for e in exc.errors(include_url=False, include_input=False))
    if errors:
        return errors
    source, doc = parsed
    if doc.schema_version != ("doc-v2-layout" if source.schema_version == "extraction-v2-layout" else "doc-v1"):
        errors.append("document.json schema differs from extraction.json layout version")
    if (source.doc_id, source.doc_sha16) != (doc.doc_id, doc.doc_sha16):
        errors.append("document.json identity differs from extraction.json")

    known = {}
    page_numbers = set()
    for page in source.pages:
        if page.page in page_numbers:
            errors.append(f"extraction.json duplicate page {page.page}")
        page_numbers.add(page.page)
        for item in page.items:
            if item.id in known:
                errors.append(f"extraction.json duplicate element id {item.id}")
            known[item.id] = (page.page, item)
            if not item.id.startswith(f"{source.doc_sha16}:p{page.page:04d}:{item.kind}"):
                errors.append(f"extraction.json {item.id} does not belong to its document/page/kind")

    def check_spans(spans: List[Span], where: str, file_name: str = "document.json") -> None:
        for span in spans:
            if span.page not in page_numbers:
                errors.append(f"{file_name} {where} references unknown page {span.page}")
            for ref in span.block_ids:
                if ref not in known:
                    errors.append(f"{file_name} {where} references unknown id {ref}")
                elif known[ref][0] != span.page:
                    errors.append(f"{file_name} {where} id {ref} is on a different page than its span")

    for page in source.pages:
        for item in page.items:
            for cell in item.cells:
                check_spans(cell.spans, f"cell {item.id} {cell.row},{cell.col}", "extraction.json")
                if any(ref != item.id for span in cell.spans for ref in span.block_ids):
                    errors.append(f"extraction.json table {item.id} cell does not reference its owning table")

    sections = {s.section_id: s for s in doc.sections}
    if len(sections) != len(doc.sections):
        errors.append("document.json duplicate section ids")
    chunks = {c.chunk_id: c for c in doc.chunks}
    if len(chunks) != len(doc.chunks):
        errors.append("document.json duplicate chunk ids")
    for section in doc.sections:
        if not section.section_id.startswith(f"{doc.doc_sha16}:s"):
            errors.append(f"document.json section {section.section_id} belongs to a different document")
        parent = sections.get(section.parent_id)
        if section.parent_id and (parent is None or parent.level >= section.level):
            errors.append(f"document.json section {section.section_id} has an invalid parent")
        if section.page_start is not None and (section.page_end is None or section.page_end < section.page_start):
            errors.append(f"document.json section {section.section_id} has an invalid page range")
        refs = [ref for span in section.spans for ref in span.block_ids]
        if set(refs) != set(section.heading_item_ids):
            errors.append(f"document.json section {section.section_id} heading spans differ from heading_item_ids")
        check_spans(section.spans, f"section {section.section_id}")
        for ref in section.chunk_ids:
            if ref not in chunks or chunks[ref].section_id != section.section_id:
                errors.append(f"document.json section {section.section_id} has an invalid chunk id {ref}")
    for chunk in doc.chunks:
        section = sections.get(chunk.section_id)
        if section is None or section.chunk_ids.count(chunk.chunk_id) != 1:
            errors.append(f"document.json chunk {chunk.chunk_id} is outside the section tree")
        check_spans(chunk.spans, f"chunk {chunk.chunk_id}")
        if chunk.chunk_id != chunk.spans[0].block_ids[0]:
            errors.append(f"document.json chunk {chunk.chunk_id} does not use its first source id")
        allowed = {"paragraph": {"b"}, "list_item": {"b"}, "table": {"t"},
                   "formula": {"b", "f"}, "image": {"i"}}[chunk.type]
        if any(ref in known and known[ref][1].kind not in allowed
               for span in chunk.spans for ref in span.block_ids):
            errors.append(f"document.json chunk {chunk.chunk_id} references the wrong element kind")
    for collection, field in ((doc.tables, "table_id"), (doc.formulas, "formula_id"), (doc.images, "image_id")):
        for entry in collection:
            ref = getattr(entry, field)
            chunk = chunks.get(entry.chunk_id)
            if ref not in known:
                errors.append(f"document.json {field} references unknown id {ref}")
            if chunk is None or chunk.section_id != entry.section_id or ref != entry.chunk_id:
                errors.append(f"document.json {field} {ref} has an invalid chunk reference")
            expected_type = {"table_id": "table", "formula_id": "formula", "image_id": "image"}[field]
            if chunk and chunk.type != expected_type:
                errors.append(f"document.json {field} {ref} references a chunk of the wrong type")
    images = {image.image_id: image for image in doc.images}
    for image in doc.images:
        linked = [(ref, "figure_text") for ref in image.figure_text_chunk_ids or []]
        if image.caption is not None:
            linked.append((image.caption.chunk_id, "caption"))
        for ref, role in linked:
            chunk = chunks.get(ref)
            if chunk is None or chunk.role != role or chunk.figure_id != image.image_id:
                errors.append(f"document.json image {image.image_id} {role} link {ref} is not mirrored by its chunk")
    for chunk in doc.chunks:
        if (chunk.role is None) != (chunk.figure_id is None):
            errors.append(f"document.json chunk {chunk.chunk_id} has role and figure_id out of step")
        elif chunk.figure_id is not None:
            image = images.get(chunk.figure_id)
            if image is None:
                listed = []
            elif chunk.role == "figure_text":
                listed = image.figure_text_chunk_ids or []
            else:
                listed = [image.caption.chunk_id] if image.caption else []
            if not listed or chunk.chunk_id not in listed:
                errors.append(f"document.json chunk {chunk.chunk_id} references figure {chunk.figure_id} "
                              "that does not list it")
    for table in doc.tables:
        chunk = chunks.get(table.chunk_id)
        owning_sources = ({ref for span in chunk.spans for ref in span.block_ids}
                          if chunk and chunk.type == "table" else {table.table_id})
        if chunk and (chunk.type != "table" or set(table.pages) != {span.page for span in chunk.spans}):
            errors.append(f"document.json table {table.table_id} pages/type differ from its chunk")
        for cell in table.cells:
            check_spans(cell.spans, f"table {table.table_id} cell {cell.row},{cell.col}")
            if any(ref not in owning_sources for span in cell.spans for ref in span.block_ids):
                errors.append(f"document.json table {table.table_id} cell does not reference its owning table")
            if any(span.page not in table.pages for span in cell.spans):
                errors.append(f"document.json table {table.table_id} cell page is outside table pages")
            if cell.row + cell.row_span > table.num_rows or cell.col + cell.col_span > table.num_cols:
                errors.append(f"document.json table {table.table_id} cell is outside table dimensions")
    return errors

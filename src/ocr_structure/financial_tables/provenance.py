"""Bind complete source blocks to unique cells and preserve numeric evidence."""
from __future__ import annotations

from collections import Counter
from ocr_text.numeric_guard import protected_numeric_fragments


def bind_sources(table, segment, originals):
    """Accept a grid only if each whole source has one cell and every token survives."""
    if len({id(b) for b in segment}) != len(segment):
        return False
    source_ids = [b.source_id for b in segment if b.source_id is not None]
    if len(set(source_ids)) != len(source_ids):
        return False
    segment_ids = {id(b) for b in segment}
    # Legacy removal uses the table rectangle: refuse a rectangle that would
    # swallow an unrepresented source, including an unsupported OCR line.
    for block in originals:
        if id(block) not in segment_ids and len(block.bbox) == 4:
            x = (block.bbox[0] + block.bbox[2]) / 2
            y = (block.bbox[1] + block.bbox[3]) / 2
            if table.bbox[0] <= x <= table.bbox[2] and table.bbox[1] <= y <= table.bbox[3]:
                return False
    positions = {id(block): index for index, block in enumerate(originals)}
    sources, assigned, covered = [], {}, set()
    for cell in table.cells:
        for row in range(cell.row_idx, cell.row_idx + cell.row_span):
            for col in range(cell.col_idx, cell.col_idx + cell.col_span):
                if (row, col) in covered:
                    return False
                covered.add((row, col))
    for block in segment:
        matches = [index for index, cell in enumerate(table.cells)
                   if cell.text and len(cell.bbox) == 4
                   and cell.bbox[0] <= block.bbox[0] and cell.bbox[1] <= block.bbox[1]
                   and block.bbox[2] <= cell.bbox[2] and block.bbox[3] <= cell.bbox[3]]
        if len(matches) != 1:
            return False
        index = matches[0]
        assigned.setdefault(index, []).append(block)
        sources.append({"source_id": block.source_id, "input_block_index": positions[id(block)],
                        "bbox": list(block.bbox), "text": block.text,
                        "text_raw": block.text_raw if block.text_raw is not None else block.text,
                        "row_idx": table.cells[index].row_idx, "col_idx": table.cells[index].col_idx})
    for index, cell in enumerate(table.cells):
        blocks = sorted(assigned.get(index, []), key=lambda b: (b.bbox[1], b.bbox[0]))
        if " ".join(b.text.strip() for b in blocks).strip() != cell.text:
            return False
    numeric_source = Counter(fragment for block in segment for fragment in protected_numeric_fragments(block.text))
    numeric_cells = Counter(fragment for cell in table.cells for fragment in protected_numeric_fragments(cell.text))
    if numeric_source != numeric_cells:
        return False
    table.provenance.update(source_mapping_method="whole-ocr-block-cell-v1", source_blocks=sources,
                            numeric_guard="PRESERVED", source_coverage="COMPLETE")
    return True

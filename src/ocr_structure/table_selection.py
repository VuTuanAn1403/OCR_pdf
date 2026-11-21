"""Prefer model cells to overlapping unverified heuristic rectangles."""
from ocr_contracts.geometry import block_is_covered


def retain_heuristic_candidates(tables, model_tables, metadata):
    """A broad heuristic must not duplicate several smaller model tables.

    No OCR source is deleted here. The document reconciliation pass retains
    sources outside, or missing from, the selected cells as review text.
    Source-checked financial grids keep their separate preservation policy.
    """
    retained, superseded = [], []
    for table in tables:
        replaced = block_is_covered(table.bbox, model_tables, .5)
        if table.engine == 'heuristic':
            replaced = replaced or any(block_is_covered(model.bbox, [table], .8)
                                      for model in model_tables)
        if replaced:
            superseded.append(table.model_dump(mode='json'))
        else:
            retained.append(table)
    if superseded:
        metadata['superseded_heuristic_tables'] = superseded
    return retained

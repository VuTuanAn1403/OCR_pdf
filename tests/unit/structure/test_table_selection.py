from ocr_contracts.table import TableCell, TableStructure
from ocr_structure.table_selection import retain_heuristic_candidates


def table(name, box, engine='heuristic'):
    return TableStructure(table_id=name, page=1, bbox=box, num_rows=1, num_cols=1,
                          cells=[TableCell(row_idx=0,col_idx=0,text='120',bbox=box)],engine=engine)


def test_wide_heuristic_cannot_duplicate_two_smaller_model_grids():
    old = table('combined',[0,0,100,300])
    models = [table('top',[0,0,100,70],'docling_tableformer'),
              table('bottom',[0,100,100,170],'docling_tableformer')]
    outside = table('outside',[200,0,300,100])
    metadata = {}
    kept = retain_heuristic_candidates([old,outside],models,metadata)
    assert kept == [outside]
    original, = metadata['superseded_heuristic_tables']
    assert original['cells'][0]['text'] == '120'
    assert old.cells[0].text == '120' and len(models) == 2


def test_source_checked_financial_grid_is_not_replaced_by_a_small_partial_model():
    old=table('financial',[0,0,100,300],'deterministic_geometry')
    metadata={}
    assert retain_heuristic_candidates([old],[table('partial',[0,0,100,70])],metadata)==[old]
    assert not metadata

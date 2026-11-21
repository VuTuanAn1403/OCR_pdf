from ocr_contracts.table import TableStructure,TableCell
from ocr_contracts.text_block import TextBlock
from ocr_structure.text_table_alignment import align_text_table


def fixture():
    data=[('Bên liên quan',[10,10,80,20]),('Mối quan hệ',[150,10,220,20]),
          ('Công ty A',[10,40,90,50]),('Dòng đầu',[150,40,250,50]),('Dòng tiếp',[150,55,250,65]),
          ('Công ty B',[10,70,90,80]),('Mô tả B',[150,70,250,80])]
    blocks=[TextBlock(text=t,text_raw=t,bbox=b,page=1,source_id=f's{i}') for i,(t,b) in enumerate(data)]
    model=TableStructure(table_id='model',page=1,bbox=[5,5,260,85],num_rows=3,num_cols=2,
      engine='docling_tableformer',cells=[TableCell(row_idx=0,col_idx=0,text='Bên liên quan',column_header=True),
      TableCell(row_idx=0,col_idx=1,text='Mối quan hệ',column_header=True)])
    return model,blocks


def test_wrapped_description_follows_observed_row_start_and_keeps_every_source():
    model,blocks=fixture();table=align_text_table(model,blocks)
    assert table is not None and table.num_rows==3
    assert next(c.text for c in table.cells if c.row_idx==1 and c.col_idx==1)=='Dòng đầu Dòng tiếp'
    assert table.provenance['source_coverage']=='COMPLETE'
    assert table.confidence is None and table.needs_review


def test_wrapped_label_or_crossing_row_abstains():
    model,blocks=fixture();model.num_rows=4
    assert align_text_table(model,blocks) is None
    model.num_rows=3;blocks[4].bbox=[150,55,250,75]
    assert align_text_table(model,blocks) is None

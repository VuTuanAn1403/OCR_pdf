from types import SimpleNamespace
import pytest
from ocr_contracts.region import Region, BoundingBox, ContentType
from ocr_contracts.text_block import TextBlock
from ocr_structure.small_image_tables import reconstruct_small_image_tables


def page():
    entries=[('Tại ngày 31 tháng 12 năm 2024',[250,20,340,35]),
             ('Tại ngày 31 tháng 12 năm 2023',[370,20,460,35]),
             ('VND',[310,40,340,50]),('VND',[430,40,460,50]),
             ('Cổ phiếu chưa niêm yết',[30,70,180,82]),
             ('1.234.000',[270,70,340,82]),('2.345.000',[390,70,460,82])]
    blocks=[TextBlock(text=text,text_raw=text,bbox=box,page=1,source_id=f's{i}') for i,(text,box) in enumerate(entries)]
    return SimpleNamespace(page_num=1,metadata={'text_evidence':{'items':[b.model_dump() for b in blocks]}},
        regions=[Region(region_id='image',content_type=ContentType.IMAGE,bbox=BoundingBox(x0=20,y0=10,x1=470,y1=95))])


def test_single_amount_row_needs_two_unit_anchors_and_preserves_whole_raw_sources():
    p=page();table,=reconstruct_small_image_tables(p)
    assert table.num_cols==3 and table.num_rows==3
    assert table.provenance['source_coverage']=='COMPLETE' and table.provenance['numeric_guard']=='PRESERVED'
    assert table.confidence is None and table.needs_review
    assert any(c.text=='Tại ngày 31 tháng 12 năm 2024' for c in table.cells)
    assert any(c.text=='1.234.000' for c in table.cells)
    assert len(table.provenance['source_blocks'])==7


@pytest.mark.parametrize('kind',['missing-unit','ambiguous-header','native','oriented','duplicate-id','second-data-row'])
def test_ambiguous_image_tables_are_not_forced_into_a_grid(kind):
    p=page();rows=p.metadata['text_evidence']['items']
    if kind=='missing-unit':rows[2]['text']='đơn vị'
    if kind=='ambiguous-header':rows[0]['bbox']=[250,20,460,35]
    if kind=='native':
        for b in rows:b['source']='native'
    if kind=='oriented':
        for b in rows:b['recognition_provenance']={'method':'vertical-line-orientation-v1','rotation_clockwise_degrees':270}
    if kind=='duplicate-id':rows[-1]['source_id']=rows[-2]['source_id']
    if kind=='second-data-row':rows[-1]['bbox']=[390,90,460,102]
    assert not reconstruct_small_image_tables(p)

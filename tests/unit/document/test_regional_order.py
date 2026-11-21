import copy
import pytest
from ocr_contracts.region_frame import RegionFrame
from ocr_document.region_tree import LayoutItem,infer_region_tree
from ocr_document.regional_order import infer_observed_region_tree


@pytest.mark.parametrize('angle',[90,180,270])
def test_rotated_intro_table_and_footnote_follow_canonical_order_without_moving_sources(angle):
    frame=RegionFrame((100,50,300,450),angle);w,h=frame.size
    boxes=[[10,5,w-10,.1*h],[10,.2*h,w-10,.7*h],[10,.8*h,w-10,.9*h]]
    items=[LayoutItem(name,frame.to_source_bbox(box),text=name,kind='t' if name=='table' else 'b')
           for name,box in zip(('intro','table','note'),boxes)]
    items.append(LayoutItem('left',[0,60,50,75],text='outside'))
    before=copy.deepcopy(items)
    result=infer_observed_region_tree(items,[dict(id='rotated',bbox=list(frame.bbox),clockwise_degrees=angle)])
    # The outer region-tree policy places the region as one atomic group;
    # this pass changes its internal reading direction without losing neighbours.
    assert [i for i in result.ordered_ids if i!='left']==['intro','table','note']
    assert result.ordered_ids.index('left') in (0,3)
    assert items==before and set(result.assignments)=={i.id for i in items}
    assert result.tree['observed_coordinate_views'][0]['ordered_source_ids']==['intro','table','note']


def test_no_observed_turn_keeps_existing_layout_exactly():
    items=[LayoutItem('a',[0,0,100,10],text='a'),LayoutItem('b',[0,20,100,30],text='b')]
    assert infer_observed_region_tree(items,[])==infer_region_tree(items)

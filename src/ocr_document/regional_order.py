"""Order observed quarter-turn regions without changing source geometry."""
import copy

from ocr_contracts.geometry import block_is_covered
from ocr_contracts.region_frame import RegionFrame
from ocr_document.region_tree import LayoutItem,infer_region_tree


def infer_observed_region_tree(items, views, *, min_gap=3.):
    regions=[]
    for view in views:
        angle=view.get('clockwise_degrees',0)
        if angle not in (90,180,270):continue
        try:frame=RegionFrame(tuple(view['bbox']),angle)
        except (ValueError,KeyError,TypeError):continue
        regions.append((view['id'],frame))
    if not regions:return infer_region_tree(items,min_gap=min_gap)
    groups={name:[] for name,_ in regions};outside=[];reviews=[]
    for item in items:
        owners=[name for name,frame in regions if block_is_covered(item.bbox,[frame],.98)]
        if len(owners)==1:groups[owners[0]].append(item)
        else:
            outside.append(item)
            if owners:reviews.append({'code':'AMBIGUOUS_ROTATED_REGION_OWNER','item_ids':[item.id]})
    expansions={};records=[]
    for name,frame in regions:
        if not groups[name]:continue
        proxy=f'observed-region:{name}'
        if any(item.id==proxy for item in items):raise ValueError('Region proxy collides with source ID')
        canonical=[LayoutItem(item.id,frame.to_view_bbox(item.bbox),item.text,item.role,item.kind) for item in groups[name]]
        inner=infer_region_tree(canonical,min_gap=min_gap)
        expansions[proxy]=inner.ordered_ids
        outside.append(LayoutItem(proxy,list(frame.bbox),kind='r',role='region'))
        records.append({'id':name,'frame':frame.to_dict(),'coordinate_space':'region_view_points',
                        'ordered_source_ids':inner.ordered_ids,'layout_tree':inner.tree})
        reviews.extend({**record,'observed_region_id':name,'coordinate_space':'region_view_points'} for record in inner.review)
    if not expansions:return infer_region_tree(items,min_gap=min_gap)
    result=infer_region_tree(outside,min_gap=min_gap)
    def expand(ids):
        return [source for item_id in ids for source in expansions.get(item_id,[item_id])]
    result.ordered_ids=expand(result.ordered_ids)
    assignments={}
    for item_id,assignment in result.assignments.items():
        for source in expansions.get(item_id,[item_id]):assignments[source]=copy.deepcopy(assignment)
    result.assignments=assignments
    def expand_tree(node):
        if 'item_ids' in node:node['item_ids']=expand(node['item_ids'])
        for child in node.get('children',[]):expand_tree(child)
    expand_tree(result.tree)
    for article in result.article_groups:
        article['item_ids']=expand(article.get('item_ids',[]))
    result.tree['observed_coordinate_views']=records
    result.review.extend(reviews)
    return result

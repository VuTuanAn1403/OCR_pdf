"""Align whole OCR lines to model-confirmed two-column text-table rows."""
import copy
from statistics import median

from ocr_contracts.geometry import block_is_covered,is_non_horizontal_line_candidate
from ocr_contracts.table import TableCell,TableStructure
from ocr_contracts.text_block import TextBlock
from ocr_structure.financial_tables.provenance import bind_sources


def _key(text):
    return ' '.join(text.casefold().split())


def _bbox(items):
    return [min(b.bbox[0] for b in items),min(b.bbox[1] for b in items),
            max(b.bbox[2] for b in items),max(b.bbox[3] for b in items)]


def align_text_table(table, originals):
    if table.engine!='docling_tableformer' or table.num_cols!=2 or table.num_rows<3:
        return None
    if table.provenance.get('table_orientation','rot_0')!='rot_0':
        return None
    headers=sorted((c for c in table.cells if c.row_idx==0 and c.column_header and c.col_span==1),key=lambda c:c.col_idx)
    if len(headers)!=2:
        return None
    blocks=[b for b in originals if len(b.bbox)==4 and b.text.strip()
            and b.source not in ('native','vector','docling')
            and not is_non_horizontal_line_candidate(b.recognition_provenance)
            and block_is_covered(b.bbox,[table],.98)]
    anchors=[]
    for cell in headers:
        matches=[b for b in blocks if _key(b.text)==_key(cell.text)]
        if len(matches)!=1:return None
        anchors.append(matches[0])
    if anchors[0].bbox[2]>=anchors[1].bbox[0]:return None
    cut=sum((b.bbox[0]+b.bbox[2])/2 for b in anchors)/2
    left=sorted((b for b in blocks if (b.bbox[0]+b.bbox[2])/2<cut),key=lambda b:b.bbox[1])
    right=[b for b in blocks if b not in left]
    # A wrapped first-column label must not silently become another row.
    if len(left)!=table.num_rows or not right or left[0] is not anchors[0]:return None
    if max(b.bbox[2] for b in left)>=min(b.bbox[0] for b in right):return None
    tolerance=.2*median(b.bbox[3]-b.bbox[1] for b in blocks)
    assigned={(i,0):[b] for i,b in enumerate(left)}
    for b in right:
        eligible=[i for i,a in enumerate(left) if a.bbox[1]<=b.bbox[1]+tolerance]
        if not eligible:return None
        assigned.setdefault((max(eligible),1),[]).append(b)
    if any((row,1) not in assigned for row in range(table.num_rows)):return None
    row_boxes=[_bbox([b for (r,c),items in assigned.items() if r==row for b in items]) for row in range(table.num_rows)]
    if any(a[3]>b[1]+.01 for a,b in zip(row_boxes,row_boxes[1:])):return None
    cells=[]
    for (row,col),items in sorted(assigned.items()):
        items=sorted(items,key=lambda b:(b.bbox[1],b.bbox[0]))
        cells.append(TableCell(row_idx=row,col_idx=col,text=' '.join(b.text.strip() for b in items),
                               bbox=_bbox(items),column_header=row==0,confidence=None))
    result=TableStructure(table_id=table.table_id+'_aligned',page=table.page,bbox=_bbox(blocks),
        num_rows=table.num_rows,num_cols=2,cells=cells,engine='deterministic_geometry',confidence=None,
        needs_review=True,provenance={**copy.deepcopy(table.provenance),'method':'whole-block-text-table-v1',
        'row_evidence':'model-row-count-and-observed-left-column-anchors-v1'})
    return result if bind_sources(result,blocks,originals) else None


def align_text_tables(page):
    originals=[TextBlock.model_validate(b) for b in page.metadata.get('text_evidence',{}).get('items',[])]
    selected=[];replaced=[]
    for table in page.tables:
        aligned=align_text_table(table,originals)
        if aligned is not None:
            replaced.append(table.model_dump(mode='json'))
        selected.append(aligned if aligned is not None else table)
    if replaced:page.metadata['text_table_model_hypotheses']=replaced
    return selected

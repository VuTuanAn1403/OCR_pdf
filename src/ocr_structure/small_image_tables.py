"""Source-checked single-row financial tables inside observed raster bounds."""
import re
from types import SimpleNamespace

from ocr_contracts.geometry import block_is_covered, is_non_horizontal_line_candidate
from ocr_contracts.table import TableCell, TableStructure
from ocr_contracts.text_block import TextBlock
from ocr_structure.financial_tables.provenance import bind_sources

AMOUNT=re.compile(r'^\(?\d{1,3}(?:[.,]\d{3})+\)?$')
UNIT=re.compile(r'^(VND|USD|VNĐ|ĐỒNG)$',re.I)


def _bbox(blocks):
    return [min(b.bbox[0] for b in blocks),min(b.bbox[1] for b in blocks),
            max(b.bbox[2] for b in blocks),max(b.bbox[3] for b in blocks)]


def reconstruct_small_image_tables(page):
    originals=[TextBlock.model_validate(b) for b in page.metadata.get('text_evidence',{}).get('items',[])]
    tables=[]
    for region in page.regions:
        if region.content_type.value not in ('IMAGE','IMAGE_TABLE'):
            continue
        frame=SimpleNamespace(bbox=region.bbox.to_list())
        blocks=[b for b in originals if len(b.bbox)==4 and b.text.strip()
                and b.source not in ('native','vector','docling')
                and not is_non_horizontal_line_candidate(b.recognition_provenance)
                and block_is_covered(b.bbox,[frame],.98)]
        amounts=sorted((b for b in blocks if AMOUNT.fullmatch(b.text.strip())),key=lambda b:b.bbox[0])
        if not 2<=len(amounts)<=4:
            continue
        centers=[(b.bbox[1]+b.bbox[3])/2 for b in amounts]
        if max(centers)-min(centers)>6:
            continue
        middle=sum(centers)/len(centers)
        data=[b for b in blocks if abs((b.bbox[1]+b.bbox[3])/2-middle)<=6]
        headers=[b for b in blocks if b not in data]
        if any(b.bbox[3]>min(a.bbox[1] for a in amounts)+1 for b in headers):
            continue
        labels=[b for b in data if b not in amounts and b.bbox[2]<amounts[0].bbox[0]]
        if not labels:
            continue
        assigned={};valid=True
        for b in blocks:
            cols=[i+1 for i,a in enumerate(amounts)
                  if max(b.bbox[0],a.bbox[0])<min(b.bbox[2],a.bbox[2])]
            if not cols and b.bbox[2]<amounts[0].bbox[0]:
                cols=[0]
            if len(cols)!=1:
                valid=False;break
            row=2 if b in data else (1 if UNIT.fullmatch(b.text.strip()) else 0)
            assigned.setdefault((row,cols[0]),[]).append(b)
        if not valid or any(not any(UNIT.fullmatch(b.text.strip()) for b in assigned.get((1,col),[]))
                            for col in range(1,len(amounts)+1)):
            continue
        extents=[_bbox([b for (row,c),items in assigned.items() if c==col for b in items])
                 for col in range(len(amounts)+1)]
        if any(a[2]>b[0] for a,b in zip(extents,extents[1:])):
            continue
        cells=[]
        for (row,col),items in sorted(assigned.items()):
            items=sorted(items,key=lambda b:(b.bbox[1],b.bbox[0]))
            cells.append(TableCell(row_idx=row,col_idx=col,text=' '.join(b.text.strip() for b in items),
                                   bbox=_bbox(items),column_header=row<2,confidence=None))
        table=TableStructure(table_id=f'p{page.page_num}_small_image_{len(tables)+1}',page=page.page_num,
            bbox=_bbox(blocks),num_rows=3,num_cols=len(amounts)+1,cells=cells,engine='deterministic_geometry',
            confidence=None,needs_review=True,provenance={'method':'financial-geometry-v1',
            'submethod':'single-row-image-unit-anchors-v1','source_region_id':region.region_id})
        if bind_sources(table,blocks,originals):
            tables.append(table)
    return tables

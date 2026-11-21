"""Run structure models in upright region views and invert cell coordinates."""
import copy

import pymupdf

from ocr_contracts.geometry import block_is_covered
from ocr_contracts.region_frame import RegionFrame
from ocr_structure.docling_client import DoclingStructureError


class RegionStructureExtractor:
    def __init__(self, extractor, plans):
        self.extractor,self.plans=extractor,list(plans)

    def __getattr__(self,name):
        return getattr(self.extractor,name)

    def extract_page(self,page,page_num):
        if not self.plans:return self.extractor.extract_page(page,page_num)
        tiled=all(p.get("method")=="paired-folio-gutter-v1" for p in self.plans)
        base=None if tiled else self.extractor.extract_page(page,page_num)
        region_tables,region_formulas,records,errors=[],[],[],[]
        completed_table_frames=[]
        metadata=copy.deepcopy(base["metadata"]) if base else None
        for index,plan in enumerate(self.plans):
            box=list(pymupdf.Rect(plan["bbox"]) & page.rect)
            frame=RegionFrame(tuple(box),plan["clockwise_degrees"])
            try:
                with pymupdf.open() as document:
                    view=document.new_page(width=frame.size[0],height=frame.size[1])
                    view.show_pdf_page(view.rect,page.parent,page.number,clip=pymupdf.Rect(box),
                                       rotate=-frame.clockwise_degrees)
                    result=self.extractor.extract_page(view,page_num)
            except DoclingStructureError as exc:
                if self.options.get("on_error")!="fallback":raise
                errors.append(f"Region {plan['id']}: {exc}")
                records.append({"id":plan["id"],"status":"FAILED_FALLBACK","frame":frame.to_dict()})
                continue
            if metadata is None:metadata=copy.deepcopy(result["metadata"])
            if result['tables']:
                completed_table_frames.append((plan['id'],frame))
            errors.extend(result["metadata"].get("errors",[]))
            for table in result["tables"]:
                table.table_id=f"p{page_num}_view{index+1}_{table.table_id}"
                table.bbox=frame.to_source_bbox(table.bbox)
                for cell in table.cells:
                    if cell.bbox:cell.bbox=frame.to_source_bbox(cell.bbox)
                original=table.provenance.get("table_orientation","rot_0")
                angle=(int(original.split("_")[-1])+frame.clockwise_degrees)%360
                table.provenance.update(table_orientation=f"rot_{angle}",
                                        region_structure_view={"id":plan["id"],"frame":frame.to_dict(),
                                                               "model_view_orientation":original,
                                                               "bbox_space":"pdf_display_points"})
                region_tables.append(table)
            for formula in result["formulas"]:
                formula.formula_id=f"p{page_num}_view{index+1}_{formula.formula_id}"
                formula.bbox=frame.to_source_bbox(formula.bbox)
                formula.provenance["region_structure_view"]={"id":plan["id"],"frame":frame.to_dict()}
                region_formulas.append(formula)
            records.append({"id":plan["id"],"status":"success","frame":frame.to_dict(),
                            "tables":len(result["tables"]),"formulas":len(result["formulas"])})
        if metadata is None:
            raise DoclingStructureError("All structure views failed: "+"; ".join(errors))
        tables=list(base["tables"]) if base else []
        formulas=list(base["formulas"]) if base else []
        # Replace only successfully reconstructed covered candidates. Outside
        # content and failed view candidates remain available for review.
        replaced=[]
        kept=[]
        for table in tables:
            owners=[region_id for region_id,frame in completed_table_frames
                    if block_is_covered(table.bbox,[frame],.98)]
            if owners or any(block_is_covered(table.bbox,[new],.8) for new in region_tables):
                replaced.append({'table_id':table.table_id,'bbox':list(table.bbox),
                    'region_ids':owners,'reason':'upright_region_structure_preferred',
                    'cells':[{'text':getattr(c,'text',''),'bbox':list(c.bbox),
                              'row_idx':getattr(c,'row_idx',None),'col_idx':getattr(c,'col_idx',None)}
                             for c in table.cells]})
            else:
                kept.append(table)
        tables=kept
        if replaced:
            metadata['superseded_primary_tables']=replaced
        formulas=[f for f in formulas if not any(block_is_covered(f.bbox,[new],.8) for new in region_formulas)]
        metadata.update(regional_views=records,status="success")
        if errors:metadata["errors"]=list(metadata.get("errors",[]))+errors
        return {"tables":tables+region_tables,"formulas":formulas+region_formulas,"metadata":metadata}

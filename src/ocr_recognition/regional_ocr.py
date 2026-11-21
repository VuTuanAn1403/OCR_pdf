"""Retry observed views on the same GPU and map every result to the source page."""
from contextlib import contextmanager
import copy

from ocr_contracts.geometry import LINE_ORIENTATION_METHOD, VERTICAL_LINE_ORIENTATION_METHOD
from ocr_contracts.region_frame import RegionFrame
from ocr_recognition.inference import predict_image_counted
from ocr_recognition.reading_views import plan_reading_views, rotate_view
from ocr_recognition.line_orientation_policy import numeric_reading
from ocr_text.numeric_guard import preserves_numeric_evidence


@contextmanager
def frame_orientation(engine, hint):
    marker=object();previous=getattr(engine,"frame_orientation_hint",marker)
    if hint is not None:engine.frame_orientation_hint=hint
    try:yield
    finally:
        if previous is marker:
            if hasattr(engine,"frame_orientation_hint"):del engine.frame_orientation_hint
        else:engine.frame_orientation_hint=previous


def _intersection(first,second):
    return max(0.,min(first[2],second[2])-max(first[0],second[0]))*max(0.,min(first[3],second[3])-max(first[1],second[1]))


def _map_provenance(value, frame, image_shape):
    if isinstance(value,list):
        for child in value:_map_provenance(child,frame,image_shape)
    elif isinstance(value,dict):
        for key,child in list(value.items()):
            if key in ("bbox","detector_bbox_pixels","crop_bbox_pixels","original_crop_bbox_pixels") and isinstance(child,list) and len(child)==4:
                value[key]=frame.to_source_bbox(child)
            elif key=="rotation_clockwise_degrees" and type(child) is int:
                value["view_rotation_clockwise_degrees"]=child
                value[key]=(child+frame.clockwise_degrees)%360
            elif key=="source_image_shape":
                value["view_source_image_shape"]=child;value[key]=list(image_shape)
            else:_map_provenance(child,frame,image_shape)


def _covered_area(box, readings):
    clips=[[max(box[0],r["bbox"][0]),max(box[1],r["bbox"][1]),min(box[2],r["bbox"][2]),min(box[3],r["bbox"][3])] for r in readings]
    clips=[b for b in clips if b[0]<b[2] and b[1]<b[3]]
    xs=sorted({b[i] for b in clips for i in (0,2)});area=0.
    for left,right in zip(xs,xs[1:]):
        ys=sorted((b[1],b[3]) for b in clips if b[0]<right and b[2]>left)
        end=-float("inf");length=0.
        for low,high in ys:
            length+=max(0.,high-max(low,end));end=max(end,high)
        area+=(right-left)*length
    return area


def retry_reading_regions(owner, image, baseline, directions, page_size):
    plans=plan_reading_views(image,baseline,page_size,directions)
    audit={"method":"regional-gpu-reading-v1","views":plans,"native_text_copied":False,
           "baseline_hypotheses":[],"status":"SKIPPED_NO_OBSERVED_VIEW"}
    if not plans:return baseline,audit
    replacements=[]
    for plan in plans:
        x0,y0,x1,y1=plan["pixel_bbox"]
        frame=RegionFrame(tuple(plan["pixel_bbox"]),plan["clockwise_degrees"])
        view=rotate_view(image[y0:y1,x0:x1],frame.clockwise_degrees)
        hint={"method":plan["method"],"region_id":plan["id"],"clockwise_degrees":0}
        with frame_orientation(owner.ocr_engine,hint):readings=predict_image_counted(owner,view)
        mapped=[]
        for original in readings:
            row=copy.deepcopy(original)
            local_box=row["bbox"]
            row["bbox"]=frame.to_source_bbox(local_box)
            if "raw_bbox" in row:row["raw_bbox"]=frame.to_source_bbox(row["raw_bbox"])
            provenance=row.setdefault("recognition_provenance",{})
            local_angle=provenance.get("rotation_clockwise_degrees",0)
            angle=(local_angle+frame.clockwise_degrees)%360
            _map_provenance(provenance,frame,image.shape)
            if frame.clockwise_degrees:
                provenance.update(method=VERTICAL_LINE_ORIENTATION_METHOD if angle in (90,270) else LINE_ORIENTATION_METHOD,
                                  rotation_clockwise_degrees=angle,selection="observed_region_view",
                                  bbox_space="original_image_pixels",detector_bbox_pixels=list(row["bbox"]))
                row["needs_review"]=True
                row["review_reason"]="; ".join(filter(None,[row.get("review_reason"),"regional_orientation_requires_review"]))
            provenance["region_view"]={"id":plan["id"],"method":plan["method"],"frame":frame.to_dict(),
                                       "view_detector_bbox_pixels":local_box,"view_input_shape":list(view.shape)}
            mapped.append(row)
        if not frame.clockwise_degrees:
            for index,row in enumerate(mapped):
                possible=[old for old in baseline if numeric_reading(old["text"])
                          and (old.get("confidence") or 0)>=.85
                          and _intersection(old["bbox"],plan["pixel_bbox"])>=.98*max(1.,(old["bbox"][2]-old["bbox"][0])*(old["bbox"][3]-old["bbox"][1]))
                          and _intersection(old["bbox"],row["bbox"])>=.65*max(1.,(old["bbox"][2]-old["bbox"][0])*(old["bbox"][3]-old["bbox"][1]))
                          and _intersection(old["bbox"],row["bbox"])>=.65*max(1.,(row["bbox"][2]-row["bbox"][0])*(row["bbox"][3]-row["bbox"][1]))]
                if len(possible)==1 and not preserves_numeric_evidence(possible[0]["text"],row["text"]):
                    kept=copy.deepcopy(possible[0]);kept["needs_review"]=True
                    kept["review_reason"]="regional_numeric_disagreement_baseline_kept"
                    kept.setdefault("recognition_provenance",{})["regional_numeric_alternative"]=row
                    kept["recognition_provenance"]["region_view"]={"id":plan["id"],"method":plan["method"],"frame":frame.to_dict()}
                    mapped[index]=kept
        plan["recognized_lines"]=len(mapped)
        replacements.append((plan,mapped))
    result=[]
    for row in baseline:
        box=row["bbox"];area=max(1e-9,(box[2]-box[0])*(box[3]-box[1]))
        owners=[(plan,readings) for plan,readings in replacements if _intersection(box,plan["pixel_bbox"])/area>=.98]
        if len(owners)!=1:
            result.append(row);continue
        plan,readings=owners[0]
        coverage=min(1.,_covered_area(box,readings)/area)
        same=any(new.get("text_raw",new["text"])==row.get("text_raw",row["text"])
                 and _intersection(box,new["bbox"])>0 for new in readings)
        superseded=bool(readings) and (coverage>=.5 or same)
        audit["baseline_hypotheses"].append({"view_id":plan["id"],"reading":row,
            "detector_coverage":coverage,"disposition":"SUPERSEDED_BY_REGIONAL_GPU_INFERENCE" if superseded else "KEPT_UNMATCHED_PRIMARY"})
        if not superseded:
            row=copy.deepcopy(row);row["needs_review"]=True
            row["review_reason"]="; ".join(filter(None,[row.get("review_reason"),"regional_ocr_source_unmatched"]))
            result.append(row)
    for _,readings in replacements:result.extend(readings)
    audit.update(status="REGIONAL_GPU_INFERENCE",selected_lines=len(result))
    return result,audit

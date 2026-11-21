"""Find rotated image-table regions from observed detector line geometry."""
from ocr_recognition.line_orientation_policy import language_support


def _covered(box, regions):
    area=max(1.,(box[2]-box[0])*(box[3]-box[1]))
    return any(max(0,min(box[2],r['pixel_bbox'][2])-max(box[0],r['pixel_bbox'][0]))
               *max(0,min(box[3],r['pixel_bbox'][3])-max(box[1],r['pixel_bbox'][1])) >= .9*area
               for r in regions)


def augment_observed_regions(results, plans, image_shape, directions):
    """Numeric confidence cannot decide 90/270; require readable text anchors.

    Native glyph directions may support an otherwise ambiguous cloud. They
    never supply OCR text. Existing complete native regions retain precedence.
    """
    height,width=image_shape[:2]
    rows=[]
    for index,row in enumerate(results):
        box=row.get('bbox',[])
        angle=row.get('recognition_provenance',{}).get('rotation_clockwise_degrees')
        if len(box)==4 and angle in (90,270) and box[3]-box[1] >= 1.5*(box[2]-box[0]) > 0:
            rows.append((index,row,angle))
    groups=[]
    for item in sorted(rows,key=lambda v:v[1]['bbox'][0]):
        if not groups or item[1]['bbox'][0] > max(v[1]['bbox'][2] for v in groups[-1]) + .07*width:
            groups.append([])
        groups[-1].append(item)
    selected=list(plans)
    for number,group in enumerate(groups,1):
        if len(group)<8 or sum(_covered(row['bbox'],plans) for _,row,_ in group)>=.9*len(group):
            continue
        votes={angle:sum(1 for _,row,a in group if a==angle
                        and sum(c.isalpha() for c in row.get('text',''))>=5
                        and language_support(row['text'])>=.7) for angle in (90,270)}
        angle=max(votes,key=votes.get)
        total=sum(votes.values())
        counts=directions.get('glyph_counts',{})
        native={a:counts.get(a,counts.get(str(a),0)) for a in (90,270)}
        native_angle=max(native,key=native.get)
        native_clear=native[native_angle]>=40 and native[native_angle]>=.95*max(1,sum(native.values()))
        if not (votes[angle]>=2 and votes[angle]>=.8*max(1,total)):
            if not native_clear:
                continue
            angle=native_angle
        elif native_clear and native_angle!=angle:
            continue
        boxes=[row['bbox'] for _,row,_ in group]
        padding=max(4,int(.006*min(width,height)))
        box=[max(0,int(min(b[0] for b in boxes))-padding),max(0,int(min(b[1] for b in boxes))-padding),
             min(width,int(max(b[2] for b in boxes))+padding),min(height,int(max(b[3] for b in boxes))+padding)]
        if (box[2]-box[0])*(box[3]-box[1])<.01*width*height:
            continue
        # A narrow native strip is a partial orientation anchor, not a second view.
        absorbed=[p for p in selected if p['clockwise_degrees']==angle
                  and max(box[0],p['pixel_bbox'][0])<min(box[2],p['pixel_bbox'][2])
                  and max(box[1],p['pixel_bbox'][1])<min(box[3],p['pixel_bbox'][3])]
        for plan in absorbed:
            box=[min(box[0],plan['pixel_bbox'][0]),min(box[1],plan['pixel_bbox'][1]),
                 max(box[2],plan['pixel_bbox'][2]),max(box[3],plan['pixel_bbox'][3])]
            selected.remove(plan)
        selected.append(dict(id=f'ocr_direction_region_{number}',method='observed-ocr-orientation-cloud-v1',
            pixel_bbox=box,clockwise_degrees=angle,source_detection_indices=[i for i,_,_ in group],
            language_votes=votes,native_direction_support=native_clear,needs_review=True))
    return selected

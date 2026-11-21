"""Observed region plans for quarter turns and independent facing pages."""
import re

import cv2
import numpy as np


def plan_reading_views(image, results, page_size, directions):
    height,width=image.shape[:2]
    page_width,page_height=page_size
    plans=[]
    for item in directions.get("regions", []):
        x0,y0,x1,y1=item["bbox"]
        box=[max(0,int(np.floor(x0*width/page_width))),max(0,int(np.floor(y0*height/page_height))),
             min(width,int(np.ceil(x1*width/page_width))),min(height,int(np.ceil(y1*height/page_height)))]
        plans.append({**item,"pixel_bbox":box})
    if not plans and width > 1.25*height:
        folios=[[] for _ in range(2)]
        labels=[0,0]
        for row in results:
            box=row.get("bbox",[]);text=row.get("text","").strip()
            if len(box)!=4:continue
            x=(box[0]+box[2])/2;y=(box[1]+box[3])/2;side=int(x>=width/2)
            if y>.95*height and (x<.12*width or x>.88*width) and re.fullmatch(r"\d{1,4}",text):
                folios[side].append(text)
            if sum(c.isalpha() for c in text)>=5:labels[side]+=1
        if all(folios) and folios[0]!=folios[1] and min(labels)>=8:
            gray=cv2.cvtColor(image[int(.1*height):int(.9*height)],cv2.COLOR_BGR2GRAY)
            ink=(gray<220).mean(axis=0)
            low,high=int(.43*width),int(.57*width)
            bands=[];start=None
            for x in range(low,high+1):
                blank=x<high and ink[x]<.008
                if blank and start is None:start=x
                if not blank and start is not None:
                    if x-start>=max(8,.006*width):bands.append((start,x))
                    start=None
            if bands:
                left,right=min(bands,key=lambda b:abs((b[0]+b[1])/2-width/2))
                cut=(left+right)//2
                plans=[{"id":f"facing_page_{i+1}","pixel_bbox":box,"clockwise_degrees":0,
                        "method":"paired-folio-gutter-v1"}
                       for i,box in enumerate(([0,0,cut,height],[cut,0,width,height]))]
    for plan in plans:
        x0,y0,x1,y1=plan["pixel_bbox"]
        plan["bbox"]=[x0*page_width/width,y0*page_height/height,x1*page_width/width,y1*page_height/height]
        plan["coordinate_space"]="pdf_display_points"
    return plans


def rotate_view(image, clockwise_degrees):
    code={90:cv2.ROTATE_90_CLOCKWISE,180:cv2.ROTATE_180,270:cv2.ROTATE_90_COUNTERCLOCKWISE}
    return cv2.rotate(image,code[clockwise_degrees]) if clockwise_degrees else image.copy()

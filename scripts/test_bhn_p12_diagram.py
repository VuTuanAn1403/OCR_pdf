import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pymupdf
import cv2
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.ocr.infrastructure.ocr.rapidocr_engine import RapidOCREngine

def test_left_subpage():
    doc = pymupdf.open("inputs/BHN_Baocaothuongnien_2022.pdf")
    page = doc[11]
    
    rect_left = pymupdf.Rect(0, 0, 595.28, 841.89)
    pix = page.get_pixmap(clip=rect_left, dpi=200)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, pix.n))
    img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR if pix.n >= 3 else cv2.COLOR_GRAY2BGR)
        
    ocr = RapidOCREngine()
    results = ocr.predict_image(img_bgr)
    results = sorted(results, key=lambda x: (x['bbox'][1], x['bbox'][0]))
    for r in results:
        bb = r['bbox']
        print(f"Y={bb[1]:<4.0f} X={bb[0]:<4.0f} text={r['text']}")

if __name__ == "__main__":
    test_left_subpage()

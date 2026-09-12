from typing import Optional, List, Tuple
import pymupdf
import numpy as np
import cv2

class PDFRenderer:
    """
    Renders PDF pages and regional crops into OpenCV/numpy BGR images.
    """
    def __init__(self, doc: pymupdf.Document):
        self.doc = doc

    def render_page(self, page_num: int, dpi: int = 180) -> np.ndarray:
        """
        Renders a 1-indexed page at the specified DPI. Returns a BGR numpy ndarray.
        """
        page = self.doc[page_num - 1]
        pix = page.get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.h, pix.w, pix.n))
        if pix.n == 4:
            return cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        elif pix.n == 3:
            return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        elif pix.n == 1:
            return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        return img

    def render_region_crop(
        self,
        page_num: int,
        bbox: List[float],
        dpi: int = 300,
        margin: float = 5.0
    ) -> Tuple[np.ndarray, List[float]]:
        """
        Renders a specific bounding box [x0, y0, x1, y1] on a page at an upscaled DPI (e.g. 300).
        Includes an optional margin in points.
        Returns (cropped_image, actual_rect_bbox).
        """
        page = self.doc[page_num - 1]
        page_rect = page.rect

        x0 = max(0.0, bbox[0] - margin)
        y0 = max(0.0, bbox[1] - margin)
        x1 = min(page_rect.width, bbox[2] + margin)
        y1 = min(page_rect.height, bbox[3] + margin)

        clip_rect = pymupdf.Rect(x0, y0, x1, y1)
        pix = page.get_pixmap(dpi=dpi, clip=clip_rect)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.h, pix.w, pix.n))

        if pix.n == 4:
            bgr = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        elif pix.n == 3:
            bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        elif pix.n == 1:
            bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        else:
            bgr = img

        return bgr, [x0, y0, x1, y1]

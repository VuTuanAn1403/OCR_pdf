"""
V3.1.2 Phase B – Region Cropper

Provides precise regional cropping and coordinate mapping for region-level
extraction and OCR, fulfilling V3.1.2 spec §2, §5, §15, §17:
- Crop arbitrary regions from PDF pages at configurable DPI.
- Map OCR bounding boxes from cropped pixel space back to PDF page point space.
- Detect decorative/background images to avoid redundant OCR.
- Detect overlaps between native text blocks and OCR blocks to prevent duplicates.
"""

from typing import List, Tuple, Optional
import pymupdf
import numpy as np
import cv2

from src.ocr.domain.models.region import BoundingBox


class RegionCropper:
    """
    Renders and crops specific regions of a PDF page, and maps coordinates
    between cropped pixel space and PDF page point space.
    """

    @staticmethod
    def crop_page_region(
        doc_page: pymupdf.Page,
        bbox: BoundingBox,
        dpi: int = 180,
        margin: float = 2.0,
    ) -> Tuple[np.ndarray, List[float]]:
        """
        Crops a rectangular region from a PyMuPDF Page at the specified DPI.

        Args:
            doc_page: pymupdf.Page object.
            bbox: BoundingBox in PDF point coordinates.
            dpi: Resolution for rendering (default 180).
            margin: Extra padding in points added around bbox (default 2.0).

        Returns:
            (cropped_bgr, actual_crop_rect)
            - cropped_bgr: numpy ndarray in BGR format suitable for OpenCV/OCR.
            - actual_crop_rect: [x0, y0, x1, y1] in PDF point coordinates.
        """
        page_rect = doc_page.rect

        x0 = max(0.0, bbox.x0 - margin)
        y0 = max(0.0, bbox.y0 - margin)
        x1 = min(float(page_rect.width), bbox.x1 + margin)
        y1 = min(float(page_rect.height), bbox.y1 + margin)

        if x1 <= x0 or y1 <= y0:
            return np.zeros((0, 0, 3), dtype=np.uint8), [0.0, 0.0, 0.0, 0.0]

        actual_rect = [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)]
        clip_rect = pymupdf.Rect(x0, y0, x1, y1)

        pix = doc_page.get_pixmap(dpi=dpi, clip=clip_rect)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.h, pix.w, pix.n))

        if pix.n == 4:
            bgr = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        elif pix.n == 3:
            bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        elif pix.n == 1:
            bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        else:
            bgr = img

        return bgr, actual_rect

    @staticmethod
    def map_crop_bbox_to_page(
        crop_bbox: List[float],
        crop_rect: List[float],
        crop_img_w: int,
        crop_img_h: int,
    ) -> List[float]:
        """
        Converts a bounding box from cropped image pixel coordinates back to
        PDF page point coordinates.

        Args:
            crop_bbox: [x0, y0, x1, y1] in crop image pixels.
            crop_rect: [x0, y0, x1, y1] of the crop in PDF point coordinates.
            crop_img_w: Width of the cropped image in pixels.
            crop_img_h: Height of the cropped image in pixels.

        Returns:
            [page_x0, page_y0, page_x1, page_y1] in PDF points.
        """
        if crop_img_w <= 0 or crop_img_h <= 0:
            return list(crop_rect)

        scale_x = (crop_rect[2] - crop_rect[0]) / max(1.0, float(crop_img_w))
        scale_y = (crop_rect[3] - crop_rect[1]) / max(1.0, float(crop_img_h))

        return [
            round(crop_rect[0] + crop_bbox[0] * scale_x, 2),
            round(crop_rect[1] + crop_bbox[1] * scale_y, 2),
            round(crop_rect[0] + crop_bbox[2] * scale_x, 2),
            round(crop_rect[1] + crop_bbox[3] * scale_y, 2),
        ]

    @staticmethod
    def compute_iou(box_a: List[float], box_b: List[float]) -> float:
        """
        Computes Intersection-over-Union (IoU) of two bounding boxes [x0, y0, x1, y1].
        """
        x_left = max(box_a[0], box_b[0])
        y_top = max(box_a[1], box_b[1])
        x_right = min(box_a[2], box_b[2])
        y_bottom = min(box_a[3], box_b[3])

        if x_right <= x_left or y_bottom <= y_top:
            return 0.0

        intersection_area = (x_right - x_left) * (y_bottom - y_top)
        area_a = max(0.0, box_a[2] - box_a[0]) * max(0.0, box_a[3] - box_a[1])
        area_b = max(0.0, box_b[2] - box_b[0]) * max(0.0, box_b[3] - box_b[1])

        union_area = area_a + area_b - intersection_area
        if union_area <= 0.0:
            return 0.0

        return intersection_area / union_area

    @staticmethod
    def compute_intersection_over_min(box_a: List[float], box_b: List[float]) -> float:
        """
        Computes intersection area divided by the minimum area of the two boxes.
        Useful to detect if one box is substantially contained inside another.
        """
        x_left = max(box_a[0], box_b[0])
        y_top = max(box_a[1], box_b[1])
        x_right = min(box_a[2], box_b[2])
        y_bottom = min(box_a[3], box_b[3])

        if x_right <= x_left or y_bottom <= y_top:
            return 0.0

        intersection_area = (x_right - x_left) * (y_bottom - y_top)
        area_a = max(0.0, box_a[2] - box_a[0]) * max(0.0, box_a[3] - box_a[1])
        area_b = max(0.0, box_b[2] - box_b[0]) * max(0.0, box_b[3] - box_b[1])

        min_area = min(area_a, area_b)
        if min_area <= 0.0:
            return 0.0

        return intersection_area / min_area

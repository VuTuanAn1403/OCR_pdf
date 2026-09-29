from typing import Dict, Any, List
import pymupdf
from src.ocr.infrastructure.postprocessing.unicode_normalizer import UnicodeNormalizer

class PDFInspector:
    """
    Inspects PDF pages to extract structural metadata, geometry, image coverage, and native text stats.
    """
    def __init__(self, doc: pymupdf.Document):
        self.doc = doc

    def inspect_page(self, page_num: int) -> Dict[str, Any]:
        """
        Inspects a 1-indexed page number and returns comprehensive metadata.
        """
        page = self.doc[page_num - 1]
        rect = page.rect
        page_area = max(1.0, rect.width * rect.height)

        # Native text stats. Some owner-protected PDFs expose a page object but
        # reject text extraction; rendering remains available and is the OCR
        # fallback path.
        native_extraction_failed = False
        try:
            raw_text = page.get_text("text")
        except Exception:
            raw_text = ""
            native_extraction_failed = True
        char_count = len(raw_text.strip())
        unicode_report = UnicodeNormalizer.analyze(raw_text)
        try:
            blocks = page.get_text("blocks")  # (x0, y0, x1, y1, text, block_no, block_type)
        except Exception:
            blocks = []
            native_extraction_failed = True
        text_blocks = [b for b in blocks if b[4].strip() and b[6] == 0]

        # Calculate text coverage
        text_area = sum(max(0.0, (b[2] - b[0]) * (b[3] - b[1])) for b in text_blocks)
        text_density = text_area / page_area

        # Image stats
        images = page.get_images(full=True)
        image_count = len(images)
        image_area = 0.0
        is_full_page_image = False

        image_rects = []
        for img_info in images:
            xref = img_info[0]
            try:
                img_bbox_list = page.get_image_rects(xref)
                for r in img_bbox_list:
                    image_rects.append([r.x0, r.y0, r.x1, r.y1])
                    area = max(0.0, (r.x1 - r.x0) * (r.y1 - r.y0))
                    image_area += area
                    if area / page_area >= 0.80:
                        is_full_page_image = True
            except Exception:
                pass

        image_coverage_ratio = min(1.0, image_area / page_area)
        if image_count > 0 and image_coverage_ratio == 0.0:
            # Fallback if get_image_rects returned empty
            image_coverage_ratio = 0.85
            is_full_page_image = True

        # Font information
        fonts = []
        try:
            fonts = page.get_fonts()
        except Exception:
            pass

        encrypted = False
        permissions = None
        copy_restricted = False
        try:
            encrypted = bool(self.doc.is_encrypted)
            permissions = int(self.doc.permissions)
            copy_permission = getattr(pymupdf, "PDF_PERM_COPY", None)
            if copy_permission is not None and permissions >= 0:
                copy_restricted = not bool(permissions & copy_permission)
        except Exception:
            pass

        # Cheap geometry signal used to avoid expensive table probing on
        # ordinary two-column pages.
        column_count = 1
        block_centers = []
        for block in text_blocks:
            center = (block[0] + block[2]) / 2.0
            block_centers.append(center)
        if len(block_centers) >= 4:
            left = sum(x < rect.width * 0.48 for x in block_centers)
            right = sum(x > rect.width * 0.52 for x in block_centers)
            if left >= 2 and right >= 2:
                column_count = 2

        return {
            "page_num": page_num,
            "width": round(rect.width, 2),
            "height": round(rect.height, 2),
            "page_area": round(page_area, 2),
            "char_count": char_count,
            "raw_text": raw_text,
            "text_blocks_count": len(text_blocks),
            "text_density": round(text_density, 4),
            "image_count": image_count,
            "image_coverage_ratio": round(image_coverage_ratio, 4),
            "is_full_page_image": is_full_page_image,
            "image_rects": image_rects,
            "fonts_count": len(fonts),
            "has_images": image_count > 0,
            "native_extraction_failed": native_extraction_failed,
            "unicode_corruption_detected": bool(unicode_report.get("is_corrupted", False)),
            "unicode_corruption_reasons": unicode_report.get("reasons", []),
            "is_encrypted": encrypted,
            "permissions": permissions,
            "copy_restricted": copy_restricted,
            "column_count": column_count
        }

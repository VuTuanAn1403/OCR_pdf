"""
V3.1.2 Phase A – Page Content Analyzer

Decomposes a PDF page into typed regions (TEXT, IMAGE, IMAGE_TABLE,
VECTOR, FIGURE, UNKNOWN) using metadata already collected by PDFInspector.

Design rules:
- No region is silently dropped.
- Regions that cannot be classified → UNKNOWN (preserved).
- This analyzer does NOT perform OCR or extraction; it only classifies.
"""

from typing import Dict, Any, List, Optional, Tuple
import pymupdf
import cv2
import numpy as np

from src.ocr.domain.models.region import Region, BoundingBox, ContentType


# Minimum image area (in points²) to be considered meaningful.
# Prevents logo/icon noise from creating regions.
MIN_IMAGE_AREA_PT2 = 2500.0  # ~50x50pt

# Ratio of image area to page area that indicates a full-page scan
FULL_PAGE_IMAGE_RATIO = 0.75

# If an image region overlaps significantly with text blocks, it may
# be a background/watermark rather than informative content.
DECORATIVE_TEXT_OVERLAP_RATIO = 0.80


class PageContentAnalyzer:
    """
    Analyzes a PDF page and produces a list of typed Regions.

    Uses the metadata dict from PDFInspector and the raw pymupdf.Page
    to decompose the page into its content composition.
    """

    def analyze(
        self,
        meta: Dict[str, Any],
        doc_page: pymupdf.Page,
    ) -> List[Region]:
        """
        Returns a list of Region objects describing the content composition
        of the page.  This is a *classification-only* step — no text
        extraction or OCR happens here.
        """
        page_num = meta["page_num"]
        page_area = meta.get("page_area", 1.0)
        image_rects = meta.get("image_rects", [])
        char_count = meta.get("char_count", 0)
        native_extraction_failed = meta.get("native_extraction_failed", False)

        regions: List[Region] = []
        region_counter = 0

        # ── 1. Collect native text block regions ──────────────────────
        text_block_bboxes: List[BoundingBox] = []
        if not native_extraction_failed:
            try:
                raw_blocks = doc_page.get_text("blocks")
                for b in raw_blocks:
                    if b[6] == 0 and b[4].strip():  # type 0 = text, non-empty
                        bb = BoundingBox(
                            x0=round(b[0], 2),
                            y0=round(b[1], 2),
                            x1=round(b[2], 2),
                            y1=round(b[3], 2),
                        )
                        if bb.area > 0:
                            text_block_bboxes.append(bb)
                            region_counter += 1
                            regions.append(Region(
                                region_id=f"p{page_num}_r{region_counter}",
                                bbox=bb,
                                content_type=ContentType.TEXT,
                                source="native",
                                confidence=1.0,
                                reading_order=region_counter,
                            ))
            except Exception:
                pass

        # ── 2. Collect image regions ──────────────────────────────────
        image_bboxes: List[BoundingBox] = []
        for rect in image_rects:
            if len(rect) < 4:
                continue
            bb = BoundingBox(
                x0=round(rect[0], 2),
                y0=round(rect[1], 2),
                x1=round(rect[2], 2),
                y1=round(rect[3], 2),
            )
            if bb.area < MIN_IMAGE_AREA_PT2:
                continue  # too small — likely icon/logo

            image_bboxes.append(bb)

            # Classify the image region
            content_type = self._classify_image_region(
                bb, text_block_bboxes, page_area
            )

            region_counter += 1
            regions.append(Region(
                region_id=f"p{page_num}_r{region_counter}",
                bbox=bb,
                content_type=content_type,
                source="image",
                confidence=0.85,
                reading_order=region_counter,
            ))

        # ── 3. Check for vector/drawing regions (Pillar 2) ───────────
        drawing_regions: List[Region] = []
        try:
            drawings = doc_page.get_drawings()
            if drawings:
                drawing_regions = self._cluster_drawings(
                    drawings, page_num, page_area, text_block_bboxes, image_bboxes
                )
                for dr in drawing_regions:
                    region_counter += 1
                    dr.region_id = f"p{page_num}_r{region_counter}"
                    dr.reading_order = region_counter
                    regions.append(dr)
        except Exception:
            pass

        # ── 4. Two-Up Evidence (Pillar 1: Subpage Isolation) ──────────
        # Check both raster images AND substantial vector/diagram regions
        visual_bboxes = list(image_bboxes)
        page_w = float(meta.get("width", 0.0))
        for dr in drawing_regions:
            if dr.bbox.area >= page_area * 0.12 and dr.bbox.width >= page_w * 0.25:
                visual_bboxes.append(dr.bbox)

        if self._detect_adjacent_image_two_up(visual_bboxes, meta):
            meta["layout_type"] = "TWO_UP"
            meta["two_up_subtype"] = "TU-02"
            meta["layout_confidence"] = 0.94
            meta["two_up_split_x"] = round(page_w / 2.0, 2)

        mixed_subtype = self._detect_mixed_source_two_up(text_block_bboxes, visual_bboxes, meta)
        if mixed_subtype:
            meta["layout_type"] = "TWO_UP"
            meta["two_up_subtype"] = mixed_subtype
            meta["layout_confidence"] = 0.92
            meta["two_up_split_x"] = round(page_w / 2.0, 2)

        # Native Two-Up evidence (narrow text blocks separated by gutter)
        two_up = self._detect_native_two_up(text_block_bboxes, meta)
        if two_up:
            text_regions = [r for r in regions if r.content_type == ContentType.TEXT]
            other_regions = [r for r in regions if r.content_type != ContentType.TEXT]
            regions = two_up + other_regions
            meta["layout_type"] = "TWO_UP"
            meta["two_up_subtype"] = "TU-01"
            meta["layout_confidence"] = 0.90
            meta["two_up_split_x"] = round(page_w / 2.0, 2)

        # ── 5. If page has no regions at all → UNKNOWN ────────────────
        if not regions:
            page_rect = doc_page.rect
            region_counter += 1
            regions.append(Region(
                region_id=f"p{page_num}_r{region_counter}",
                bbox=BoundingBox(
                    x0=0.0, y0=0.0,
                    x1=round(page_rect.width, 2),
                    y1=round(page_rect.height, 2),
                ),
                content_type=ContentType.UNKNOWN,
                source="unknown",
                confidence=0.0,
                reading_order=1,
            ))

        # ── 6. Sort by reading order (top-to-bottom, left-to-right) ──
        regions.sort(key=lambda r: (r.bbox.y0, r.bbox.x0))
        for idx, r in enumerate(regions):
            r.reading_order = idx + 1

        return regions

    def refine_with_rendered_image(
        self,
        meta: Dict[str, Any],
        regions: List[Region],
        image_bgr: Optional[np.ndarray],
    ) -> List[Region]:
        """Split a rendered landscape scan when a real gutter is present.

        This is evidence-based: a wide page alone is not enough. The method
        requires a low-ink vertical gutter and meaningful content on both sides
        (or all three segments). It preserves the original region if evidence
        is insufficient.
        """
        if image_bgr is None or image_bgr.size == 0:
            return regions
        if meta.get("layout_type") == "TWO_UP":
            return regions
        page_area = max(1.0, float(meta.get("page_area", 1.0)))
        has_specific_regions = any(
            r.content_type in (ContentType.TEXT, ContentType.IMAGE_TABLE, ContentType.TABLE, ContentType.VECTOR)
            and r.bbox.area < page_area * 0.80
            and not (
                r.content_type == ContentType.TEXT
                and (r.bbox.y1 <= float(meta.get("height", 1.0)) * 0.12
                     or r.bbox.y0 >= float(meta.get("height", 1.0)) * 0.88
                     or r.bbox.width >= float(meta.get("width", 1.0)) * 0.75)
            )
            for r in regions
        )
        if has_specific_regions:
            return regions
        height, width = image_bgr.shape[:2]
        if width < height * 1.15:
            return regions

        cuts = self._find_vertical_gutters(image_bgr)
        if not cuts:
            return regions

        boundaries = [0] + cuts + [width]
        segments = []
        page_w = float(meta.get("width", 1.0))
        page_h = float(meta.get("height", 1.0))
        for idx in range(len(boundaries) - 1):
            px0, px1 = boundaries[idx], boundaries[idx + 1]
            if px1 - px0 < width * 0.18:
                return regions
            x0 = round(px0 * page_w / width, 2)
            x1 = round(px1 * page_w / width, 2)
            segments.append(Region(
                region_id=f"p{meta['page_num']}_r{idx + 1}",
                bbox=BoundingBox(x0=x0, y0=0.0, x1=x1, y1=page_h),
                content_type=ContentType.IMAGE,
                source="image",
                confidence=0.78,
                region_type="two_up" if len(boundaries) == 3 else "multi_region",
                orientation_status="UNKNOWN",
                orientation_confidence=0.0,
                reading_order=idx + 1,
            ))

        meta["layout_type"] = "TWO_UP" if len(segments) == 2 else "MULTI_REGION"
        meta["layout_confidence"] = 0.78
        meta["layout_gutters_px"] = cuts
        return segments

    @staticmethod
    def _detect_native_two_up(
        text_bboxes: List[BoundingBox],
        meta: Dict[str, Any],
    ) -> List[Region]:
        if len(text_bboxes) < 4:
            return []
        page_w = float(meta.get("width", 0.0))
        page_h = float(meta.get("height", 0.0))
        if page_w <= 0 or page_h <= 0:
            return []

        # Full-width lines are evidence against Two-Up segmentation.
        if any(bb.width > page_w * 0.68 for bb in text_bboxes):
            return []
        left = [bb for bb in text_bboxes if bb.x1 <= page_w * 0.48]
        right = [bb for bb in text_bboxes if bb.x0 >= page_w * 0.52]
        if len(left) < 2 or len(right) < 2:
            return []

        left_box = BoundingBox(
            x0=min(bb.x0 for bb in left), y0=min(bb.y0 for bb in left),
            x1=max(bb.x1 for bb in left), y1=max(bb.y1 for bb in left),
        )
        right_box = BoundingBox(
            x0=min(bb.x0 for bb in right), y0=min(bb.y0 for bb in right),
            x1=max(bb.x1 for bb in right), y1=max(bb.y1 for bb in right),
        )
        return [
            Region(
                region_id=f"p{meta['page_num']}_r1",
                bbox=left_box,
                content_type=ContentType.TEXT,
                source="native",
                confidence=0.90,
                region_type="two_up",
                reading_order=1,
            ),
            Region(
                region_id=f"p{meta['page_num']}_r2",
                bbox=right_box,
                content_type=ContentType.TEXT,
                source="native",
                confidence=0.90,
                region_type="two_up",
                reading_order=2,
            ),
        ]

    @staticmethod
    def _detect_adjacent_image_two_up(
        image_bboxes: List[BoundingBox],
        meta: Dict[str, Any],
    ) -> bool:
        """Recognize two substantial side-by-side page images (TU-02)."""
        if len(image_bboxes) < 2:
            return False
        page_area = max(1.0, float(meta.get("page_area", 1.0)))
        page_w = float(meta.get("width", 0.0))
        page_h = float(meta.get("height", 0.0))
        if page_w <= 0 or page_h <= 0 or page_w < page_h * 1.15:
            return False
        candidates = [b for b in image_bboxes if b.area >= page_area * 0.18]
        if len(candidates) < 2:
            return False
        # Select the two largest candidates
        candidates.sort(key=lambda b: b.area, reverse=True)
        pair = candidates[:2]
        pair.sort(key=lambda b: b.x0)
        left, right = pair
        same_band = abs(left.y0 - right.y0) <= page_h * 0.12 and abs(left.y1 - right.y1) <= page_h * 0.12
        adjacent = (right.x0 - left.x1) <= page_w * 0.12 and (right.x0 >= left.x0)
        substantial = left.width >= page_w * 0.30 and right.width >= page_w * 0.30
        return bool(same_band and adjacent and substantial)

    @staticmethod
    def _detect_mixed_source_two_up(
        text_bboxes: List[BoundingBox],
        image_bboxes: List[BoundingBox],
        meta: Dict[str, Any],
    ) -> Optional[str]:
        """
        Recognize Two-Up pages combining one Native Text half and one Scanned Image half:
        - TU-03: Left is Native Text (portrait), Right is Scanned Image (landscape table)
        - TU-04: Left is Scanned Image (landscape table), Right is Native Text (portrait)
        """
        page_w = float(meta.get("width", 0.0))
        page_h = float(meta.get("height", 0.0))
        page_area = max(1.0, float(meta.get("page_area", 1.0)))
        if page_w <= 0 or page_h <= 0 or page_w < page_h * 1.15:
            return None

        # Exclude header and footer bands from text blocks
        header_y = page_h * 0.12
        footer_y = page_h * 0.88
        body_texts = [
            bb for bb in text_bboxes
            if bb.y1 > header_y and bb.y0 < footer_y
        ]

        left_texts = [bb for bb in body_texts if bb.x1 <= page_w * 0.52]
        right_texts = [bb for bb in body_texts if bb.x0 >= page_w * 0.48]

        # Substantial image candidates (area >= 18% of page)
        substantial_images = [
            b for b in image_bboxes
            if b.area >= page_area * 0.18 and b.width >= page_w * 0.30
        ]
        left_images = [b for b in substantial_images if b.x1 <= page_w * 0.58]
        right_images = [b for b in substantial_images if b.x0 >= page_w * 0.42]

        # Case TU-03: Left has native text (>= 2 blocks), Right has substantial image
        if len(left_texts) >= 2 and len(right_images) >= 1 and len(left_images) == 0:
            return "TU-03"

        # Case TU-04: Left has substantial image, Right has native text (>= 2 blocks)
        if len(left_images) >= 1 and len(right_texts) >= 2 and len(right_images) == 0:
            return "TU-04"

        return None

    @staticmethod
    def _find_vertical_gutters(image_bgr: np.ndarray) -> List[int]:
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape[:2]
        y0, y1 = int(height * 0.04), int(height * 0.96)
        ink = (gray[y0:y1] < 245).mean(axis=0)
        if len(ink) < 20:
            return []

        smooth = np.convolve(ink, np.ones(9) / 9.0, mode="same")
        candidates: List[Tuple[float, int]] = []
        for x in range(int(width * 0.18), int(width * 0.82)):
            if smooth[x] > 0.015:
                continue
            radius = max(3, int(width * 0.006))
            lo, hi = max(0, x - radius), min(width, x + radius + 1)
            if float(smooth[lo:hi].mean()) <= 0.02:
                candidates.append((float(smooth[x]), x))

        # Collapse adjacent whitespace columns into one gutter center.
        groups: List[List[Tuple[float, int]]] = []
        for item in candidates:
            if not groups or item[1] - groups[-1][-1][1] > max(4, int(width * 0.01)):
                groups.append([item])
            else:
                groups[-1].append(item)

        gutters = []
        for group in groups:
            center = int(round(sum(x for _, x in group) / len(group)))
            left_density = float(ink[:center].mean())
            right_density = float(ink[center + 1:].mean())
            if left_density >= 0.01 and right_density >= 0.01:
                gutters.append(center)

        # Keep at most two strongest gutters, ordered left-to-right.
        return sorted(gutters, key=lambda x: float(smooth[x]))[:2]

    def _classify_image_region(
        self,
        img_bbox: BoundingBox,
        text_bboxes: List[BoundingBox],
        page_area: float,
    ) -> ContentType:
        """
        Classify an image region based on its geometry and overlap with text.

        Returns IMAGE_TABLE if the image looks like a table scan,
        FIGURE if it appears informative, or IMAGE as default.
        """
        ratio_of_page = img_bbox.area / max(1.0, page_area)

        # Full-page scan image
        if ratio_of_page >= FULL_PAGE_IMAGE_RATIO:
            return ContentType.IMAGE

        # Check overlap with text blocks — highly overlapping images are
        # likely background/watermark, but we still preserve them.
        text_overlap_count = sum(
            1 for tb in text_bboxes
            if img_bbox.overlap_ratio(tb) > DECORATIVE_TEXT_OVERLAP_RATIO
        )
        if text_overlap_count > 3:
            # Image is behind many text blocks → probably decorative
            return ContentType.IMAGE

        # Medium-sized images with table-like aspect ratio
        aspect = img_bbox.width / max(1.0, img_bbox.height)
        if 0.5 < aspect < 3.0 and ratio_of_page > 0.05:
            # Could be an image table — flag for future analysis
            # Phase C will refine this with OCR-based table detection
            if ratio_of_page > 0.15 and aspect > 0.8:
                return ContentType.IMAGE_TABLE

        return ContentType.IMAGE

    def _cluster_drawings(
        self,
        drawings: list,
        page_num: int,
        page_area: float,
        text_bboxes: List[BoundingBox],
        image_bboxes: List[BoundingBox],
    ) -> List[Region]:
        """
        Cluster drawing primitives (lines, rects, curves) into VECTOR regions.

        Drawings that largely overlap existing text or image regions are skipped
        to avoid duplication (e.g. table borders already captured by find_tables).
        """
        if not drawings:
            return []

        # Collect all drawing bboxes
        draw_bboxes: List[BoundingBox] = []
        for d in drawings:
            rect = d.get("rect")
            if rect is None:
                continue
            bb = BoundingBox(
                x0=round(rect.x0, 2),
                y0=round(rect.y0, 2),
                x1=round(rect.x1, 2),
                y1=round(rect.y1, 2),
            )
            if bb.area > MIN_IMAGE_AREA_PT2:
                draw_bboxes.append(bb)

        if not draw_bboxes:
            return []

        # Merge overlapping drawing bboxes into clusters
        clusters = self._merge_overlapping_bboxes(draw_bboxes)

        regions: List[Region] = []
        for cluster_bb in clusters:
            # Skip if cluster overlaps significantly with existing text or image
            skip = False
            for tb in text_bboxes:
                if cluster_bb.overlap_ratio(tb) > 0.7:
                    skip = True
                    break
            if skip:
                continue
            for ib in image_bboxes:
                if cluster_bb.overlap_ratio(ib) > 0.7:
                    skip = True
                    break
            if skip:
                continue

            ratio_of_page = cluster_bb.area / max(1.0, page_area)
            if ratio_of_page < 0.02:
                continue  # too small to be meaningful

            c_type = ContentType.DIAGRAM if (len(drawings) >= 15 and ratio_of_page >= 0.12) else ContentType.VECTOR
            regions.append(Region(
                region_id="",  # assigned by caller
                bbox=cluster_bb,
                content_type=c_type,
                source="vector",
                confidence=0.85 if c_type == ContentType.DIAGRAM else 0.75,
            ))

        return regions

    @staticmethod
    def _merge_overlapping_bboxes(bboxes: List[BoundingBox]) -> List[BoundingBox]:
        """Merge bboxes that overlap into combined bounding boxes."""
        if not bboxes:
            return []

        merged = [bboxes[0]]
        for bb in bboxes[1:]:
            found_merge = False
            for i, m in enumerate(merged):
                if m.intersects(bb):
                    merged[i] = BoundingBox(
                        x0=min(m.x0, bb.x0),
                        y0=min(m.y0, bb.y0),
                        x1=max(m.x1, bb.x1),
                        y1=max(m.y1, bb.y1),
                    )
                    found_merge = True
                    break
            if not found_merge:
                merged.append(bb)

        return merged

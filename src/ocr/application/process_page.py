import sys
import time
import math
import re
from typing import Dict, Any, Optional, List
import pymupdf
import cv2

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from src.ocr.domain.models.page import ExtractedPage
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.models.quality import PageQualityReport
from src.ocr.domain.models.region import ContentType, Region, BoundingBox
from src.ocr.domain.models.evidence_block import EvidenceBlock
from src.ocr.domain.routing.page_type import PageType
from src.ocr.infrastructure.pdf.pdf_inspector import PDFInspector
from src.ocr.infrastructure.pdf.pdf_renderer import PDFRenderer
from src.ocr.infrastructure.pdf.region_cropper import RegionCropper
from src.ocr.infrastructure.pdf.native_text_extractor import NativeTextExtractor
from src.ocr.infrastructure.ocr.vietnamese_seq2seq_engine import VietnameseSeq2SeqEngine
from src.ocr.infrastructure.ocr.rapidocr_engine import RapidOCREngine
from src.ocr.infrastructure.ocr.fallback_engine import FallbackEngine
from src.ocr.infrastructure.quality.ocr_quality import OCRQualityEvaluator
from src.ocr.infrastructure.layout.document_layout_engine import DocumentLayoutEngine
from src.ocr.infrastructure.layout.page_content_analyzer import PageContentAnalyzer
from src.ocr.infrastructure.postprocessing.reading_order import ReadingOrderSorter
from src.ocr.infrastructure.postprocessing.unicode_normalizer import UnicodeNormalizer
from src.ocr.infrastructure.postprocessing.text_cleaner import TextCleaner
from src.ocr.infrastructure.quality.vietnamese_quality import VietnameseQualityEvaluator
from src.ocr.infrastructure.cache.artifact_cache import ArtifactCache
from src.ocr.application.route_page import RoutePageUseCase
from src.ocr.application.reocr_region import ReocrRegionUseCase
from src.ocr.infrastructure.export.markdown_exporter import MarkdownExporter

class ProcessPageUseCase:
    """
    Orchestrates extraction for a single PDF page:
    Route -> Native Extract or OCR -> Quality Gate -> Regional Fallback -> Layout/Tables -> Reading Order -> Cache.
    """
    def __init__(
        self,
        doc: pymupdf.Document,
        config: Dict[str, Any],
        doc_hash: str,
        cache: Optional[ArtifactCache] = None
    ):
        self.doc = doc
        self.config = config
        self.doc_hash = doc_hash
        self.cache = cache

        self.inspector = PDFInspector(doc)
        self.renderer = PDFRenderer(doc)
        self.native_extractor = NativeTextExtractor(doc)
        primary_engine = config.get("primary_engine", "vietocr")
        if primary_engine == "rapidocr":
            self.ocr_engine = RapidOCREngine()
        elif primary_engine == "vietocr":
            self.ocr_engine = VietnameseSeq2SeqEngine(batch_size=config.get("batch_size", 16))
        else:
            raise ValueError(f"Unsupported primary_engine: {primary_engine}")
        self.ocr_quality = OCRQualityEvaluator(min_confidence=config.get("min_confidence", 0.80))
        self.layout_engine = DocumentLayoutEngine()
        self.content_analyzer = PageContentAnalyzer()
        self.fallback_engine = FallbackEngine(
            upscale_dpi=config.get("fallback_upscale_dpi", 250),
            ocr_engine=self.ocr_engine,
        )
        self.reocr_use_case = ReocrRegionUseCase(self.renderer, self.fallback_engine)

    def execute(
        self,
        page_num: int,
        total_pages: int,
        router: RoutePageUseCase,
        resume: bool = False
    ) -> ExtractedPage:
        t0 = time.time()
        profile_name = self.config.get("profile", "balanced")
        render_dpi = self.config.get("render_dpi", 180)

        # 1. Check cache if resume is active
        if resume and self.cache:
            cached = self.cache.get_cached_page(self.doc_hash, page_num, profile_name, render_dpi)
            if cached is not None:
                print(f"[{page_num}/{total_pages}] page={page_num} [CACHED] restored from .cache")
                return cached

        # 2. Inspect page metadata
        meta = self.inspector.inspect_page(page_num)
        page_type, native_score = router.execute(meta)

        page_w = meta["width"]
        page_h = meta["height"]

        # 2b. V3.1.2 Content Composition — decompose page into regions
        doc_page = self.doc[page_num - 1]
        regions = self.content_analyzer.analyze(meta, doc_page)
        inspect_duration = time.time() - t0
        render_duration = 0.0
        ocr_duration = 0.0
        fallback_duration = 0.0
        recognized_lines = 0
        recognizer_confidences: List[float] = []
        from collections import Counter
        region_type_counts = dict(Counter(r.content_type.value for r in regions))

        print(f"[{page_num}/{total_pages}] page={page_num} type={page_type.value} dpi={render_dpi} regions={len(regions)} {region_type_counts}")

        blocks: List[TextBlock] = []
        self._current_page_diagrams: List[Any] = []
        fallback_count = 0
        suspicious_lines: List[str] = []
        candidates_for_fallback: List[TextBlock] = []
        actual_dpi = None

        char_count = meta.get("char_count", 0)
        is_native_eligible = (
            (
                page_type in (PageType.NATIVE_TEXT, PageType.MIXED)
                and native_score >= self.config.get("native_review_threshold", 0.70)
            )
            or (
                page_type == PageType.PRODUCT_SHOWCASE_COLLAGE
                and char_count >= 30
                and not meta.get("unicode_corruption_detected", False)
            )
        ) and not meta.get("native_extraction_failed", False)

        if is_native_eligible:
            # Extract digital native text directly (accuracy-first, 0 OCR corruption)
            blocks = self.native_extractor.extract_blocks(page_num)
            for b in blocks:
                b.quality_score = 1.0
                b.confidence = 1.0

            if not blocks and page_type == PageType.MIXED:
                # Defensive fallback: If native extractor returned 0 blocks on mixed page,
                # fall through to OCR to avoid content loss.
                is_native_eligible = False

        if is_native_eligible:
            # Determine which regions require regional OCR (V4.0 Pillar 2: Vector & Diagram preservation):
            # - For MIXED or TWO_UP pages: all non-decorative image, vector and diagram regions
            # - For NATIVE_TEXT pages: any IMAGE_TABLE or DIAGRAM regions
            page_area = max(1.0, page_w * page_h)
            if page_type == PageType.PRODUCT_SHOWCASE_COLLAGE:
                target_regions = [
                    r for r in regions
                    if r.content_type in (ContentType.IMAGE_TABLE, ContentType.DIAGRAM)
                    or (r.content_type in (ContentType.IMAGE, ContentType.FIGURE)
                        and r.bbox.area / page_area >= 0.02)
                ]
            elif page_type == PageType.MIXED or meta.get("layout_type") in ("TWO_UP", "MULTI_REGION"):
                target_regions = [
                    r for r in regions
                    if r.content_type in (ContentType.IMAGE, ContentType.IMAGE_TABLE, ContentType.VECTOR, ContentType.DIAGRAM)
                ]
            else:
                target_regions = [
                    r for r in regions
                    if r.content_type in (ContentType.IMAGE_TABLE, ContentType.DIAGRAM)
                    or (r.content_type in (ContentType.IMAGE, ContentType.FIGURE)
                        and r.bbox.area / page_area >= 0.20)
                ]

            image_ocr_blocks = self._extract_image_regions(
                doc_page,
                page_num,
                target_regions,
                blocks,
                render_dpi,
                evaluate_all_orientations=meta.get("layout_type") in ("TWO_UP", "MULTI_REGION"),
            )
            if image_ocr_blocks:
                blocks.extend(image_ocr_blocks)
                recognized_lines += len(image_ocr_blocks)
                recognizer_confidences.extend(b.confidence for b in image_ocr_blocks)

            if page_type == PageType.MIXED:
                print(f"[{page_num}/{total_pages}] mixed composition: native={len(blocks) - len(image_ocr_blocks)} image_ocr={len(image_ocr_blocks)} lines")
            elif image_ocr_blocks:
                print(f"[{page_num}/{total_pages}] native+image_table: native={len(blocks) - len(image_ocr_blocks)} image_ocr={len(image_ocr_blocks)} lines")
            else:
                print(f"[{page_num}/{total_pages}] native={len(blocks)} lines (native text gate passed: {native_score:.2f})")

        else:
            # Route to full-page OCR (scanned pages, corrupted native text, low-quality OCR layers)
            actual_dpi = render_dpi
            t_render_start = time.time()
            img_bgr = self.renderer.render_page(page_num, dpi=render_dpi)
            refined_regions = regions
            if self.config.get("two_up_detection_enabled", True) or self.config.get("multi_region_detection_enabled", True):
                refined_regions = self.content_analyzer.refine_with_rendered_image(
                    meta, regions, img_bgr
                )
            if refined_regions:
                regions = refined_regions
                region_type_counts = dict(Counter(r.content_type.value for r in regions))
                meta["region_count"] = len(regions)
            render_duration = time.time() - t_render_start
            t_ocr_start = time.time()
            if meta.get("layout_type") in ("TWO_UP", "MULTI_REGION") and len(regions) >= 2:
                ocr_results, orientation_fallback = self._predict_region_ocr(
                    img_bgr,
                    regions,
                    page_w,
                    page_h,
                    enabled=self.config.get("orientation_fallback_enabled", True),
                )
            else:
                ocr_results, orientation_fallback = self._predict_with_orientation_fallback(
                    img_bgr,
                    enabled=self.config.get("orientation_fallback_enabled", True),
                )
            if orientation_fallback:
                meta["orientation_fallback"] = orientation_fallback
                print(f"[{page_num}/{total_pages}] orientation_fallback={orientation_fallback}")
            ocr_duration = time.time() - t_ocr_start
            recognized_lines = len(ocr_results)
            recognizer_confidences = [float(result.get("confidence", 0.0)) for result in ocr_results]
            print(f"[{page_num}/{total_pages}] primary={len(ocr_results)} lines time={ocr_duration:.2f}s")

            # Check if this is a 1-Up landscape scan (full-page scan rotated 90 deg) or 180 deg scan
            is_1up_rotated_90 = (
                meta.get("layout_type") not in ("TWO_UP", "MULTI_REGION")
                and orientation_fallback in ("rotate_90_cw", "rotate_90_ccw")
            )
            is_1up_rotated_180 = (
                meta.get("layout_type") not in ("TWO_UP", "MULTI_REGION")
                and orientation_fallback == "rotate_180"
            )

            if is_1up_rotated_90:
                # Upright coordinate space: Swap page dimensions to effective landscape
                page_w, page_h = page_h, page_w
                meta["effective_width"] = page_w
                meta["effective_height"] = page_h
                meta["is_1up_landscape"] = True
                meta["page_orientation"] = "LANDSCAPE"
                # In 90 deg rotation, rotated image width is img_bgr.shape[0] and height is img_bgr.shape[1]
                rot_w = img_bgr.shape[0]
                rot_h = img_bgr.shape[1]
                scale_x = page_w / max(1.0, rot_w)
                scale_y = page_h / max(1.0, rot_h)
            elif is_1up_rotated_180:
                meta["is_180_rotated"] = True
                rot_w = img_bgr.shape[1]
                rot_h = img_bgr.shape[0]
                scale_x = page_w / max(1.0, rot_w)
                scale_y = page_h / max(1.0, rot_h)
            else:
                scale_x = page_w / max(1.0, img_bgr.shape[1])
                scale_y = page_h / max(1.0, img_bgr.shape[0])

            # Convert OCR results to TextBlocks and evaluate quality
            for idx, res in enumerate(ocr_results):
                raw_bbox = res.get("raw_bbox") if ((is_1up_rotated_90 or is_1up_rotated_180) and "raw_bbox" in res) else res["bbox"]
                scaled_bbox = [
                    round(raw_bbox[0] * scale_x, 2),
                    round(raw_bbox[1] * scale_y, 2),
                    round(raw_bbox[2] * scale_x, 2),
                    round(raw_bbox[3] * scale_y, 2)
                ]
                text = UnicodeNormalizer.normalize(res["text"])
                if TextCleaner.is_connector_line_artifact(text):
                    continue
                conf = res["confidence"]

                q_score, needs_fb, reasons = self.ocr_quality.evaluate_ocr_result(text, conf)

                block = TextBlock(
                    text=text,
                    source="primary_ocr",
                    page=page_num,
                    bbox=scaled_bbox,
                    confidence=conf,
                    quality_score=q_score,
                    reviewed=False,
                    line_index=idx,
                    region_id=res.get("region_id")
                )
                blocks.append(block)

                if needs_fb:
                    candidates_for_fallback.append(block)
                    suspicious_lines.append(f"{text} (reasons: {', '.join(reasons)})")

            # 4. Adaptive Regional Fallback (if enabled)
            if self.config.get("fallback_enabled", True) and candidates_for_fallback:
                max_ratio = self.config.get("max_fallback_region_ratio", 0.15)
                max_count = self.config.get("max_fallback_regions_per_page", 20)
                total_lines = len(blocks)

                budget = min(max_count, max(1, math.ceil(total_lines * max_ratio))) if total_lines else 0
                if budget > 0:
                    t_fallback_start = time.time()
                    upscale_dpi = self.config.get("fallback_upscale_dpi", 250)
                    priority_candidates = sorted(
                        candidates_for_fallback,
                        key=lambda block: (block.quality_score, block.confidence),
                    )[:budget]
                    _, improved = self.reocr_use_case.execute(
                        page_num=page_num,
                        blocks_to_retry=priority_candidates,
                        upscale_dpi=upscale_dpi
                    )
                    fallback_count = improved
                    fallback_duration = time.time() - t_fallback_start

            for candidate in candidates_for_fallback:
                if candidate.fallback_reason != "OCR candidates disagree on numeric text":
                    continue
                if len(candidate.bbox) < 4:
                    continue
                mid_x = (candidate.bbox[0] + candidate.bbox[2]) / 2.0
                mid_y = (candidate.bbox[1] + candidate.bbox[3]) / 2.0
                for region in regions:
                    if region.bbox.contains_point(mid_x, mid_y):
                        region.needs_fallback = True
                        region.failure_reason = candidate.fallback_reason
                        break

        # 5. Extract tables (doc_page already obtained during content analysis)
        t_table_start = time.time()
        allow_vector_tables = (
            page_type in (PageType.NATIVE_TEXT, PageType.MIXED)
            and native_score >= self.config.get("vector_table_min_native_score", 0.90)
            and not meta.get("native_extraction_failed", False)
            and not (
                self.config.get("skip_vector_tables_on_restricted_pdf", True)
                and meta.get("copy_restricted", False)
            )
        )
        tables = self.layout_engine.extract_tables(
            doc_page,
            blocks,
            page_num,
            allow_vector_tables=allow_vector_tables,
            regions=regions,
        )

        # Filter out text blocks that fall inside legitimate tables to prevent duplication
        if tables:
            blocks = [b for b in blocks if not self.layout_engine.is_block_inside_any_table(b.bbox, tables)]
        table_duration = time.time() - t_table_start

        # 6. Sort Reading Order
        order_sorter = ReadingOrderSorter(page_width=page_w, page_height=page_h)
        sorted_blocks = order_sorter.sort_blocks(blocks)

        block_regions = self._link_blocks_to_regions(sorted_blocks, regions, page_num)
        orientation_angle = {
            "rotate_90_cw": 90.0,
            "rotate_90_ccw": 270.0,
            "rotate_180": 180.0,
        }.get(meta.get("orientation_fallback"), 0.0)
        for region in regions:
            if region.region_id in block_regions:
                region.status = "NEEDS_REVIEW" if region.needs_fallback else "EXTRACTED"
                if region.orientation_status == "UNKNOWN":
                    region.rotation = orientation_angle
                    region.orientation_status = "CONFIDENT" if orientation_angle else "UNCERTAIN"
                    region.orientation_confidence = 0.8 if orientation_angle else 0.5
            elif any(
                table.bbox and len(table.bbox) >= 4
                and region.bbox.overlap_ratio(BoundingBox(
                    x0=table.bbox[0], y0=table.bbox[1],
                    x1=table.bbox[2], y1=table.bbox[3],
                )) > 0.5
                for table in tables
            ):
                region.status = "EXTRACTED"
                if region.orientation_status == "UNKNOWN":
                    region.rotation = orientation_angle
                    region.orientation_status = "CONFIDENT" if orientation_angle else "UNCERTAIN"
                    region.orientation_confidence = 0.8 if orientation_angle else 0.5
            elif region.content_type in (ContentType.IMAGE, ContentType.IMAGE_TABLE, ContentType.FIGURE):
                region.status = "PRESERVED"
                if region.orientation_status == "UNKNOWN":
                    region.orientation_status = "UNCERTAIN"
                region.failure_reason = region.failure_reason or "Image preserved without recognized text"
            else:
                region.failure_reason = region.failure_reason or "No matching extracted block or table"

        total_time = round(time.time() - t0, 2)
        mean_conf = (
            sum(recognizer_confidences) / len(recognizer_confidences)
            if recognizer_confidences else 0.0
        )
        mean_q = sum(b.quality_score for b in sorted_blocks) / len(sorted_blocks) if sorted_blocks else 0.0
        if not sorted_blocks and tables:
            mean_q = sum(t.confidence for t in tables) / len(tables)
        if sorted_blocks or tables or self._current_page_diagrams:
            content_status, content_reason = "EXTRACTED", None
        elif any(r.content_type in (ContentType.IMAGE, ContentType.IMAGE_TABLE, ContentType.FIGURE) for r in regions):
            content_status = "IMAGE_PRESERVED_NO_TEXT"
            content_reason = "No text recognized; image is preserved for review"
        else:
            content_status = "UNRESOLVED_NO_CONTENT"
            content_reason = "No text, table, diagram, or image region was extracted"

        quality_report = PageQualityReport(
            page=page_num,
            page_type=page_type.value,
            native_text_score=native_score,
            ocr_confidence=round(mean_conf, 4),
            quality_score=round(mean_q, 4),
            total_blocks=len(sorted_blocks),
            recognized_lines=recognized_lines,
            fallback_count=fallback_count,
            review_required_count=sum(not block.reviewed for block in candidates_for_fallback),
            suspicious_lines=suspicious_lines[:10],
            time_taken_seconds=total_time,
            stage_seconds={
                "inspect": round(inspect_duration, 3),
                "render_and_refine": round(render_duration, 3),
                "primary_ocr": round(ocr_duration, 3),
                "fallback": round(fallback_duration, 3),
                "table": round(table_duration, 3),
                "postprocess": round(max(0.0, total_time - inspect_duration - render_duration - ocr_duration - fallback_duration - table_duration), 3),
            },
            unicode_corruption_detected=bool(
                meta.get("unicode_corruption_detected", False)
                or meta.get("native_extraction_failed", False)
            ),
            native_extraction_failed=bool(meta.get("native_extraction_failed", False)),
            orientation_fallback=meta.get("orientation_fallback"),
            vector_table_probe_enabled=allow_vector_tables,
            region_count=len(regions),
            region_types=region_type_counts,
            content_status=content_status,
            content_reason=content_reason,
        )

        # 7. V3.1.2 EvidenceBlock intermediate representation (spec §3)
        evidence_blocks: List[EvidenceBlock] = []
        region_by_id = {r.region_id: r for r in regions}
        for idx, b in enumerate(sorted_blocks):
            src = "native" if b.source == "native" else "ocr"
            ct = ContentType.TEXT if src == "native" else ContentType.IMAGE
            block_region = region_by_id[b.region_id]
            needs_review = b.quality_score < 0.75
            evidence_blocks.append(EvidenceBlock(
                block_id=f"B_{page_num:04d}_{idx + 1:04d}",
                page=page_num,
                region_id=b.region_id,
                bbox=b.bbox if b.bbox else [],
                bbox_original=b.bbox if b.bbox else [],
                bbox_normalized=b.bbox if b.bbox else [],
                text=b.text,
                content_type=ct,
                source=src,
                confidence=b.confidence,
                orientation=block_region.rotation,
                quality_score=b.quality_score,
                block_type=b.block_type or "paragraph",
                is_bold=b.is_bold,
                font_size=b.font_size,
                reading_order=idx + 1,
                status="UNRESOLVED" if needs_review else "EXTRACTED",
                failure_reason="Low OCR quality score" if needs_review else None,
            ))

        extracted_page = ExtractedPage(
            page_num=page_num,
            width=page_w,
            height=page_h,
            page_type=page_type,
            rendered_dpi=actual_dpi,
            blocks=sorted_blocks,
            tables=tables,
            diagrams=self._current_page_diagrams,
            regions=regions,
            evidence_blocks=evidence_blocks,
            quality=quality_report,
            metadata=meta
        )

        # Export page markdown
        extracted_page.markdown_content = MarkdownExporter.export_page_markdown(extracted_page)

        # Save to cache
        if self.cache:
            self.cache.save_cached_page(self.doc_hash, extracted_page, profile_name, render_dpi)

        print(f"[{page_num}/{total_pages}] quality={mean_q:.2f} fallback={fallback_count}/{len(sorted_blocks)}")
        print(f"[{page_num}/{total_pages}] completed time={total_time:.2f}s")

        return extracted_page

    @staticmethod
    def _link_blocks_to_regions(
        blocks: List[TextBlock], regions: List[Region], page_num: int
    ) -> set[str]:
        linked: set[str] = set()
        for idx, block in enumerate(blocks):
            region = next((r for r in regions if r.region_id == block.region_id), None)
            if region is None and len(block.bbox) >= 4:
                mid_x = (block.bbox[0] + block.bbox[2]) / 2.0
                mid_y = (block.bbox[1] + block.bbox[3]) / 2.0
                candidates = [r for r in regions if r.bbox.contains_point(mid_x, mid_y)]
                preferred = (
                    (ContentType.TEXT, ContentType.OUTLINED_TEXT)
                    if block.source == "native"
                    else (ContentType.IMAGE, ContentType.IMAGE_TABLE, ContentType.TEXT)
                )
                candidates.sort(key=lambda r: (
                    0 if r.content_type in preferred else 1,
                    r.bbox.area,
                ))
                region = candidates[0] if candidates else None
            if region is None:
                bbox = block.bbox if len(block.bbox) >= 4 else [0.0, 0.0, 0.0, 0.0]
                region = Region(
                    region_id=f"p{page_num}_inferred_{idx + 1}",
                    bbox=BoundingBox(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3]),
                    content_type=ContentType.TEXT,
                    source="native" if block.source == "native" else "ocr",
                    orientation_status="UNCERTAIN",
                    failure_reason="Region inferred from text block bbox",
                )
                regions.append(region)
            block.region_id = region.region_id
            linked.add(region.region_id)
        return linked

    VN_LEXICON_WORDS = {
        "và", "các", "của", "trong", "năm", "cho", "tại", "báo", "cáo",
        "tài", "chính", "hợp", "nhất", "công", "ty", "cổ", "phần", "tiền",
        "phải", "thu", "trả", "chi", "phí", "hạn", "ngắn", "dài", "nợ",
        "số", "lượng", "thuyết", "minh", "tổng", "đầu", "tư", "giá", "trị",
        "dự", "phòng", "khách", "hàng", "bên", "liên", "quan", "xem", "không",
        "có", "vnd", "đồng", "stt", "ngày", "tháng", "kết", "thúc", "kinh",
        "doanh", "thương", "mại", "dịch", "vụ", "hoạt", "động", "lợi", "nhuận",
        "thuế", "tài", "sản", "ngân", "hàng", "khoản", "mục", "vay", "vốn",
        "chủ", "sở", "hữu", "lưu", "chuyển", "hàng", "tồn", "kho", "người",
        "đại", "diện", "theo", "pháp", "luật", "kế", "toán", "trưởng"
    }

    @classmethod
    def _calculate_vn_lexicon_ratio(cls, results: List[Dict[str, Any]]) -> float:
        if not results:
            return 0.0
        total_words = 0
        hits = 0
        for item in results:
            words = str(item.get("text", "")).lower().split()
            for w in words:
                clean_w = "".join(c for c in w if c.isalpha())
                if clean_w:
                    total_words += 1
                    if clean_w in cls.VN_LEXICON_WORDS:
                        hits += 1
        return hits / max(1, total_words)

    def _predict_with_orientation_fallback(
        self,
        img_bgr,
        enabled: bool = True,
        allow_hybrid: bool = True,
        evaluate_all_orientations: bool = False,
    ):
        """Retry a failed landscape or 180-degree scan after rotating it for the detector.

        Evaluates orientations when first pass fails, produces vertical glyph noise,
        has low Vietnamese lexicon ratio (< 12%), or when evaluate_all_orientations is requested.
        """
        results = self.ocr_engine.predict_image(img_bgr)
        is_noise = self._is_glyph_noise(results)
        vn_ratio = self._calculate_vn_lexicon_ratio(results)

        # A high-confidence full page can be readable even when the recognizer omits
        # Vietnamese diacritics. Avoid three unnecessary rotated OCR passes in that case.
        if (
            results and not is_noise and not evaluate_all_orientations
            and (vn_ratio >= 0.12 or self._is_readable_ocr_layout(results))
        ):
            return results, None
        if not enabled:
            return results, None

        height, width = img_bgr.shape[:2]
        candidates = []
        if results and not is_noise:
            candidates.append((self._orientation_candidate_score(results, label=None), results, None, vn_ratio))

        # Rotate order: 90_CW is standard for landscape tables in Vietnamese portrait PDFs (top is on left),
        # followed by 90_CCW and 180.
        for code, label in (
            (cv2.ROTATE_90_CLOCKWISE, "rotate_90_cw"),
            (cv2.ROTATE_90_COUNTERCLOCKWISE, "rotate_90_ccw"),
            (cv2.ROTATE_180, "rotate_180"),
        ):
            rotated = cv2.rotate(img_bgr, code)
            rotated_results = self.ocr_engine.predict_image(rotated)
            if not rotated_results or self._is_glyph_noise(rotated_results):
                continue

            rot_vn_ratio = self._calculate_vn_lexicon_ratio(rotated_results)

            mapped = []
            for item in rotated_results:
                bbox = item.get("bbox", [])
                if len(bbox) < 4:
                    continue
                rx0, ry0, rx1, ry1 = bbox
                if code == cv2.ROTATE_90_CLOCKWISE:
                    mapped_bbox = [ry0, height - rx1, ry1, height - rx0]
                elif code == cv2.ROTATE_90_COUNTERCLOCKWISE:
                    mapped_bbox = [width - ry1, rx0, width - ry0, rx1]
                else:  # ROTATE_180
                    mapped_bbox = [width - rx1, height - ry1, width - rx0, height - ry0]
                mapped_item = dict(item)
                mapped_item["bbox"] = [round(float(v), 2) for v in mapped_bbox]
                mapped_item["raw_bbox"] = [round(float(v), 2) for v in bbox]
                mapped.append(mapped_item)
            if mapped:
                candidates.append((self._orientation_candidate_score(mapped, label=label), mapped, label, rot_vn_ratio))

        if candidates:
            # If 0 deg was already valid text (not noise), require a significant score margin
            # or higher Vietnamese lexicon density before overriding with a rotation
            orig_candidate = next((c for c in candidates if c[2] is None), None)
            best_score, best_results, best_label, best_vn_ratio = max(candidates, key=lambda item: item[0])
            if orig_candidate and best_label is not None:
                orig_score, orig_results, _, orig_vn_ratio = orig_candidate
                # A small language-score gain can come from hallucinated words
                # on an upside-down crop. Keep the original angle and expose
                # ambiguity instead of silently rotating it.
                strong_language_recovery = orig_vn_ratio <= 0.02 and best_vn_ratio >= 0.25
                if best_score < orig_score + 8.0 and not strong_language_recovery:
                    return orig_results, "orientation_uncertain"
                if best_label == "rotate_180" and orig_vn_ratio >= 0.12 and best_vn_ratio <= orig_vn_ratio * 1.5:
                    return orig_results, None
            return best_results, best_label

        # If all rotations returned noise or empty, return original results if any
        if results:
            return results, None

        # Hybrid 2-Up check for landscape pages with mixed orientation sub-regions
        if allow_hybrid and width >= height * 1.25:
            hybrid_results, hybrid_label = self._predict_hybrid_2up(img_bgr, enabled=enabled)
            if hybrid_results:
                return hybrid_results, hybrid_label

        return [], None

    @staticmethod
    def _is_readable_ocr_layout(results: List[Dict[str, Any]]) -> bool:
        if len(results) < 20:
            return False
        texts = [str(item.get("text", "")).strip() for item in results]
        meaningful = sum(len(text) >= 12 for text in texts)
        mean_conf = sum(float(item.get("confidence", 0.0)) for item in results) / len(results)
        return meaningful / len(results) >= 0.55 and mean_conf >= 0.90

    @staticmethod
    def _is_glyph_noise(results: List[Dict[str, Any]]) -> bool:
        """
        Detects whether OCR output is vertical glyph noise / isolated fragment noise
        (typically caused by running horizontal OCR on a 90-degree rotated page).
        """
        if not results:
            return True
        texts = [str(item.get("text", "")).strip() for item in results if str(item.get("text", "")).strip()]
        if not texts:
            return True
        total = len(texts)
        single_or_double = sum(1 for t in texts if len(t) <= 2)
        vertical_strokes = sum(1 for t in texts if t in ("I", "1", "l", "|", "!", "[", "]", "/", "-", "i", "L", "T", "H", "E", "F"))
        meaningful = sum(1 for t in texts if len(t) >= 4)
        total_chars = sum(len(t) for t in texts)
        avg_len = total_chars / max(1, total)

        # High proportion of isolated 1-2 char glyphs with few real words
        if total <= 20 and (single_or_double / total >= 0.40) and meaningful < 3:
            return True
        if (single_or_double / total >= 0.35) and meaningful < 12:
            return True
        if (vertical_strokes / total >= 0.25):
            return True
        if avg_len < 3.2 and (meaningful / total <= 0.15):
            return True
        if avg_len < 3.0 and meaningful <= 2:
            return True
        return False

    @staticmethod
    def _orientation_candidate_score(results: List[Dict[str, Any]], label: Optional[str] = None) -> float:
        """Prefer readable multi-character Vietnamese OCR over vertical glyph noise or upside-down text."""
        if not results:
            return float("-inf")
        texts = [str(item.get("text", "")).strip() for item in results]
        texts = [t for t in texts if t]
        if not texts:
            return float("-inf")

        total_lines = len(texts)
        chars = sum(len(text) for text in texts)
        meaningful = sum(1 for text in texts if len(text) >= 3)
        long_words = sum(1 for text in texts if len(text) >= 5)
        single_glyphs = sum(1 for text in texts if len(text) <= 2)
        confidence = sum(float(item.get("confidence", 0.0)) for item in results) / total_lines

        # Common Vietnamese lexicon bonus (strongly prefers upright text over upside-down text)
        vn_keywords = {
            "và", "các", "của", "trong", "năm", "cho", "tại", "báo", "cáo",
            "tài", "chính", "hợp", "nhất", "công", "ty", "cổ", "phần", "tiền",
            "phải", "thu", "trả", "chi", "phí", "hạn", "ngắn", "dài", "nợ",
            "số", "lượng", "thuyết", "minh", "tổng", "đầu", "tư", "giá", "trị",
            "dự", "phòng", "khách", "hàng", "bên", "liên", "quan", "xem", "không",
            "có", "vnd", "đồng", "stt", "ngày", "tháng", "kết", "thúc", "kinh",
            "doanh", "thương", "mại", "dịch", "vụ", "hoạt", "động", "lợi", "nhuận",
            "thuế", "tài", "sản", "ngân", "hàng", "khoản", "mục", "vay", "vốn",
            "chủ", "sở", "hữu", "lưu", "chuyển", "hàng", "tồn", "kho", "người",
            "đại", "diện", "theo", "pháp", "luật", "kế", "toán", "trưởng"
        }
        keyword_hits = 0
        for text in texts:
            words = text.lower().split()
            for w in words:
                clean_w = "".join(c for c in w if c.isalpha())
                if clean_w in vn_keywords:
                    keyword_hits += 1

        vn_evaluator = VietnameseQualityEvaluator()
        vn_scores = [vn_evaluator.evaluate_line(text)[0] for text in texts if text]
        vn_quality = sum(vn_scores) / len(vn_scores) if vn_scores else 0.0

        suspicious = sum(
            1 for text in texts
            if UnicodeNormalizer.analyze(text).get("is_corrupted", False)
        )

        score = (
            (confidence * 2.0)
            + min(10.0, chars / 50.0)
            + (meaningful / total_lines) * 6.0
            + (long_words / total_lines) * 3.0
            + (vn_quality * 2.0)
            + min(30.0, keyword_hits * 1.5)
            - ((single_glyphs / total_lines) * 5.0)
            - (suspicious * 0.5)
        )
        return score

    def _predict_region_ocr(
        self,
        img_bgr,
        regions: List[Region],
        page_width: float,
        page_height: float,
        enabled: bool = True,
    ):
        """OCR each detected Two-Up/multi-region crop and restore page pixels."""
        image_h, image_w = img_bgr.shape[:2]
        combined = []
        orientations = []
        image_regions = [
            r for r in regions
            if r.content_type in (ContentType.IMAGE, ContentType.IMAGE_TABLE)
        ]
        route_regions = image_regions if len(image_regions) >= 2 else regions
        for region in sorted(route_regions, key=lambda r: (r.bbox.x0, r.bbox.y0)):
            bb = region.bbox
            px0 = max(0, min(image_w - 1, int(round(bb.x0 / max(page_width, 1.0) * image_w))))
            py0 = max(0, min(image_h - 1, int(round(bb.y0 / max(page_height, 1.0) * image_h))))
            px1 = max(px0 + 1, min(image_w, int(round(bb.x1 / max(page_width, 1.0) * image_w))))
            py1 = max(py0 + 1, min(image_h, int(round(bb.y1 / max(page_height, 1.0) * image_h))))
            crop = img_bgr[py0:py1, px0:px1]
            if crop.size == 0:
                continue

            local_results, label = self._predict_with_orientation_fallback(
                crop,
                enabled=enabled,
                allow_hybrid=False,
                evaluate_all_orientations=False,
            )
            if label:
                orientations.append(f"{region.region_id}:{label}")
                if "90_cw" in label:
                    region.rotation = 90.0
                    region.orientation_status = "CONFIDENT"
                    region.orientation_confidence = 0.80
                elif "90_ccw" in label:
                    region.rotation = 270.0
                    region.orientation_status = "CONFIDENT"
                    region.orientation_confidence = 0.80
                elif "180" in label:
                    region.rotation = 180.0
                    region.orientation_status = "CONFIDENT"
                    region.orientation_confidence = 0.80
                else:
                    region.orientation_status = "UNCERTAIN"
                    region.needs_fallback = True
                    region.failure_reason = "OCR orientations disagree; manual review required"

            for item in local_results:
                bbox = item.get("bbox", [])
                if len(bbox) < 4:
                    continue
                mapped = dict(item)
                mapped["bbox"] = [
                    round(float(bbox[0]) + px0, 2),
                    round(float(bbox[1]) + py0, 2),
                    round(float(bbox[2]) + px0, 2),
                    round(float(bbox[3]) + py0, 2),
                ]
                mapped["region_id"] = region.region_id
                combined.append(mapped)

        return combined, ";".join(orientations) if orientations else None

    def _predict_hybrid_2up(
        self,
        img_bgr,
        enabled: bool = True
    ):
        """
        V3.1.2 Phase F: Hybrid 2-Up orientation processing.
        Splits a landscape page into left and right halves, independently evaluates
        and rotates each half, then maps bounding boxes back to page space.
        """
        if not enabled:
            return [], None

        height, width = img_bgr.shape[:2]
        if width < height * 1.25:
            return [], None

        mid_x = width // 2
        left_img = img_bgr[:, :mid_x]
        right_img = img_bgr[:, mid_x:]

        left_results, left_orient = self._predict_with_orientation_fallback(
            left_img, enabled=True, allow_hybrid=False
        )
        right_results, right_orient = self._predict_with_orientation_fallback(
            right_img, enabled=True, allow_hybrid=False
        )

        if left_results or right_results:
            combined = []
            if left_results:
                combined.extend(left_results)
            if right_results:
                for item in right_results:
                    mapped_item = dict(item)
                    bx = list(item.get("bbox", [0.0, 0.0, 0.0, 0.0]))
                    mapped_item["bbox"] = [
                        round(bx[0] + mid_x, 2),
                        round(bx[1], 2),
                        round(bx[2] + mid_x, 2),
                        round(bx[3], 2),
                    ]
                    combined.append(mapped_item)

            orient_tag = f"hybrid_2up(L:{left_orient or '0'},R:{right_orient or '0'})"
            return combined, orient_tag

        return [], None

    def _extract_image_regions(
        self,
        doc_page: pymupdf.Page,
        page_num: int,
        target_regions: List[Region],
        native_blocks: List[TextBlock],
        render_dpi: int,
        evaluate_all_orientations: bool = False,
    ) -> List[TextBlock]:
        """
        V3.1.2 Phase B: Inspects and extracts text from candidate image regions.
        - Inspects large images even when a sparse native header/footer exists.
        - Skips images substantially covered by clean native text.
        - Runs OCR on cropped regions, maps coordinates back to page.
        - Suppresses OCR blocks that duplicate clean native text blocks (spec §17).
        """
        if not target_regions:
            return []

        image_blocks: List[TextBlock] = []
        page_rect = doc_page.rect
        page_area = max(1.0, float(page_rect.width * page_rect.height))
        native_bboxes = [b.bbox for b in native_blocks if b.bbox and len(b.bbox) >= 4]

        for region in target_regions:
            bb = region.bbox
            # FastIconFilter (spec §4.1): Skip decorative icons, bullets, and micro-images
            if bb.width < 60.0 or bb.height < 40.0 or bb.area < 3000.0:
                continue

            # Check if region is covered by native text blocks
            covered_area = 0.0
            for nb in native_bboxes:
                ix0 = max(bb.x0, nb[0])
                iy0 = max(bb.y0, nb[1])
                ix1 = min(bb.x1, nb[2])
                iy1 = min(bb.y1, nb[3])
                if ix1 > ix0 and iy1 > iy0:
                    covered_area += (ix1 - ix0) * (iy1 - iy0)
            if bb.area > 0 and (covered_area / bb.area) > 0.40:
                continue

            # Crop region and run OCR
            crop_bgr, actual_crop_rect = RegionCropper.crop_page_region(
                doc_page, bb, dpi=render_dpi, margin=3.0
            )
            if crop_bgr.size == 0 or crop_bgr.shape[0] < 16 or crop_bgr.shape[1] < 16:
                continue

            crop_h, crop_w = crop_bgr.shape[:2]
            try:
                ocr_results, orientation_label = self._predict_with_orientation_fallback(
                    crop_bgr,
                    enabled=self.config.get("orientation_fallback_enabled", True),
                    evaluate_all_orientations=evaluate_all_orientations,
                )
                mean_confidence = (
                    sum(float(item.get("confidence", 0.0)) for item in ocr_results) / len(ocr_results)
                    if ocr_results else 0.0
                )
                retry_risk = (
                    not ocr_results or self._is_glyph_noise(ocr_results)
                    or mean_confidence < self.config.get("min_confidence", 0.85)
                )
                if (
                    self.config.get("regional_retry_enabled", False)
                    and retry_risk
                    and bb.area / page_area >= 0.10
                    and self.config.get("fallback_upscale_dpi", render_dpi) > render_dpi
                ):
                    retry_crop, retry_rect = RegionCropper.crop_page_region(
                        doc_page, bb,
                        dpi=self.config["fallback_upscale_dpi"], margin=3.0,
                    )
                    if retry_crop.size:
                        retry_results, retry_orientation = self._predict_with_orientation_fallback(
                            retry_crop,
                            enabled=self.config.get("orientation_fallback_enabled", True),
                            evaluate_all_orientations=evaluate_all_orientations,
                        )
                        original_numbers = re.findall(
                            r"\d+(?:[.,/]\d+)*", " ".join(item.get("text", "") for item in ocr_results)
                        )
                        retry_numbers = re.findall(
                            r"\d+(?:[.,/]\d+)*", " ".join(item.get("text", "") for item in retry_results)
                        )
                        if ocr_results and retry_results and original_numbers != retry_numbers and (original_numbers or retry_numbers):
                            region.needs_fallback = True
                            region.failure_reason = "High-DPI OCR disagrees on numeric text; manual review required"
                        elif retry_results and (
                            not ocr_results
                            or self._orientation_candidate_score(retry_results) >
                            self._orientation_candidate_score(ocr_results) + 5.0
                        ):
                            ocr_results = retry_results
                            orientation_label = retry_orientation
                            crop_bgr, actual_crop_rect = retry_crop, retry_rect
                            crop_h, crop_w = retry_crop.shape[:2]
                if orientation_label:
                    if "90_cw" in orientation_label:
                        region.rotation = 90.0
                    elif "90_ccw" in orientation_label:
                        region.rotation = 270.0
                    elif "180" in orientation_label:
                        region.rotation = 180.0
                    if orientation_label == "orientation_uncertain":
                        region.orientation_status = "UNCERTAIN"
                        region.orientation_confidence = 0.0
                        region.needs_fallback = True
                        region.failure_reason = "OCR orientations disagree; manual review required"
                    else:
                        region.orientation_status = "CONFIDENT"
                        region.orientation_confidence = 0.80
                    print(f"[{page_num}] region {region.region_id} rotation={region.rotation}° ({orientation_label}, {len(ocr_results)} lines)")
                elif evaluate_all_orientations:
                    region.orientation_status = "CONFIDENT"
                    region.orientation_confidence = 0.70
            except Exception as e:
                import traceback
                print(f"[WARN] Region OCR failed for region {region.region_id}: {e}")
                traceback.print_exc()
                continue
            if not ocr_results:
                continue

            # V4.0 Diagram Structure & Mermaid Detection (Pillar 4)
            from src.ocr.infrastructure.layout.diagram_builder import DiagramBuilder
            if region.content_type == ContentType.DIAGRAM or DiagramBuilder.is_diagram_content(ocr_results):
                diagram = DiagramBuilder.build_org_chart(
                    ocr_results,
                    page_num=page_num,
                    diagram_idx=len(self._current_page_diagrams) + 1
                )
                if diagram:
                    self._current_page_diagrams.append(diagram)
                    try:
                        print(f"[{page_num}] Reconstructed diagram: {diagram.title} ({len(diagram.nodes)} nodes, {len(diagram.edges)} edges)")
                    except Exception:
                        pass

            for idx, res in enumerate(ocr_results):
                raw_bbox = res.get("bbox", [])
                if len(raw_bbox) < 4:
                    continue

                text = UnicodeNormalizer.normalize(res.get("text", ""))
                if not text.strip() or TextCleaner.is_connector_line_artifact(text):
                    continue

                mapped_bbox = RegionCropper.map_crop_bbox_to_page(
                    raw_bbox, actual_crop_rect, crop_w, crop_h
                )

                # Check if mapped OCR block overlaps with any native text block
                is_duplicate = False
                for nb in native_bboxes:
                    if RegionCropper.compute_intersection_over_min(mapped_bbox, nb) > 0.40:
                        is_duplicate = True
                        break

                if is_duplicate:
                    continue  # Native clean text takes priority

                conf = res.get("confidence", 0.85)
                q_score, _, _ = self.ocr_quality.evaluate_ocr_result(text, conf)

                block = TextBlock(
                    text=text,
                    source="image_ocr",
                    page=page_num,
                    bbox=mapped_bbox,
                    confidence=conf,
                    quality_score=q_score,
                    reviewed=False,
                    line_index=len(native_blocks) + len(image_blocks) + idx,
                    region_id=region.region_id,
                )
                image_blocks.append(block)

        return image_blocks

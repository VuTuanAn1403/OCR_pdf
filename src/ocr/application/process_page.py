import time
from typing import Dict, Any, Optional, List
import pymupdf

from src.ocr.domain.models.page import ExtractedPage
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.models.quality import PageQualityReport
from src.ocr.domain.routing.page_type import PageType
from src.ocr.infrastructure.pdf.pdf_inspector import PDFInspector
from src.ocr.infrastructure.pdf.pdf_renderer import PDFRenderer
from src.ocr.infrastructure.pdf.native_text_extractor import NativeTextExtractor
from src.ocr.infrastructure.ocr.vietnamese_seq2seq_engine import VietnameseSeq2SeqEngine
from src.ocr.infrastructure.ocr.fallback_engine import FallbackEngine
from src.ocr.infrastructure.quality.ocr_quality import OCRQualityEvaluator
from src.ocr.infrastructure.layout.document_layout_engine import DocumentLayoutEngine
from src.ocr.infrastructure.postprocessing.reading_order import ReadingOrderSorter
from src.ocr.infrastructure.postprocessing.unicode_normalizer import UnicodeNormalizer
from src.ocr.infrastructure.postprocessing.text_cleaner import TextCleaner
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
        self.ocr_engine = VietnameseSeq2SeqEngine(batch_size=config.get("batch_size", 16))
        self.ocr_quality = OCRQualityEvaluator(min_confidence=config.get("min_confidence", 0.80))
        self.layout_engine = DocumentLayoutEngine()
        self.fallback_engine = FallbackEngine(upscale_dpi=config.get("fallback_upscale_dpi", 250))
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

        print(f"[{page_num}/{total_pages}] page={page_num} type={page_type.value} dpi={render_dpi}")

        blocks: List[TextBlock] = []
        fallback_count = 0
        suspicious_lines: List[str] = []
        actual_dpi = None

        # 3. Route execution
        if page_type == PageType.NATIVE_TEXT:
            # High quality native text: 0 OCR needed!
            blocks = self.native_extractor.extract_blocks(page_num)
            for b in blocks:
                b.quality_score = 1.0
                b.confidence = 1.0
            print(f"[{page_num}/{total_pages}] native={len(blocks)} lines (native text gate passed: {native_score:.2f})")

        else:
            # Route to OCR
            actual_dpi = render_dpi
            img_bgr = self.renderer.render_page(page_num, dpi=render_dpi)
            t_ocr_start = time.time()
            ocr_results = self.ocr_engine.predict_image(img_bgr)
            ocr_duration = time.time() - t_ocr_start
            print(f"[{page_num}/{total_pages}] primary={len(ocr_results)} lines time={ocr_duration:.2f}s")

            # Scale OCR bounding boxes from image pixel coordinates back to PDF point coordinates
            scale_x = page_w / max(1.0, img_bgr.shape[1])
            scale_y = page_h / max(1.0, img_bgr.shape[0])

            # Convert OCR results to TextBlocks and evaluate quality
            candidates_for_fallback: List[TextBlock] = []
            for idx, res in enumerate(ocr_results):
                raw_bbox = res["bbox"]
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
                    line_index=idx
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

                if total_lines > 0 and (len(candidates_for_fallback) / total_lines <= max_ratio) and len(candidates_for_fallback) <= max_count:
                    upscale_dpi = self.config.get("fallback_upscale_dpi", 250)
                    _, improved = self.reocr_use_case.execute(
                        page_num=page_num,
                        blocks_to_retry=candidates_for_fallback,
                        upscale_dpi=upscale_dpi
                    )
                    fallback_count = improved

        # 5. Extract tables
        doc_page = self.doc[page_num - 1]
        tables = self.layout_engine.extract_tables(doc_page, blocks, page_num)

        # Filter out text blocks that fall inside legitimate tables to prevent duplication
        if tables:
            blocks = [b for b in blocks if not self.layout_engine.is_block_inside_any_table(b.bbox, tables)]

        # 6. Sort Reading Order
        order_sorter = ReadingOrderSorter(page_width=page_w, page_height=page_h)
        sorted_blocks = order_sorter.sort_blocks(blocks)

        total_time = round(time.time() - t0, 2)
        mean_conf = sum(b.confidence for b in sorted_blocks) / len(sorted_blocks) if sorted_blocks else 1.0
        mean_q = sum(b.quality_score for b in sorted_blocks) / len(sorted_blocks) if sorted_blocks else 1.0

        quality_report = PageQualityReport(
            page=page_num,
            page_type=page_type.value,
            native_text_score=native_score,
            ocr_confidence=round(mean_conf, 4),
            quality_score=round(mean_q, 4),
            total_blocks=len(sorted_blocks),
            fallback_count=fallback_count,
            suspicious_lines=suspicious_lines[:10],
            time_taken_seconds=total_time
        )

        extracted_page = ExtractedPage(
            page_num=page_num,
            width=page_w,
            height=page_h,
            page_type=page_type,
            rendered_dpi=actual_dpi,
            blocks=sorted_blocks,
            tables=tables,
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

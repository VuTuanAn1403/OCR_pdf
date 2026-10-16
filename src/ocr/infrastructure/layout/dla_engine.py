"""
V5.0 Document Layout Analysis (DLA) Engine.

Provides optional model inference and a heuristic fallback for 11 project labels:
- TITLE
- SECTION_HEADER
- HEADER (Running page header)
- FOOTER (Running page footer)
- PAGE_NUMBER
- TABLE
- FIGURE
- CAPTION
- TEXT / PARAGRAPH
- LIST
- FORMULA

Supports ONNX Runtime deep learning inference (DocLayout-YOLO / PP-Structure Layout)
with a fast, robust geometric fallback when offline or for ultra-high-speed CPU passes.
"""

from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import re
import numpy as np
import cv2
import pymupdf
import logging

from src.ocr.infrastructure.layout.model_adapters import build_adapter, image_box_to_pdf
from src.ocr.runtime.device import resolve_device

logger = logging.getLogger(__name__)

from src.ocr.domain.models.region import Region, BoundingBox, ContentType
from src.ocr.domain.models.text_block import TextBlock


# Project label order; not a claim of equivalence to a published benchmark.
DLA_CLASSES = [
    "title",           # 0
    "section_header",  # 1
    "text",            # 2
    "header",          # 3
    "footer",          # 4
    "page_number",     # 5
    "table",           # 6
    "figure",          # 7
    "caption",         # 8
    "list",            # 9
    "formula",         # 10
]

CLASS_TO_CONTENT_TYPE = {
    "title": ContentType.TITLE,
    "section_header": ContentType.SECTION_HEADER,
    "text": ContentType.TEXT,
    "header": ContentType.HEADER,
    "footer": ContentType.FOOTER,
    "page_number": ContentType.PAGE_NUMBER,
    "table": ContentType.TABLE,
    "figure": ContentType.FIGURE,
    "caption": ContentType.CAPTION,
    "list": ContentType.LIST,
    "formula": ContentType.FORMULA,
}


class HeuristicLayoutClassifier:
    """
    Heuristic and Geometric Document Layout Classifier.
    Executes geometric visual decomposition and element classification
    (renamed per Proposal R4; formerly named DLAEngine).
    """

    def __init__(
        self,
        model_path: Optional[str] = None,
        conf_threshold: float = 0.35,
        iou_threshold: float = 0.45,
        use_onnx: bool = True,
        model_config: Optional[Dict[str, Any]] = None,
        device: str = "auto",
        num_threads: int = 4,
        legacy_furniture: bool = False,
    ):
        self.model_path = Path(model_path) if model_path else None
        self.legacy_furniture = legacy_furniture
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.use_onnx = use_onnx
        self._session = None
        options = dict(model_config or {})
        self.adapter_kind = options.get("adapter", "yolo")
        self.strict = options.get("on_error") == "raise"
        if self.adapter_kind == "hf_rtdetr_torch":
            from src.ocr.infrastructure.layout.torch_layout import TorchLayout
            self.device = resolve_device(device)
            self._torch_layout = TorchLayout(options, self.device)
            self._session = self._torch_layout
            self.last_error = None
            return
        self.class_names = options.get("class_names", DLA_CLASSES if self.adapter_kind == "yolo" else None)
        if not self.class_names or not isinstance(self.class_names, list) or not all(isinstance(v, str) for v in self.class_names):
            raise ValueError("Layout adapter requires an explicit class_names list matching the exported model")
        self.label_map = options.get("label_map", {})
        self.adapter = build_adapter(self.adapter_kind, len(self.class_names),
                                     {"threshold": conf_threshold, "iou_threshold": iou_threshold, **options})
        self.device = resolve_device(device)
        self.num_threads = max(1, int(num_threads))
        self.last_error = None

        if self.use_onnx and self.model_path and not self.model_path.is_file():
            raise FileNotFoundError(f"Layout model not found: {self.model_path}")
        if self.use_onnx and self.model_path and self.model_path.exists():
            self._init_onnx_session()

    def _init_onnx_session(self) -> None:
        """Initializes ONNX Runtime session with CPU / CUDA ExecutionProvider."""
        try:
            import onnxruntime as ort
            providers = ["CPUExecutionProvider"]
            if self.device == "cuda" and "CUDAExecutionProvider" in ort.get_available_providers():
                providers.insert(0, "CUDAExecutionProvider")
            elif self.device == "cuda":
                raise RuntimeError("CUDAExecutionProvider is unavailable for the requested layout device")
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = self.num_threads
            self._session = ort.InferenceSession(str(self.model_path), opts, providers=providers)
            if self.device == "cuda" and self._session.get_providers()[0] != "CUDAExecutionProvider":
                raise RuntimeError("Layout session fell back to CPU despite the requested CUDA device")
            logger.info("Loaded layout model %s using %s", self.model_path, self._session.get_providers()[0])
        except Exception as e:
            if self.strict:
                raise RuntimeError(f"GPU layout initialization failed: {e}") from e
            self.last_error = str(e)
            logger.warning("Layout model initialization failed; using geometric fallback: %s", e)
            self._session = None

    def analyze_page(
        self,
        page: pymupdf.Page,
        page_num: int,
        page_image: Optional[np.ndarray] = None,
        total_pages: int = 1,
    ) -> List[Region]:
        """
        Decomposes a PDF page into typed semantic Regions.
        Combines deep visual features and structural geometry.
        """
        if self._session is not None and page_image is not None:
            try:
                self.last_error = None
                return self._run_onnx_dla(page_image, page, page_num)
            except Exception as e:
                self.last_error = str(e)
                logger.warning("Layout inference failed; using geometric fallback: %s", e)

        return self._run_geometric_dla(page, page_num, total_pages)

    def _run_onnx_dla(
        self,
        image: np.ndarray,
        page: pymupdf.Page,
        page_num: int,
    ) -> List[Region]:
        """Run the configured export contract and convert pixels to PDF points."""
        if hasattr(self, "_torch_layout"):
            return self._torch_layout.predict(image, page, page_num)
        tensor, geometry = self.adapter.prepare(image)
        inputs = self._session.get_inputs()
        if len(inputs) != 1:
            raise ValueError("Layout export requires an adapter for its extra input tensors")
        values = self._session.run(None, {inputs[0].name: tensor})
        outputs = {output.name: value for output, value in zip(self._session.get_outputs(), values)}
        detections = self.adapter.decode(outputs, geometry)
        regions = []
        for index, detection in enumerate(detections, 1):
            raw_label = self.class_names[detection.class_id]
            label = self.label_map.get(raw_label, raw_label).lower().replace("-", "_")
            c_type = CLASS_TO_CONTENT_TYPE.get(label, ContentType.UNKNOWN)
            bb = image_box_to_pdf(detection.bbox, image.shape, page)
            regions.append(Region(
                region_id=f"p{page_num}_dla_{index}", bbox=BoundingBox(
                    x0=bb[0], y0=bb[1], x1=bb[2], y1=bb[3]),
                region_type=label, content_type=c_type, label=label,
                category_id=detection.class_id, dla_score=detection.score,
                confidence=detection.score, confidence_source="layout_model", quality_score=None, source="ocr",
                reading_order=index,
            ))
        return regions

    def _parse_detections(
        self,
        preds: np.ndarray,
        scale: float,
        pad_x: int,
        pad_y: int,
        w_orig: int,
        h_orig: int,
    ) -> Tuple[List[List[float]], List[float], List[int]]:
        """Parses model output tensor with NMS into bounding boxes."""
        preds = np.squeeze(preds)
        if preds.shape[0] < preds.shape[1] and preds.shape[0] < 20:
            preds = preds.T

        boxes, scores, class_ids = [], [], []
        for row in preds:
            cx, cy, w, h = row[:4]
            cls_scores = row[4:]
            cid = int(np.argmax(cls_scores))
            score = float(cls_scores[cid])

            if score < self.conf_threshold:
                continue

            # Convert to original image coordinates
            x0 = (cx - w / 2 - pad_x) / scale
            y0 = (cy - h / 2 - pad_y) / scale
            x1 = (cx + w / 2 - pad_x) / scale
            y1 = (cy + h / 2 - pad_y) / scale

            x0 = max(0.0, min(float(w_orig), x0))
            y0 = max(0.0, min(float(h_orig), y0))
            x1 = max(0.0, min(float(w_orig), x1))
            y1 = max(0.0, min(float(h_orig), y1))

            if x1 > x0 and y1 > y0:
                boxes.append([x0, y0, x1, y1])
                scores.append(score)
                class_ids.append(cid)

        if not boxes:
            return [], [], []

        # OpenCV NMS
        cv_boxes = [[int(b[0]), int(b[1]), int(b[2] - b[0]), int(b[3] - b[1])] for b in boxes]
        indices = cv2.dnn.NMSBoxes(cv_boxes, scores, self.conf_threshold, self.iou_threshold)
        final_boxes, final_scores, final_cids = [], [], []
        for i in indices:
            idx = i if isinstance(i, int) else i[0]
            final_boxes.append(boxes[idx])
            final_scores.append(scores[idx])
            final_cids.append(class_ids[idx])

        return final_boxes, final_scores, final_cids

    def _run_geometric_dla(
        self,
        page: pymupdf.Page,
        page_num: int,
        total_pages: int = 1,
    ) -> List[Region]:
        """
        Fast, robust geometric & semantic layout classifier.
        Doesn't use fixed hardcoded strings. Dynamically detects:
        - Running headers and footers based on geometric position, font hierarchy, and margin bounds.
        - Page numbers based on standalone digits at header/footer bands.
        - Captions based on linguistic indicators (Hình, Bảng, Figure, Table).
        - Section headings and titles based on font sizes and numbering patterns.
        - Tables and figures based on vector drawings and embedded image rects.
        """
        page_w = page.rect.width
        page_h = page.rect.height
        regions: List[Region] = []
        region_counter = 0

        # Margin boundaries calculated dynamically based on page dimensions
        header_band_y = page_h * 0.10
        footer_band_y = page_h * 0.90

        # 1. Identify vector tables and figures
        table_rects = []
        try:
            tabs = page.find_tables()
            for t in tabs:
                tb = t.bbox
                bb = BoundingBox(x0=round(tb[0], 2), y0=round(tb[1], 2), x1=round(tb[2], 2), y1=round(tb[3], 2))
                if bb.area > 500:
                    region_counter += 1
                    table_rects.append(bb)
                    regions.append(Region(
                        region_id=f"p{page_num}_dla_{region_counter}",
                        bbox=bb,
                        content_type=ContentType.TABLE,
                        label="table",
                        category_id=6,
                        dla_score=0.95,
                        confidence=0.95,
                            confidence_source="heuristic",
                        source="vector",
                        reading_order=region_counter,
                    ))
        except Exception:
            pass

        # 2. Identify embedded images (FIGURE)
        for img_info in page.get_images():
            try:
                for img_rect in page.get_image_rects(img_info[0]):
                    bb = BoundingBox(x0=round(img_rect.x0, 2), y0=round(img_rect.y0, 2), x1=round(img_rect.x1, 2), y1=round(img_rect.y1, 2))
                    # Avoid noise icons (min 50x50pt) and avoid full-page background scans
                    if bb.area >= 2500 and (bb.area / (page_w * page_h)) < 0.85:
                        # Avoid duplicates overlapping with tables
                        if not any(bb.overlap_ratio(tr) > 0.6 for tr in table_rects):
                            region_counter += 1
                            regions.append(Region(
                                region_id=f"p{page_num}_dla_{region_counter}",
                                bbox=bb,
                                content_type=ContentType.FIGURE,
                                label="figure",
                                category_id=7,
                                dla_score=0.90,
                                confidence=0.90,
                            confidence_source="heuristic",
                                source="image",
                                reading_order=region_counter,
                            ))
            except Exception:
                pass

        # 3. Analyze text blocks (Titles, Headers, Footers, Captions, Section Headers, Body Text)
        raw_blocks = page.get_text("blocks")
        # Collect font stats to establish dynamic hierarchy
        font_sizes: List[float] = []
        try:
            dict_data = page.get_text("dict")
            for block in dict_data.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("text", "").strip():
                            font_sizes.append(span.get("size", 10.0))
        except Exception:
            pass

        base_font_size = float(np.median(font_sizes)) if font_sizes else 11.0

        for b in raw_blocks:
            if b[6] != 0:  # non-text
                continue
            text = b[4].strip()
            if not text:
                continue

            bb = BoundingBox(x0=round(b[0], 2), y0=round(b[1], 2), x1=round(b[2], 2), y1=round(b[3], 2))

            # Skip if inside table area
            if any(bb.overlap_ratio(tr) > 0.7 for tr in table_rects):
                continue

            # Classify semantic category
            c_type, label, cid = self._classify_text_block(
                text=text,
                bbox=bb,
                page_h=page_h,
                page_w=page_w,
                page_num=page_num,
                base_font_size=base_font_size,
                header_band_y=header_band_y,
                footer_band_y=footer_band_y,
            )

            region_counter += 1
            regions.append(Region(
                region_id=f"p{page_num}_dla_{region_counter}",
                bbox=bb,
                content_type=c_type,
                label=label,
                category_id=cid,
                dla_score=0.88,
                confidence=0.90,
                            confidence_source="heuristic",
                source="native",
                reading_order=region_counter,
            ))

        return regions

    def _classify_text_block(
        self,
        text: str,
        bbox: BoundingBox,
        page_h: float,
        page_w: float,
        page_num: int,
        base_font_size: float,
        header_band_y: float,
        footer_band_y: float,
    ) -> Tuple[ContentType, str, int]:
        """
        Propose a content role; running furniture needs document evidence.

        The old 10% rule below is available only for explicitly reproducing
        the frozen V6.1 baseline. It is disabled in normal processing.
        """
        clean_text = " ".join(text.split())
        line_count = len(text.splitlines())

        if self.legacy_furniture:
            page_number = bool(re.match(r"^(\d+|[ivxlcdm]+|\btrang\s+\d+(\s*\/\s*\d+)?\b)$", clean_text, re.I))
        else:
            page_number = False  # Pagination, too, is resolved after all pages.
        if page_number:
            if bbox.y1 <= header_band_y or bbox.y0 >= footer_band_y:
                return ContentType.PAGE_NUMBER, "page_number", 5

        if self.legacy_furniture:
            if bbox.y1 <= header_band_y and line_count <= 2 and page_num > 1:
                if not re.match(r"^\s*(\d+(\.\d+)*\s*[\.\-]|[IVXLCDM]+\s*[\.\-])", clean_text):
                    return ContentType.HEADER, "header", 3
            if bbox.y0 >= footer_band_y and line_count <= 2:
                return ContentType.FOOTER, "footer", 4

        # 4. Caption (Begins with Figure/Table/Hình/Bảng/Biểu đồ)
        if re.match(r"^(hình|bảng|ảnh|sơ đồ|biểu đồ|figure|table|fig\.)\s+\d+[:\.\-]", clean_text, re.IGNORECASE):
            return ContentType.CAPTION, "caption", 8

        # 5. Formula / Equation
        if re.search(r"(=|\+|-|\*|/|sum|int)\s*\(\d+\)$", clean_text) or re.match(r"^\s*\$\$.*\$\$\s*$", clean_text):
            return ContentType.FORMULA, "formula", 10

        # 6. Title / Major Header (e.g. Page 1 big title, or all-caps bold short header)
        if page_num == 1 and bbox.y0 < page_h * 0.35 and len(clean_text) < 120 and line_count <= 3:
            if clean_text.isupper() or len(clean_text.split()) < 15:
                return ContentType.TITLE, "title", 0

        # 7. Section Header (e.g., "1. Giới thiệu", "II. Thực nghiệm", "1.2. Phương pháp")
        if re.match(r"^\s*(\d+(\.\d+)*|[A-Z]\.|[IVXLCDM]+\.|\bchương\b|\bphần\b|\bmục\b)\s+[^\n]+", clean_text, re.IGNORECASE):
            if line_count <= 2 and len(clean_text) < 150:
                return ContentType.SECTION_HEADER, "section_header", 1

        # 8. List
        if re.match(r"^\s*([\-\*•–\+]|\(\d+\)|\d+\))\s+", text):
            return ContentType.LIST, "list", 9

        # Default: Standard Body Paragraph
        return ContentType.TEXT, "text", 2


# Backward compatibility alias (Proposal R4)
DLAEngine = HeuristicLayoutClassifier

from typing import List, Dict, Any, Optional
import numpy as np
import unicodedata
from src.ocr.infrastructure.ocr.primary_engine import BaseOCREngine

class RapidOCREngine(BaseOCREngine):
    """
    RapidOCR ONNXRuntime backend optimized for CPU execution.
    Maintains a singleton engine instance to prevent redundant model reloading.
    """
    _instance: Optional["RapidOCREngine"] = None
    _engine: Optional[Any] = None
    _threads: Optional[int] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(RapidOCREngine, cls).__new__(cls)
        return cls._instance

    def __init__(self, **kwargs):
        threads = kwargs.get("num_threads")
        if self._engine is None or threads != self._threads:
            # Initialize ONNXRuntime RapidOCR
            self._engine = self._make_engine(threads)
            self._threads = threads

    @staticmethod
    def _make_engine(threads):
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR(intra_op_num_threads=threads, inter_op_num_threads=1) if threads else RapidOCR()

    def predict_image(self, img_bgr: np.ndarray) -> List[Dict[str, Any]]:
        """
        Runs OCR on a BGR image and returns bounding boxes [x0, y0, x1, y1], text, and confidence.
        """
        if img_bgr is None or img_bgr.size == 0:
            return []

        h, w = img_bgr.shape[:2]
        if h < 16 or w < 16:
            return []

        try:
            raw_results, _ = self._engine(img_bgr)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("RapidOCR prediction failed: %s. Re-initializing engine for retry.", exc)
            try:
                self._engine = self._make_engine(self._threads)
                raw_results, _ = self._engine(img_bgr)
            except Exception as retry_exc:
                logging.getLogger(__name__).error("RapidOCR retry also failed: %s", retry_exc)
                return []
        if not raw_results:
            return []

        formatted: List[Dict[str, Any]] = []
        for item in raw_results:
            # item is: [box_points, text, confidence]
            box_points = item[0]
            raw_text = item[1]
            conf = float(item[2])

            xs = [pt[0] for pt in box_points]
            ys = [pt[1] for pt in box_points]
            x0 = min(xs)
            y0 = min(ys)
            x1 = max(xs)
            y1 = max(ys)

            norm_text = unicodedata.normalize("NFC", raw_text).strip()
            if not norm_text:
                continue

            formatted.append({
                "bbox": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
                "text": norm_text,
                "text_raw": raw_text,
                "correction_source": "unicode_nfc_and_strip" if raw_text != norm_text else None,
                "confidence": round(conf, 4)
            })

        return formatted

    def predict_batch(self, images: List[np.ndarray]) -> List[List[Dict[str, Any]]]:
        """
        Runs sequential or batched prediction over a list of images.
        """
        results = []
        for img in images:
            results.append(self.predict_image(img))
        return results

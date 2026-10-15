from typing import List, Dict, Any, Optional, Tuple
import numpy as np
import cv2
from PIL import Image
import unicodedata
import math
import os
from pathlib import Path
import yaml
from vietocr.tool.config import Cfg
from vietocr.tool.predictor import Predictor
from src.ocr.infrastructure.ocr.primary_engine import BaseOCREngine
from src.ocr.runtime.paths import models_dir
from src.ocr.runtime.models import model_file_sha256
from src.ocr.infrastructure.postprocessing.numeric_guard import preserves_numeric_evidence


def RapidOCR(*args, **kwargs):
    """Legacy factory, loaded only when a direct detector was not injected."""
    from rapidocr_onnxruntime import RapidOCR as LegacyRapidOCR
    return LegacyRapidOCR(*args, **kwargs)

class VietnameseSeq2SeqEngine(BaseOCREngine):
    """
    High-accuracy, fast Vietnamese OCR engine:
    - Text box detection using RapidOCR ONNXRuntime detector (~0.5s)
    - Batch text recognition using VietOCR vgg_seq2seq (~0.09s/line) on CPU
    - Returns VietOCR's own recognition probability for each recognized line
    - Singleton pattern to prevent repeated weight reloads
    """
    _instance: Optional["VietnameseSeq2SeqEngine"] = None
    _detector: Optional[Any] = None
    _recognizer: Optional[Predictor] = None
    _device: Optional[str] = None
    _model_signature: Optional[Tuple[Any, ...]] = None
    _detector_threads: Optional[int] = None
    batch_size: int = 16

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(VietnameseSeq2SeqEngine, cls).__new__(cls)
        return cls._instance

    def __init__(self, batch_size: int = 16, device: Optional[str] = None, num_threads: Optional[int] = None,
                 detector=None):
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        self.batch_size = batch_size

        if detector is not None:
            self._detector = detector
            self._detector_threads = num_threads
        elif self._detector is None or num_threads != self._detector_threads or getattr(type(self._detector), "METHOD", None):
            self._detector = RapidOCR(intra_op_num_threads=num_threads, inter_op_num_threads=1) if num_threads else RapidOCR()
            self._detector_threads = num_threads

        model_dir = Path(os.environ.get("VIETOCR_MODEL_DIR") or models_dir() / "vietocr").expanduser().resolve()
        base_config = model_dir / "base.yml"
        seq2seq_config = model_dir / "vgg-seq2seq.yml"
        weights = model_dir / "vgg_seq2seq.pth"
        signature = (str(model_dir), *(model_file_sha256(path) for path in (base_config, seq2seq_config, weights)))
        # Device=None preserves the loaded device. Replacing persistent model
        # files or changing the model directory must reload the singleton too,
        # otherwise cache provenance would identify weights we never used.
        requested_device = device or self._device or "cpu"
        if (self._recognizer is None or requested_device != self._device
                or signature != self._model_signature):
            if base_config.is_file() and seq2seq_config.is_file() and weights.is_file():
                with base_config.open(encoding="utf-8") as stream:
                    local_config = yaml.safe_load(stream)
                with seq2seq_config.open(encoding="utf-8") as stream:
                    local_config.update(yaml.safe_load(stream))
                local_config["weights"] = str(weights)
                cfg = Cfg(local_config)
            else:
                cfg = Cfg.load_config_from_name("vgg_seq2seq")
            cfg["device"] = requested_device
            type(self)._recognizer = Predictor(cfg)
            type(self)._device = requested_device
            type(self)._model_signature = signature

    @staticmethod
    def _guard_repetition(text: str) -> Tuple[str, bool]:
        """
        VietOCR Repetition Guard (spec §4.2):
        Detects attention loops where 1 word or an n-word phrase (up to 4 words)
        repeats >= 3 times consecutively.
        Truncates the loop and marks as GRAPHIC_NOISE if the remaining string is trivial.
        Returns: (cleaned_text, is_graphic_noise)
        """
        words = text.split()
        if not words:
            return "", True

        had_repetition = False
        best_i = len(words)
        for k in range(1, 5):
            for i in range(len(words)):
                if i + 3 * k <= len(words):
                    pattern = [w.lower() for w in words[i : i + k]]
                    rep = 1
                    while i + (rep + 1) * k <= len(words):
                        next_chunk = [w.lower() for w in words[i + rep * k : i + (rep + 1) * k]]
                        if next_chunk == pattern:
                            rep += 1
                        else:
                            break
                    if rep >= 3:
                        if i + k < best_i:
                            best_i = i + k
                            had_repetition = True
                        break

        truncated_words = words[:best_i] if had_repetition else words
        cleaned = " ".join(truncated_words).strip()
        is_graphic_noise = False
        if had_repetition:
            meaningful_chars = [c for c in cleaned if c.isalnum()]
            if len(meaningful_chars) <= 8 or len(truncated_words) <= 2:
                is_graphic_noise = True

        return cleaned, is_graphic_noise

    def predict_image(self, img_bgr: np.ndarray) -> List[Dict[str, Any]]:
        """
        Detects text bounding boxes and runs batch recognition with vgg_seq2seq.
        """
        if img_bgr is None or img_bgr.size == 0:
            return []

        h, w = img_bgr.shape[:2]
        if h < 16 or w < 16:
            return []

        # RapidOCR is used only for bounding boxes. Running its recognizer here
        # would duplicate the VietOCR pass and attach confidence to the wrong text.
        try:
            raw_det, _ = self._detector(img_bgr, use_cls=False, use_rec=False)
        except Exception:
            if getattr(type(self._detector), "METHOD", None):
                raise  # Direct detector failures must not look like blank pages.
            return []
        if not raw_det:
            return []

        boxes_and_crops = []
        h, w = img_bgr.shape[:2]

        for item in raw_det:
            # Handle both full recognition format [polygon, text, conf] and detection-only format polygon
            if isinstance(item, (list, tuple)) and len(item) in (2, 3) and not isinstance(item[1], (list, tuple, np.ndarray)):
                box_points = item[0]
            else:
                box_points = item

            try:
                if isinstance(box_points, np.ndarray):
                    xs = [float(x) for x in box_points[:, 0]]
                    ys = [float(y) for y in box_points[:, 1]]
                else:
                    xs = [float(pt[0]) for pt in box_points]
                    ys = [float(pt[1]) for pt in box_points]
            except Exception:
                continue

            x0 = max(0, int(min(xs)) - 2)
            y0 = max(0, int(min(ys)) - 2)
            x1 = min(w, int(max(xs)) + 2)
            y1 = min(h, int(max(ys)) + 2)

            crop = img_bgr[y0:y1, x0:x1]
            if crop.size == 0 or crop.shape[0] < 4 or crop.shape[1] < 4:
                continue

            crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(crop_rgb)

            boxes_and_crops.append({
                "bbox": [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))],
                "pil_crop": pil_img,
            })

        if not boxes_and_crops:
            return []

        # 2. Bound the number of lines passed to VietOCR. Its predict_batch
        # buckets crops by width but does not impose a maximum batch size.
        # Nearby aspect ratios share resized widths, so group them before
        # chunking to avoid turning each bounded batch into many single lines.
        # Scatter predictions back to detection order below.
        pil_crops = [bc["pil_crop"] for bc in boxes_and_crops]
        recognition_order = sorted(range(len(pil_crops)), key=lambda i: pil_crops[i].width / pil_crops[i].height)
        recognized_texts, recognized_probs = [""] * len(pil_crops), [0.0] * len(pil_crops)
        pending = [recognition_order[start:start + self.batch_size] for start in range(0, len(pil_crops), self.batch_size)]
        while pending:
            indices = pending.pop(0)
            chunk = [pil_crops[index] for index in indices]
            try:
                texts, probs = self._recognizer.predict_batch(chunk, return_prob=True)
                if len(texts) != len(chunk) or len(probs) != len(chunk):
                    raise ValueError("VietOCR batch result does not match the crop count")
            except Exception as exc:
                if "out of memory" in str(exc).lower():
                    if len(indices) == 1:
                        raise RuntimeError("VietOCR OOM at batch 1; checkpoint/restart the stage or use more VRAM") from exc
                    smaller = max(1, len(indices) // 2)
                    self.batch_size = min(self.batch_size, smaller)
                    pending[:0] = [indices[i:i + smaller] for i in range(0, len(indices), smaller)]
                    continue
                # Retry only this chunk, preserving successful earlier batches
                # and the association of each line/probability with its box.
                predictions = [self._recognizer.predict(img, return_prob=True) for img in chunk]
                texts = [prediction[0] for prediction in predictions]
                probs = [prediction[1] for prediction in predictions]
            for index, text, probability in zip(indices, texts, probs):
                recognized_texts[index] = text
                recognized_probs[index] = probability

        # 3. Format results
        results = []
        for idx, item in enumerate(boxes_and_crops):
            raw_text = recognized_texts[idx] if idx < len(recognized_texts) else ""
            nfc_text = unicodedata.normalize("NFC", raw_text)
            norm_text = nfc_text.strip()
            if not norm_text:
                continue
            corrections = []
            if nfc_text != raw_text:
                corrections.append("unicode_nfc")
            if norm_text != nfc_text:
                corrections.append("trim_whitespace")

            # In VietOCR, replace common question marks representing soft hyphens or broken dashes
            clean_text = norm_text.replace(" ? ", " - ")
            cleanup_corrections = []
            if clean_text != norm_text:
                cleanup_corrections.append("vietocr_dash_repair")

            # Apply repetition guard
            before_guard = clean_text
            clean_text, is_graphic_noise = self._guard_repetition(before_guard)
            if clean_text != before_guard:
                cleanup_corrections.append("vietocr_repetition_guard")
            # Repeated numeric columns can be valid financial evidence. A
            # cleanup candidate that changes protected numbers is rejected,
            # retained verbatim after NFC, and surfaced for manual review.
            numeric_cleanup_rejected = not preserves_numeric_evidence(norm_text, clean_text)
            if numeric_cleanup_rejected:
                clean_text, is_graphic_noise = norm_text, False
            else:
                corrections.extend(cleanup_corrections)
            if is_graphic_noise or not clean_text:
                continue

            probability = float(recognized_probs[idx]) if idx < len(recognized_probs) else 0.0
            if not math.isfinite(probability):
                probability = 0.0

            results.append({
                "bbox": [round(c, 2) for c in item["bbox"]],
                "text": clean_text,
                "text_raw": raw_text,
                "correction_source": "+".join(corrections) if corrections else None,
                "numeric_cleanup_rejected": numeric_cleanup_rejected,
                "needs_review": numeric_cleanup_rejected,
                "review_reason": "numeric_cleanup_rejected" if numeric_cleanup_rejected else None,
                "confidence": round(max(0.0, min(1.0, probability)), 4)
            })

        return results

    def predict_batch(self, images: List[np.ndarray]) -> List[List[Dict[str, Any]]]:
        return [self.predict_image(img) for img in images]

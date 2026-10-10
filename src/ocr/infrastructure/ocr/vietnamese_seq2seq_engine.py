from typing import List, Dict, Any, Optional, Tuple
import numpy as np
import cv2
from PIL import Image
import unicodedata
import math
import os
from pathlib import Path
import yaml
from rapidocr_onnxruntime import RapidOCR
from vietocr.tool.config import Cfg
from vietocr.tool.predictor import Predictor
from src.ocr.infrastructure.ocr.primary_engine import BaseOCREngine

class VietnameseSeq2SeqEngine(BaseOCREngine):
    """
    High-accuracy, fast Vietnamese OCR engine:
    - Text box detection using RapidOCR ONNXRuntime detector (~0.5s)
    - Batch text recognition using VietOCR vgg_seq2seq (~0.09s/line) on CPU
    - Returns VietOCR's own recognition probability for each recognized line
    - Singleton pattern to prevent repeated weight reloads
    """
    _instance: Optional["VietnameseSeq2SeqEngine"] = None
    _detector: Optional[RapidOCR] = None
    _recognizer: Optional[Predictor] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(VietnameseSeq2SeqEngine, cls).__new__(cls)
        return cls._instance

    def __init__(self, batch_size: int = 16):
        self.batch_size = batch_size

        if self._detector is None:
            self._detector = RapidOCR()

        if self._recognizer is None:
            model_dir = Path(os.environ.get(
                "VIETOCR_MODEL_DIR",
                Path(__file__).resolve().parents[4] / "models" / "vietocr",
            ))
            base_config = model_dir / "base.yml"
            seq2seq_config = model_dir / "vgg-seq2seq.yml"
            weights = model_dir / "vgg_seq2seq.pth"
            if base_config.is_file() and seq2seq_config.is_file() and weights.is_file():
                with base_config.open(encoding="utf-8") as stream:
                    local_config = yaml.safe_load(stream)
                with seq2seq_config.open(encoding="utf-8") as stream:
                    local_config.update(yaml.safe_load(stream))
                local_config["weights"] = str(weights)
                cfg = Cfg(local_config)
            else:
                cfg = Cfg.load_config_from_name("vgg_seq2seq")
            cfg["device"] = "cpu"
            self._recognizer = Predictor(cfg)

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
            return []
        if not raw_det:
            return []

        boxes_and_crops = []
        h, w = img_bgr.shape[:2]

        for box_points in raw_det:

            xs = [pt[0] for pt in box_points]
            ys = [pt[1] for pt in box_points]
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

        # 2. Batch Recognition with vgg_seq2seq
        pil_crops = [bc["pil_crop"] for bc in boxes_and_crops]
        try:
            recognized_texts, recognized_probs = self._recognizer.predict_batch(
                pil_crops, return_prob=True
            )
        except Exception:
            # Fallback to sequential predict if batch fails
            predictions = [self._recognizer.predict(img, return_prob=True) for img in pil_crops]
            recognized_texts = [prediction[0] for prediction in predictions]
            recognized_probs = [prediction[1] for prediction in predictions]

        # 3. Format results
        results = []
        for idx, item in enumerate(boxes_and_crops):
            raw_text = recognized_texts[idx] if idx < len(recognized_texts) else ""
            norm_text = unicodedata.normalize("NFC", raw_text).strip()
            if not norm_text:
                continue

            # In VietOCR, replace common question marks representing soft hyphens or broken dashes
            clean_text = norm_text.replace(" ? ", " - ")

            # Apply repetition guard
            clean_text, is_graphic_noise = self._guard_repetition(clean_text)
            if is_graphic_noise or not clean_text:
                continue

            probability = float(recognized_probs[idx]) if idx < len(recognized_probs) else 0.0
            if not math.isfinite(probability):
                probability = 0.0

            results.append({
                "bbox": [round(c, 2) for c in item["bbox"]],
                "text": clean_text,
                "confidence": round(max(0.0, min(1.0, probability)), 4)
            })

        return results

    def predict_batch(self, images: List[np.ndarray]) -> List[List[Dict[str, Any]]]:
        return [self.predict_image(img) for img in images]

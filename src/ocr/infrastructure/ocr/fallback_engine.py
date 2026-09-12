from typing import List, Dict, Any, Optional
import numpy as np
import unicodedata
from src.ocr.infrastructure.ocr.vietnamese_seq2seq_engine import VietnameseSeq2SeqEngine
from src.ocr.infrastructure.quality.ocr_quality import OCRQualityEvaluator
from src.ocr.infrastructure.ocr.candidate_scorer import CandidateScorer

class FallbackEngine:
    """
    Adaptive fallback engine that processes only targeted failed regions/lines.
    Applies image enhancement (e.g. adaptive contrast/thresholding) or upscaling,
    evaluates quality, and returns improved results if available.
    """
    def __init__(self, upscale_dpi: int = 250):
        self.upscale_dpi = upscale_dpi
        self.ocr_engine = VietnameseSeq2SeqEngine()
        self.quality_evaluator = OCRQualityEvaluator()
        self.scorer = CandidateScorer()

    @staticmethod
    def _enhance_contrast(crop_bgr: np.ndarray) -> Optional[np.ndarray]:
        if crop_bgr is None or crop_bgr.size == 0:
            return None
        try:
            import cv2
            gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)
            return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
        except Exception:
            return None

    def reocr_crop(
        self,
        crop_bgr: np.ndarray,
        original_text: str,
        original_conf: float
    ) -> Dict[str, Any]:
        """
        Re-OCRs a cropped image region using up to 2-3 candidates (Codex v3.1 Section 6):
        - Candidate A: original primary OCR result
        - Candidate B: enhanced contrast crop (CLAHE)
        - Candidate C: direct high-DPI crop
        Returns the best candidate selected by CandidateScorer.
        """
        if crop_bgr is None or crop_bgr.size == 0:
            return {
                "text": original_text,
                "confidence": original_conf,
                "improved": False
            }

        orig_cand_score = self.scorer.score_candidate(original_text, original_conf)
        best_cand = {
            "text": original_text,
            "confidence": original_conf,
            "score": orig_cand_score,
            "improved": False
        }

        # Candidate C: High-DPI crop prediction
        cands_c = self.ocr_engine.predict_image(crop_bgr)
        if cands_c:
            text_c = " ".join(c["text"] for c in cands_c).strip()
            conf_c = sum(c["confidence"] for c in cands_c) / len(cands_c)
            score_c = self.scorer.score_candidate(text_c, conf_c)
            if score_c > best_cand["score"]:
                best_cand = {
                    "text": text_c,
                    "confidence": conf_c,
                    "score": score_c,
                    "improved": True
                }

        # Candidate B: Enhanced contrast crop
        enh_crop = self._enhance_contrast(crop_bgr)
        if enh_crop is not None:
            cands_b = self.ocr_engine.predict_image(enh_crop)
            if cands_b:
                text_b = " ".join(c["text"] for c in cands_b).strip()
                conf_b = sum(c["confidence"] for c in cands_b) / len(cands_b)
                score_b = self.scorer.score_candidate(text_b, conf_b)
                if score_b > best_cand["score"]:
                    best_cand = {
                        "text": text_b,
                        "confidence": conf_b,
                        "score": score_b,
                        "improved": True
                    }

        orig_q_score, _, _ = self.quality_evaluator.evaluate_ocr_result(original_text, original_conf)
        best_q_score, _, _ = self.quality_evaluator.evaluate_ocr_result(best_cand["text"], best_cand["confidence"])

        return {
            "text": best_cand["text"],
            "confidence": best_cand["confidence"],
            "quality_score": best_q_score if best_cand["improved"] else orig_q_score,
            "candidate_score": best_cand["score"],
            "improved": best_cand["improved"]
        }

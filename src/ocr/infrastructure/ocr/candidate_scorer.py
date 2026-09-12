import re
import unicodedata
from typing import Dict, Any, Optional
from src.ocr.infrastructure.quality.vietnamese_quality import VietnameseQualityEvaluator

class CandidateScorer:
    """
    Computes a multi-criteria score to select the best candidate line among primary and fallback OCR results.
    Formula (Codex v3 Section 5.3):
      candidate_score =
          w_conf * OCR_confidence
        + w_vn * Vietnamese_quality
        + w_lex * lexical_consistency
        + w_geom * geometry_consistency
        + w_char * character_validity
    """
    def __init__(
        self,
        w_conf: float = 0.20,
        w_vn: float = 0.35,
        w_lex: float = 0.20,
        w_geom: float = 0.10,
        w_char: float = 0.15
    ):
        self.w_conf = w_conf
        self.w_vn = w_vn
        self.w_lex = w_lex
        self.w_geom = w_geom
        self.w_char = w_char
        self.vn_evaluator = VietnameseQualityEvaluator()

    def score_candidate(
        self,
        text: str,
        confidence: float,
        expected_width: Optional[float] = None,
        actual_width: Optional[float] = None
    ) -> float:
        clean = text.strip()
        if not clean:
            return 0.0

        # 1. OCR Confidence
        c_conf = max(0.0, min(1.0, confidence))

        # 2. Vietnamese Quality
        vn_score, _, _ = self.vn_evaluator.evaluate_line(clean)

        # 3. Lexical Consistency
        # Measures whether words resemble valid words rather than random garbage
        words = clean.split()
        if words:
            valid_words = 0
            for w in words:
                # Letters, common digits, or punctuation
                if re.match(r"^[a-zA-Z0-9àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđĐ\.\,\-\:\%\/\(\)]+$", w):
                    valid_words += 1
            lex_score = valid_words / len(words)
        else:
            lex_score = 0.0

        # 4. Character Validity (printable, no replacement chars, no isolated diacritics)
        char_valid = 1.0
        if "\ufffd" in clean:
            char_valid -= 0.5
        non_printable = sum(1 for c in clean if not c.isprintable())
        if non_printable > 0:
            char_valid -= min(0.5, non_printable * 0.1)
        char_score = max(0.0, char_valid)

        # 5. Geometry Consistency
        if expected_width and actual_width and expected_width > 0:
            ratio = min(expected_width, actual_width) / max(expected_width, actual_width)
            geom_score = ratio
        else:
            geom_score = 1.0

        total = (
            self.w_conf * c_conf
            + self.w_vn * vn_score
            + self.w_lex * lex_score
            + self.w_geom * geom_score
            + self.w_char * char_score
        )
        return round(max(0.0, min(1.0, total)), 4)

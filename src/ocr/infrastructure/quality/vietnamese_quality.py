import re
import unicodedata
from typing import Tuple, List

class VietnameseQualityEvaluator:
    """
    Evaluates Vietnamese text lines for OCR corruption, diacritic anomalies,
    replacement symbols, and encoding errors.
    NEVER modifies text; strictly used for quality scoring and routing.
    """
    def __init__(self):
        # Valid Vietnamese vowels with diacritics
        self.vn_vowels = set("aăâeêioôơuưyAĂÂEÊIOÔƠUƯY"
                             "àáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩị"
                             "òóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ"
                             "ÀÁẢÃẠẰẮẲẴẶẦẤẨẪẬÈÉẺẼẸỀẾỂỄỆÌÍỈĨỊ"
                             "ÒÓỎÕỌỒỐỔỖỘỜỚỞỠỢÙÚỦŨỤỪỨỬỮỰỲÝỶỸỴ")

        # Suspicious Scandinavian or unusual accents that occur in bad OCR of Vietnamese
        self.foreign_accent_chars = set("ÅåÆæØøÄäÖöÜüÿËëÏï")

        # Specific unaccented phrases that strongly suggest dropped diacritics in Vietnamese administrative text
        self.unaccented_indicator_regex = re.compile(
            r"\b(cong\s+ty|bao\s+cao|thanh\s+pho|trach\s+nhiem|thanh\s+vien|thuong\s+nien|ke\s+hoach|thuc\s+hien|tai\s+chinh|tong\s+giam\s+doc|giam\s+doc|chu\s+tich|hoi\s+dong|quan\s+tri)\b",
            re.IGNORECASE
        )

        # Suspicious patterns in OCR text (Section 6.3)
        self.suspicious_regexes = [
            (r"[a-zA-Z]\d+[a-zA-Z]", "Digit embedded inside word"),
            (r"\bph[60]\s+H[60]\b", "Digit substitution in 'phố Hồ'"),
            (r"\b[a-zA-Z]+[06]\b", "Digit substitution at word ending"),
            (r"[\"\'][a-zA-Z][\"\']", "Orphaned quotes around character"),
            (r"\bdo\s+14p\b", "Corrupted 'độc lập'"),
            (r"\btv\s+do\b", "Corrupted 'tự do'"),
            (r"\bhnh\s+phtic\b", "Corrupted 'hạnh phúc'"),
            (r"\bthong\s+men\b", "Corrupted 'thường niên'"),
            (r"\b([A-Z]\s+){3,}[A-Z]\b", "Broken spaced characters (e.g. 'C O N G T Y')"),
            (r"\b[a-z]{4,}[A-Z][a-z]{4,}\b", "Merged compound words without space"),
            (r"\ufffd", "Replacement character"),
        ]

    def evaluate_line(self, line: str) -> Tuple[float, bool, List[str]]:
        """
        Evaluates a single line of OCR/native text.
        Returns:
            (quality_score: float, is_suspicious: bool, reasons: List[str])
        """
        clean = line.strip()
        if not clean:
            return 1.0, False, []

        reasons = []
        score = 1.0

        # Check for replacement character \ufffd
        if "\ufffd" in clean:
            reasons.append("Contains replacement character \\ufffd")
            score -= 0.50

        # Check foreign accent characters replacing Vietnamese vowels
        for char in clean:
            if char in self.foreign_accent_chars:
                reasons.append(f"Suspicious foreign character: '{char}'")
                score -= 0.30
                break

        # Check suspicious regexes
        for pattern, reason in self.suspicious_regexes:
            if re.search(pattern, clean, re.IGNORECASE):
                reasons.append(reason)
                score -= 0.25

        # Check for unaccented Vietnamese keywords
        if self.unaccented_indicator_regex.search(clean):
            reasons.append("Unaccented Vietnamese keywords in administrative context")
            score -= 0.35

        # Check for isolated accents (combining diacritics without base characters)
        combining_chars = [c for c in clean if unicodedata.combining(c)]
        # After NFC normalization, combining characters should not be isolated
        nfc_text = unicodedata.normalize("NFC", clean)
        isolated_accents = [c for c in nfc_text if unicodedata.combining(c)]
        if isolated_accents:
            reasons.append("Isolated combining accents detected")
            score -= 0.20

        # Check for severe punctuation noise (e.g. "....,,;;;")
        punct_count = sum(1 for c in clean if unicodedata.category(c).startswith("P"))
        if len(clean) > 5 and punct_count / len(clean) > 0.40:
            reasons.append("Excessive punctuation ratio")
            score -= 0.20

        final_score = max(0.0, min(1.0, round(score, 4)))
        is_suspicious = final_score < 0.80 or len(reasons) > 0

        return final_score, is_suspicious, reasons

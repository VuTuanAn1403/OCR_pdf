import re
import unicodedata
from typing import Dict, Any, List

class NativeQualityEvaluator:
    """
    Evaluates the quality and reliability of native text layers in PDF pages.
    Detects corrupted OCR layers, missing diacritics, broken encoding, and replacement characters.
    """
    def __init__(self):
        # Known Vietnamese marker words (common in official and corporate docs)
        self.vietnamese_indicators = [
            "cộng hòa", "xã hội", "chủ nghĩa", "việt nam", "độc lập", "tự do", "hạnh phúc",
            "báo cáo", "thường niên", "công ty", "cổ phần", "tài chính", "nghị quyết",
            "hội đồng", "quản trị", "giám đốc", "kinh doanh", "thông báo", "ngày", "tháng", "năm"
        ]
        # Corrupted markers specifically observed in bad OCR layers (like CLW)
        self.corrupted_patterns = [
            r"\bdo\s+14p\b",
            r"\btv\s+do\b",
            r"\bhnh\s+phtic\b",
            r"\bthong\s+men\b",
            r"\bczia\b",
            r"\bchieng\s+khocin\b",
            r"\bngvo\b",
            r"\bclu\b",
            r"c[\"\']\.j",
            r"\ufffd"
        ]

    def evaluate_text(self, text: str, meta: Dict[str, Any]) -> float:
        """
        Returns a score between 0.0 (completely unreliable) and 1.0 (highly reliable native text).
        """
        if not text or len(text.strip()) == 0:
            return 0.0

        char_count = len(text.strip())
        if char_count < 20:
            return 0.5  # Too short to judge, needs caution

        score = 1.0
        lower_text = text.lower()

        # 1. Check for replacement characters (\ufffd)
        replacement_count = text.count("\ufffd")
        if replacement_count > 0:
            penalty = min(0.5, (replacement_count / max(1, char_count)) * 50)
            score -= penalty

        # 2. Check for control characters (other than \n, \r, \t)
        control_chars = sum(1 for c in text if unicodedata.category(c).startswith("C") and c not in "\r\n\t")
        if control_chars > 0:
            score -= min(0.4, (control_chars / char_count) * 20)

        # 3. Check for specific corrupted patterns from bad OCR text layers
        for pat in self.corrupted_patterns:
            matches = len(re.findall(pat, lower_text, re.IGNORECASE))
            if matches > 0:
                score -= min(0.35, matches * 0.15)

        # 4. Check for printable characters ratio
        printable_count = sum(1 for c in text if c.isprintable() or c in "\r\n\t")
        printable_ratio = printable_count / char_count
        if printable_ratio < 0.95:
            score -= (0.95 - printable_ratio) * 2

        # 5. Check Vietnamese diacritic sanity if Vietnamese is expected
        # Look for English-like approximations with missing accents in official titles
        if "cong hoa xa" in lower_text or "doc lap - tu do" in lower_text or "bao cao thuong nien" in lower_text:
            # If standard diacritics are absent in these standard phrases, it's an un-accented or damaged layer
            has_proper_diacritics = any(c in text for c in "àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđĐ")
            if not has_proper_diacritics:
                score -= 0.35

        # 6. Check for random single-letter lines or high punctuation noise
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            short_garbage_lines = sum(1 for line in lines if len(line) <= 2 and not line.isdigit())
            if short_garbage_lines / len(lines) > 0.25:
                score -= 0.20

        # 7. Check if page has high image coverage (scanned PDF with cheap OCR layer)
        if meta.get("is_full_page_image", False):
            score -= 0.15

        return max(0.0, min(1.0, round(score, 4)))

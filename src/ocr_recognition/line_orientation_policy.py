"""Conservative selection between horizontal OCR direction hypotheses."""
import re
import unicodedata

from ocr_recognition.hallucination import VN_LEXICON_WORDS, EN_COMMON_WORDS


def numeric_reading(text):
    value = text.strip()
    return bool(re.search(r"\d", value) and re.fullmatch(r"[\d\s.,()%/:+\-−]+", value))


def language_support(text):
    words = re.findall(r"[^\W\d_]+", text.casefold())
    if not words:
        return 0.0
    lexicon = sum(word in VN_LEXICON_WORDS or word in EN_COMMON_WORDS for word in words)
    accented = sum(any(unicodedata.combining(c) for c in unicodedata.normalize("NFD", word))
                   or "đ" in word for word in words if len(word) >= 3)
    return (2 * lexicon + accented) / len(words)


def select_horizontal_direction(baseline, opposite, texts, probabilities, usable):
    """A larger uncalibrated probability alone cannot overturn a readable line."""
    if not usable(baseline):
        return opposite, "baseline_unreadable"
    if not usable(opposite):
        return baseline, "opposite_unreadable"
    if numeric_reading(texts[baseline]) and probabilities[baseline] >= .6:
        return baseline, "preserved_numeric_orientation_ambiguity"
    if language_support(texts[baseline]) > language_support(texts[opposite]):
        return baseline, "preserved_readable_baseline"
    if probabilities[opposite] < probabilities[baseline] + 0.08:
        return baseline, "insufficient_orientation_margin"
    return opposite, "stronger_opposite_reading_requires_review"

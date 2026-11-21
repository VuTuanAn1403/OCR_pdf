"""Whitespace context for tightly cropped transformer recognition inputs."""
import math
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher

from PIL import ImageOps
from ocr_text.numeric_guard import preserves_numeric_evidence, protected_numeric_fragments


METHOD = "vietocr-white-context-v1"
RETRY_METHOD = "vietocr-context-consensus-v1"
CONTEXT_RATIOS = (0.125, 0.2, 0.25, 0.3, 0.4)


def add_line_context(image, ratio=0.2):
    """Keep every source pixel and add context without sampling adjacent lines."""
    padding = max(1, math.ceil(min(image.size) * ratio))
    padded = ImageOps.expand(image, border=padding, fill=(255, 255, 255))
    return padded, {
        "method": METHOD,
        "padding_pixels": [padding] * 4,
        "fill_rgb": [255, 255, 255],
        "source_crop_size": list(image.size),
        "padded_crop_size": list(padded.size),
    }


def _words(text):
    return tuple(re.findall(r"\w+", unicodedata.normalize("NFC", text).casefold()))


def _key(text):
    # Punctuation may vary, but accents and exact numeric formats must agree.
    return _words(text), tuple(protected_numeric_fragments(text))


def _no_new_numbers(original, candidate):
    remaining = iter(protected_numeric_fragments(original))
    return all(any(old == value for old in remaining)
               for value in protected_numeric_fragments(candidate))


def retry_line_context(images, texts, probabilities, recognize):
    """Read weak lines with distinct margins; retain baseline unless views agree.

    This selects a complete model reading, never edits or truncates a string.
    Both readings remain in the audit; numeric disagreements require review.
    """
    result_images, result_texts, result_probs = list(images), list(texts), list(probabilities)
    pending, proposals, contexts = [], [], []
    for index, (image, text, prob) in enumerate(zip(images, texts, probabilities)):
        if not text.strip() or not math.isfinite(float(prob)) or not 0.6 <= float(prob) < 0.9:
            continue
        seen = set()
        for ratio in CONTEXT_RATIOS:
            candidate, context = add_line_context(image, ratio)
            padding = context["padding_pixels"][0]
            if padding in seen:
                continue  # Identical pixels must not count as independent votes.
            seen.add(padding)
            pending.append(index)
            proposals.append(candidate)
            contexts.append(context)
    if not proposals:
        return result_images, result_texts, result_probs, {}
    candidate_texts, candidate_probs = recognize(proposals)
    audits, groups = {}, defaultdict(lambda: defaultdict(list))
    for offset, (index, text, probability, context) in enumerate(
            zip(pending, candidate_texts, candidate_probs, contexts)):
        probability = float(probability)
        probability = probability if math.isfinite(probability) else 0.0
        audit = audits.setdefault(index, {
            "method": RETRY_METHOD, "applied": False,
            "baseline": {"text_raw": texts[index], "confidence": float(probabilities[index])},
            "candidates": [], "selected_candidate": None, "numeric_disagreement": False,
            "disposition": "KEPT_BASELINE",
        })
        candidate_index = len(audit["candidates"])
        audit["candidates"].append({"text_raw": text, "confidence": probability, **context})
        groups[index][_key(text)].append((offset, candidate_index, probability))
    for index, variants in groups.items():
        eligible = []
        for members in variants.values():
            best = max(members, key=lambda value: value[2])
            text = candidate_texts[best[0]]
            if (len(members) >= 2 and best[2] >= max(0.9, float(probabilities[index]) + 0.025)
                    and len(_words(text)) < len(_words(texts[index]))
                    and _no_new_numbers(texts[index], text)):
                baseline_words, candidate_words = _words(texts[index]), _words(text)
                matched = sum(block.size for block in SequenceMatcher(
                    None, baseline_words, candidate_words, autojunk=False).get_matching_blocks())
                # Prefer preserving observed words over introducing a new spelling,
                # even if more contexts repeat the changed accent or word.
                novel_words = len(candidate_words) - matched
                eligible.append((-novel_words, len(members),
                                 sum(row[2] for row in members) / len(members), best))
        if not eligible:
            continue
        _, _, _, (offset, candidate_index, confidence) = max(eligible)
        selected = candidate_texts[offset]
        result_images[index], result_texts[index], result_probs[index] = proposals[offset], selected, confidence
        audits[index].update({
            "applied": True, "selected_candidate": candidate_index,
            "numeric_disagreement": not preserves_numeric_evidence(texts[index], selected),
            "disposition": "SELECTED_MODEL_READING_REQUIRES_REVIEW",
        })
    return result_images, result_texts, result_probs, audits

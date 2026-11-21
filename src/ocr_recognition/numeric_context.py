"""Re-read suspicious repeated-separator groups; never synthesize digits."""
import math
import re

from ocr_recognition.line_context import add_line_context, CONTEXT_RATIOS


INCOMPLETE_GROUP = re.compile(r"[+\-−]?\(?\d{1,3}(?P<s>[.,])\d{3}(?:(?P=s)\d{3})*(?P=s)\d{1,2}\)?")
COMPLETE_GROUP = re.compile(r"[+\-−]?\(?\d{1,3}(?P<s>[.,])\d{3}(?:(?P=s)\d{3})+\)?")


def retry_numeric_context(images, texts, probabilities, recognize):
    """Select a complete OCR reading only when all distinct contexts agree.

    A malformed repeated separator is a retry signal, never a number-repair
    rule. Decimal values, complete groups and short integers are unchanged.
    Competing raw readings remain recorded and always require review.
    """
    out_images, out_texts, out_probs = list(images), list(texts), list(probabilities)
    pending, proposals, contexts = [], [], []
    for index, (image, text, prob) in enumerate(zip(images, texts, probabilities)):
        if not INCOMPLETE_GROUP.fullmatch(text.strip()) or not math.isfinite(float(prob)):
            continue
        seen = set()
        for ratio in CONTEXT_RATIOS:
            crop, context = add_line_context(image, ratio)
            padding = context['padding_pixels'][0]
            if padding in seen:
                continue
            seen.add(padding)
            pending.append(index)
            proposals.append(crop)
            contexts.append(context)
    if not proposals:
        return out_images, out_texts, out_probs, {}
    candidate_texts, candidate_probs = recognize(proposals)
    audits = {}
    for offset, (index, text, prob, context) in enumerate(zip(pending, candidate_texts, candidate_probs, contexts)):
        prob = float(prob)
        prob = prob if math.isfinite(prob) else 0.0
        audit = audits.setdefault(index, dict(method='numeric-context-consensus-v1', applied=False,
            baseline=dict(text_raw=texts[index], confidence=float(probabilities[index])),
            candidates=[], selected_candidate=None, numeric_disagreement=True,
            disposition='KEPT_BASELINE_REQUIRES_REVIEW'))
        audit['candidates'].append(dict(text_raw=text, confidence=float(prob), offset=offset, **context))
    for index, audit in audits.items():
        readings = audit['candidates']
        values = {r['text_raw'].strip() for r in readings}
        if len(readings) < 3 or len(values) != 1:
            continue
        value = next(iter(values))
        old_digits = ''.join(c for c in texts[index] if c.isdigit())
        new_digits = ''.join(c for c in value if c.isdigit())
        # Only complete model predictions with preserved leading digits qualify;
        # changed signs, punctuation and internal digits must keep the baseline.
        original = texts[index].strip().rstrip(')')
        candidate = value.rstrip(')')
        if (not COMPLETE_GROUP.fullmatch(value) or not candidate.startswith(original)
                or value.endswith(')') != texts[index].strip().endswith(')')
                or not new_digits.startswith(old_digits)
                or not all(math.isfinite(r['confidence']) and r['confidence'] >= .9 for r in readings)
                or sum(r['confidence'] for r in readings)/len(readings) < float(probabilities[index])-.02):
            continue
        chosen = max(range(len(readings)), key=lambda k: readings[k]['confidence'])
        reading = readings[chosen]
        out_images[index] = proposals[reading['offset']]
        out_texts[index], out_probs[index] = reading['text_raw'], reading['confidence']
        audit.update(applied=True, selected_candidate=chosen, disposition='SELECTED_MODEL_READING_REQUIRES_REVIEW')
    return out_images, out_texts, out_probs, audits

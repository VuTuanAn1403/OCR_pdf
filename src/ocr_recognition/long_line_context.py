"""Read long lines in observed whitespace partitions without dropping pixels."""
import math

import numpy as np

from ocr_text.numeric_guard import preserves_numeric_evidence


def observed_line_partitions(image):
    """Return contiguous source crops, never character-count-based boxes."""
    width, height = image.size
    target = min(480, max(96, height * 14))
    if width <= target * 1.35:
        return []
    gray = np.asarray(image.convert('L'))
    background = float(np.percentile(gray, 90))
    if background < 150:
        return []
    ink = (gray < background - max(20, background * .12)).sum(axis=0)
    gaps, start = [], None
    for x in range(width + 1):
        blank = x < width and ink[x] == 0
        if blank and start is None:
            start = x
        if not blank and start is not None:
            if x - start >= max(3, math.ceil(height * .12)):
                gaps.append((start + x) // 2)
            start = None
    cuts = [0]
    while width - cuts[-1] > target * 1.35:
        eligible = [x for x in gaps if cuts[-1] + .55 * target <= x <= cuts[-1] + 1.15 * target]
        if not eligible or len(cuts) >= 32:
            return []
        cuts.append(min(eligible, key=lambda x: abs(x - cuts[-1] - target)))
    cuts.append(width)
    return [(image.crop((a, 0, b, height)), [a, 0, b, height]) for a, b in zip(cuts, cuts[1:])]


def retry_long_lines(images, texts, probabilities, recognize, *, skip_indices=()):
    """Keep both hypotheses; choose complete, usable partition readings.

    Each partition covers real pixels and boundaries lie in observed blank
    columns. The minimum segment probability is only a routing score, never
    calibrated line accuracy. A changed numeric reading always needs review.
    """
    pending, crops, boxes = [], [], []
    for index, image in enumerate(images):
        if index in skip_indices:
            continue
        # Preserve confident short readings. The pinned transformer's decoder
        # has a 128-character horizon; a long crop near that limit can be
        # truncated despite a high score and needs an observed partition view.
        if (float(probabilities[index]) >= .9 and len(texts[index].strip()) < 120
                and image.width <= 50 * image.height):
            continue
        for crop, box in observed_line_partitions(image):
            pending.append(index)
            crops.append(crop)
            boxes.append(box)
    if not crops:
        return list(texts), list(probabilities), {}
    candidates, scores = recognize(crops)
    audits = {}
    for index, text, score, box in zip(pending, candidates, scores, boxes):
        score = float(score)
        score = score if math.isfinite(score) else 0.0
        baseline_score = float(probabilities[index])
        baseline_score = baseline_score if math.isfinite(baseline_score) else 0.0
        audit = audits.setdefault(index, dict(method='observed-whitespace-partitions-v1', applied=False,
            baseline=dict(text_raw=texts[index], confidence=baseline_score),
            source_crop_size=list(images[index].size), coordinate_space='oriented_detector_crop_pixels',
            segments=[], all_pixels_preserved=True, confidence_aggregation='minimum_segment_probability'))
        audit['segments'].append(dict(text_raw=text, confidence=float(score), crop_box=box))
    selected_texts, selected_scores = list(texts), list(probabilities)
    for index, audit in audits.items():
        segments = audit['segments']
        if not all(s['text_raw'].strip() and math.isfinite(s['confidence']) and s['confidence'] >= .85
                   for s in segments):
            audit['disposition'] = 'KEPT_BASELINE_REQUIRES_REVIEW'
            continue
        selected_texts[index] = ' '.join(s['text_raw'].strip() for s in segments)
        selected_scores[index] = min(s['confidence'] for s in segments)
        audit.update(applied=True, disposition='SELECTED_PARTITION_READINGS_REQUIRES_REVIEW',
                     numeric_disagreement=not preserves_numeric_evidence(texts[index], selected_texts[index]))
    return selected_texts, selected_scores, audits

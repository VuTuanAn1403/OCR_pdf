import re
import unicodedata
import difflib
from typing import List, Dict, Any, Tuple


def _strip_vietnamese_accents(s: str) -> str:
    """Strips Vietnamese tone marks and combining accents while keeping base consonants/vowels."""
    s = s.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _word_sim(w1: str, w2: str) -> float:
    """Calculates similarity between two individual words/tokens."""
    raw = difflib.SequenceMatcher(None, w1, w2).ratio()
    base = difflib.SequenceMatcher(
        None, _strip_vietnamese_accents(w1), _strip_vietnamese_accents(w2)
    ).ratio()
    if base >= 0.999:
        return max(0.85, 0.3 * raw + 0.7 * base)
    return max(raw, 0.35 * raw + 0.65 * base)


def calc_vietnamese_similarity(cand: str, kw: str) -> float:
    """
    Computes a composite similarity ratio tailored for Vietnamese OCR text.
    Enforces a strict word-level gate so that phrases with completely different
    constituent words (e.g. 'dữ liệu không' vs 'dữ liệu lớn') are immediately rejected.
    """
    cand_l, kw_l = cand.lower(), kw.lower()
    c_words = cand_l.split()
    k_words = kw_l.split()

    # Word-level gate for multi-word phrases
    if len(c_words) == len(k_words) and len(k_words) > 1:
        min_w = min(_word_sim(cw, kw_w) for cw, kw_w in zip(c_words, k_words))
        if min_w < 0.60:
            return 0.0

    raw = difflib.SequenceMatcher(None, cand_l, kw_l).ratio()
    base = difflib.SequenceMatcher(
        None, _strip_vietnamese_accents(cand_l), _strip_vietnamese_accents(kw_l)
    ).ratio()

    # If unaccented strings match >= 99.9%, it's a pure accent/diacritic drop OCR error
    if base >= 0.999:
        sim = max(0.88, 0.3 * raw + 0.7 * base)
    else:
        sim = max(raw, 0.35 * raw + 0.65 * base)
    return round(sim, 3)


class FuzzyKeywordMatcher:
    """
    Tier-2 Fuzzy Candidate Extractor.

    Scans text using high-performance pre-filtered sliding-window token matching.
    Achieves sub-second execution even across 100,000+ words.
    Strictly excludes positions that are already 100% exact matches.
    """

    @staticmethod
    def extract_candidates(
        text: str,
        keywords: List[str],
        exact_spans: List[Tuple[int, int]],
        min_similarity: float = 0.86,
        max_similarity: float = 0.999,
        context_window_chars: int = 80,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Extracts non-overlapping fuzzy keyword candidates from text.
        """
        if not text or not keywords:
            return {kw: [] for kw in keywords}

        text_len = len(text)

        # Helper to test overlap with exact matches
        def overlaps_exact(start: int, end: int) -> bool:
            for e_start, e_end in exact_spans:
                if not (end <= e_start or start >= e_end):
                    return True
            return False

        # Tokenize words with start/end character offsets
        word_matches = list(re.finditer(r"\S+", text))
        if not word_matches:
            return {kw: [] for kw in keywords}

        num_words = len(word_matches)

        # Pre-index keywords by word length and build fast metadata caches
        kw_by_wlen: Dict[int, List[str]] = {}
        kw_meta: Dict[str, Dict[str, Any]] = {}

        for kw in keywords:
            kw_clean = kw.strip()
            if not kw_clean:
                continue
            kw_l = kw_clean.lower()
            w_len = len(kw_clean.split())
            kw_by_wlen.setdefault(w_len, []).append(kw_clean)

            unacc = _strip_vietnamese_accents(kw_l)
            kw_meta[kw_clean] = {
                "clean": kw_clean,
                "len": len(kw_clean),
                "unaccented": unacc,
                "first_char": unacc[0] if unacc else "",
                "chars_set": set(unacc),
            }

        candidates_by_kw: Dict[str, List[Dict[str, Any]]] = {kw: [] for kw in keywords}
        all_raw_candidates: Dict[str, List[Dict[str, Any]]] = {kw: [] for kw in keywords}

        # Single-pass scan over word tokens with fast candidate pruning
        for i in range(num_words):
            for w_len, kws_list in kw_by_wlen.items():
                if i + w_len > num_words:
                    continue

                first_wm = word_matches[i]
                last_wm = word_matches[i + w_len - 1]
                start_char = first_wm.start()
                end_char = last_wm.end()

                if overlaps_exact(start_char, end_char):
                    continue

                raw_chunk = text[start_char:end_char]
                cleaned_chunk = re.sub(r"^[^\w]+|[^\w]+$", "", raw_chunk, flags=re.UNICODE)
                chunk_len = len(cleaned_chunk)
                if chunk_len < 2:
                    continue

                chunk_unacc = _strip_vietnamese_accents(cleaned_chunk.lower())
                first_c = chunk_unacc[0] if chunk_unacc else ""
                chunk_chars = set(chunk_unacc)

                for kw in kws_list:
                    km = kw_meta[kw]

                    # Fast pre-filter 1: Character length difference must be small
                    if abs(chunk_len - km["len"]) > 3:
                        continue

                    # Fast pre-filter 2: Character set intersection must be high (>= 60%)
                    intersect = len(chunk_chars & km["chars_set"])
                    if intersect / max(1, len(km["chars_set"])) < 0.60:
                        continue

                    # Deep verification with word-level gate
                    sim = calc_vietnamese_similarity(cleaned_chunk, kw)
                    if min_similarity <= sim <= max_similarity:
                        ctx_start = max(0, start_char - context_window_chars)
                        ctx_end = min(text_len, end_char + context_window_chars)
                        snippet = text[ctx_start:ctx_end].replace("\n", " ").strip()

                        all_raw_candidates[kw].append({
                            "keyword": kw,
                            "matched_text": cleaned_chunk,
                            "similarity": sim,
                            "start_char": start_char,
                            "end_char": end_char,
                            "context": snippet,
                        })

        # Non-maximum suppression (NMS) per keyword
        for kw, raw_list in all_raw_candidates.items():
            if not raw_list:
                continue

            raw_list.sort(key=lambda x: x["similarity"], reverse=True)
            chosen: List[Dict[str, Any]] = []

            for cand in raw_list:
                overlap = False
                for prev in chosen:
                    if not (cand["end_char"] <= prev["start_char"] or cand["start_char"] >= prev["end_char"]):
                        overlap = True
                        break
                if not overlap:
                    chosen.append(cand)

            chosen.sort(key=lambda x: x["start_char"])
            candidates_by_kw[kw] = chosen

        return candidates_by_kw

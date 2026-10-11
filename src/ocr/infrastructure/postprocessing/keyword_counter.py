import re
from pathlib import Path
from typing import List, Tuple, Optional

from src.ocr.domain.models.keyword import KeywordCount, KeywordReport, FuzzyMatchDetail
from src.ocr.domain.models.llm_config import LLMConfig
from src.ocr.infrastructure.postprocessing.fuzzy_keyword_matcher import FuzzyKeywordMatcher
from src.ocr.infrastructure.llm.llm_verifier import LLMVerifier


# Default keywords to write when config/keywords.txt does not exist
_DEFAULT_KEYWORDS = [
    "Trí tuệ nhân tạo",
    "Đổi mới",
    "Chuyển đổi số",
    "Xấu xí",
]


class KeywordCounter:
    """
    3-Tier Keyword Counter:
    - Tier 1: Fast deterministic Exact Counter (case-insensitive Unicode regex).
    - Tier 2: Algorithmic Fuzzy Candidate Extractor (sliding-window 80% - 99% similarity).
    - Tier 3: LLM Semantic & Context Verifier (validates genuine OCR typos/variants).
    """

    @staticmethod
    def load_keywords(keywords_file: str) -> List[str]:
        """
        Loads keywords from the given file path (one keyword per line).
        If the file does not exist, creates it with a default set of sample keywords.
        Blank lines and leading/trailing whitespace are stripped.
        """
        kw_path = Path(keywords_file)

        if not kw_path.exists():
            kw_path.parent.mkdir(parents=True, exist_ok=True)
            with open(kw_path, "w", encoding="utf-8") as f:
                for kw in _DEFAULT_KEYWORDS:
                    f.write(kw + "\n")
            print(f"[KEYWORDS] Created default keywords file: {kw_path}")

        keywords: List[str] = []
        with open(kw_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    keywords.append(stripped)

        return keywords

    @staticmethod
    def count_keywords(
        text: str,
        keywords: List[str],
        document_id: str = "",
        source_file: str = "",
        enable_llm: bool = False,
        min_similarity: float = 0.86,
        llm_config: Optional[LLMConfig] = None,
    ) -> KeywordReport:
        """
        Counts occurrences of each keyword in *text*.

        When *enable_llm* is False, performs only Tier 1 exact match.
        When *enable_llm* is True, activates Tier 2 (Fuzzy Extractor) and
        Tier 3 (LLM Verifier) to capture near-matches (80% - 99%).
        """
        text_lower = text.lower()
        exact_spans: List[Tuple[int, int]] = []
        keyword_counts: dict[str, KeywordCount] = {}
        total_exact = 0

        # Tier 1: Exact Matching
        for kw in keywords:
            pattern = re.escape(kw.lower())
            matches = list(re.finditer(pattern, text_lower, flags=re.UNICODE))
            exact_count = len(matches)
            for m in matches:
                exact_spans.append((m.start(), m.end()))

            kw_obj = KeywordCount(
                keyword=kw,
                exact_count=exact_count,
                fuzzy_count=0,
                count=exact_count,
                variants=[],
            )
            keyword_counts[kw] = kw_obj
            total_exact += exact_count

        total_fuzzy = 0

        # Tier 2 & 3: Fuzzy extraction + LLM verification
        if enable_llm:
            candidates_by_kw = FuzzyKeywordMatcher.extract_candidates(
                text=text,
                keywords=keywords,
                exact_spans=exact_spans,
                min_similarity=min_similarity,
            )

            # Flatten candidates for batch verification
            all_candidates = []
            for kw, cands in candidates_by_kw.items():
                all_candidates.extend(cands)

            if all_candidates:
                verifier = LLMVerifier(config=llm_config)
                verified_details: List[FuzzyMatchDetail] = verifier.verify_candidates(all_candidates)

                # Group verified details back by keyword
                for detail in verified_details:
                    # Match to target keyword
                    for cand in all_candidates:
                        if cand["matched_text"] == detail.matched_text:
                            target_kw = cand["keyword"]
                            if target_kw in keyword_counts:
                                keyword_counts[target_kw].variants.append(detail)
                                keyword_counts[target_kw].fuzzy_count += 1
                                total_fuzzy += 1
                            break

            # Sync total counts
            for kw_obj in keyword_counts.values():
                kw_obj.sync_total()

        return KeywordReport(
            document_id=document_id,
            source_file=source_file,
            keywords=list(keyword_counts.values()),
            total_keywords_searched=len(keywords),
            total_exact_matches=total_exact,
            total_fuzzy_matches=total_fuzzy,
            total_matches=total_exact + total_fuzzy,
            llm_enabled=enable_llm,
        )

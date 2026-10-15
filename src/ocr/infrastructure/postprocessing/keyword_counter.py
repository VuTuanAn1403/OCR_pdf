"""Literal, case-insensitive counting adapted from OCR_pdf_backup.

Each distinct phrase is counted independently, including substring matches.
Unicode NFC makes composed and decomposed Vietnamese accents equivalent.
"""
from pathlib import Path
import re
import unicodedata

from src.ocr.domain.models.keyword import KeywordCount, KeywordReport


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


class KeywordCounter:
    @staticmethod
    def unique_keywords(keywords: list[str]) -> list[str]:
        seen = set()
        result = []
        for keyword in keywords:
            keyword = unicodedata.normalize("NFC", keyword.strip())
            normalized = _normalize(keyword)
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(keyword)
        return result

    @classmethod
    def load_keywords(cls, keywords_file: str | Path) -> list[str]:
        # Accept a UTF-8 BOM written by Windows editors, without creating files.
        text = Path(keywords_file).expanduser().read_text(encoding="utf-8-sig")
        keywords = cls.unique_keywords(text.splitlines())
        if not keywords:
            raise ValueError(f"No keywords found in {keywords_file}")
        return keywords

    @classmethod
    def count_keywords(cls, text: str, keywords: list[str], document_id: str = "",
                       source_file: str = "") -> KeywordReport:
        normalized_text = _normalize(text)
        counts = []
        for keyword in cls.unique_keywords(keywords):
            count = sum(1 for _ in re.finditer(re.escape(_normalize(keyword)), normalized_text))
            counts.append(KeywordCount(keyword=keyword, exact_count=count, count=count))
        total = sum(item.count for item in counts)
        return KeywordReport(document_id=document_id, source_file=source_file, keywords=counts,
                             total_keywords_searched=len(counts), total_exact_matches=total,
                             total_matches=total)

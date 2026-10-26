"""Literal, case-insensitive counting adapted from OCR_pdf_backup.

Each distinct phrase is counted independently, including substring matches.
Unicode NFC makes composed and decomposed Vietnamese accents equivalent.

When a ``pages_dir`` is supplied the counter also reads individual page
Markdown files (``page-XXXX.md``) to determine the page number of each
occurrence and to extract a ±10-word citation window around the match.
"""
from pathlib import Path
import re
import unicodedata

from src.ocr.domain.models.keyword import KeywordCitation, KeywordCount, KeywordReport


def _normalize(value: str) -> str:
    """NFC + casefold for deterministic caseless matching."""
    return unicodedata.normalize("NFC", value).casefold()


def _prepare_page_text(raw_text: str) -> str:
    """NFC-normalize and lightly clean Markdown for readable citations.

    Removes image links, header markers, bold/italic markers, and table
    pipe characters so that the extracted citation windows read as clean
    natural-language sentences in the Excel report.
    """
    text = unicodedata.normalize("NFC", raw_text)
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)          # image links
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)       # keep link text
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE) # header markers
    text = re.sub(r'\*{1,3}', '', text)                        # bold / italic
    text = re.sub(r'\|', ' ', text)                            # table pipes
    text = re.sub(r'-{3,}', ' ', text)                         # horizontal rules
    text = re.sub(r'\s+', ' ', text).strip()                   # collapse whitespace
    return text


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
                       source_file: str = "", pages_dir: str | Path | None = None) -> KeywordReport:
        """Count keyword frequencies and extract per-page citations.

        Parameters
        ----------
        text : str
            Full document text (``result.md`` content).
        keywords : list[str]
            Ordered keyword list (from ``keywords.txt``).
        document_id, source_file : str
            Metadata identifiers embedded in the report.
        pages_dir : Path | None
            If provided and contains ``page-XXXX.md`` files, reads them.
            Otherwise falls back to splitting ``result.md`` by page headers.
        """
        normalized_text = _normalize(text)
        keywords_unique = cls.unique_keywords(keywords)

        # ── Tier 1: Exact counting on full result.md ──
        counts: list[KeywordCount] = []
        for keyword in keywords_unique:
            count = sum(1 for _ in re.finditer(re.escape(_normalize(keyword)), normalized_text))
            counts.append(KeywordCount(keyword=keyword, exact_count=count, count=count))

        # ── Per-page citation extraction ──
        # Collect page streams as (page_num, raw_content)
        pages_stream: list[tuple[int, str]] = []
        if pages_dir is not None and Path(pages_dir).is_dir():
            pages_path = Path(pages_dir)
            selected_pages = None
            manifest_path = pages_path.parent / "manifest.json"
            if manifest_path.is_file():
                try:
                    import json
                    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if isinstance(manifest_data.get("selected_pages"), list):
                        selected_pages = set(manifest_data["selected_pages"])
                except Exception:
                    pass

            for page_file in sorted(pages_path.glob("page-*.md")):
                try:
                    page_num = int(page_file.stem.split("-")[1])
                except (IndexError, ValueError):
                    continue
                if selected_pages is not None and page_num not in selected_pages:
                    continue
                pages_stream.append((page_num, page_file.read_text(encoding="utf-8-sig")))

        # Fallback: parse '# Trang X' from text if pages_dir gave no pages
        if not pages_stream and text:
            pattern = re.compile(r'(?:^|\n)# Trang\s+(\d+)\s*\n?', re.IGNORECASE)
            splits = list(pattern.finditer(text))
            if splits:
                for i, m in enumerate(splits):
                    p_num = int(m.group(1))
                    start = m.end()
                    end = splits[i + 1].start() if i + 1 < len(splits) else len(text)
                    pages_stream.append((p_num, text[start:end]))
            else:
                pages_stream.append((1, text))

        all_citations: list[KeywordCitation] = []
        for page_num, raw_page_text in pages_stream:
            page_text = _prepare_page_text(raw_page_text)
            for keyword in keywords_unique:
                pattern = re.compile(re.escape(unicodedata.normalize("NFC", keyword)), re.IGNORECASE)
                for m in pattern.finditer(page_text):
                    start, end = m.start(), m.end()
                    before_fragment = page_text[:start].strip()
                    after_fragment = page_text[end:].strip()
                    words_before = before_fragment.split()[-10:]
                    words_after = after_fragment.split()[:10]

                    all_citations.append(KeywordCitation(
                        keyword=keyword,
                        page_number=page_num,
                        citation_before=" ".join(words_before),
                        citation_keyword=page_text[start:end],
                        citation_after=" ".join(words_after),
                    ))

        total = sum(item.count for item in counts)
        return KeywordReport(
            document_id=document_id,
            source_file=source_file,
            keywords=counts,
            citations=all_citations,
            total_keywords_searched=len(counts),
            total_exact_matches=total,
            total_matches=total,
        )

from pathlib import Path
from typing import Optional

from src.ocr.domain.models.keyword import KeywordReport
from src.ocr.domain.models.llm_config import LLMConfig
from src.ocr.infrastructure.postprocessing.keyword_counter import KeywordCounter
from src.ocr.infrastructure.export.keyword_exporter import KeywordExporter


class CountKeywordsUseCase:
    """
    Application use case that orchestrates keyword counting after OCR export:
    1. Loads keywords from the configuration file.
    2. Reads the ``result.md`` output.
    3. Counts keyword frequencies (exact + optional 3-tier fuzzy LLM recheck).
    4. Exports ``count_keywords.md`` next to ``result.md``.
    """

    @classmethod
    def execute(
        cls,
        doc_dir: Path,
        document_id: str,
        keywords_file: Optional[str] = None,
        enable_llm: bool = False,
        min_similarity: float = 0.86,
        llm_config: Optional[LLMConfig] = None,
    ) -> Optional[KeywordReport]:
        """
        Parameters
        ----------
        doc_dir : Path
            The document output directory that contains ``result.md``.
        document_id : str
            Identifier for the processed document.
        keywords_file : str | None
            Path to the keywords text file.
        enable_llm : bool
            Whether to activate Tier 2 & Tier 3 LLM rechecking.
        min_similarity : float
            Minimum similarity threshold (0.80 = 80%).
        llm_config : LLMConfig | None
            Custom LLM configuration if any.

        Returns
        -------
        KeywordReport | None
            The keyword frequency report, or *None* if skipped.
        """
        # Resolve keywords file path
        if keywords_file is None:
            root_kw = Path("config/keywords.txt").resolve()
            bundled_kw = Path(__file__).parent.parent / "config" / "keywords.txt"
            if root_kw.exists():
                keywords_file = str(root_kw)
            else:
                keywords_file = str(bundled_kw)

        kw_path = Path(keywords_file)
        if not kw_path.exists():
            print(f"[KEYWORDS] Keywords file not found: {keywords_file} — skipping keyword counting.")
            return None

        # Load keywords
        keywords = KeywordCounter.load_keywords(keywords_file)
        if not keywords:
            print("[KEYWORDS] No keywords loaded — skipping keyword counting.")
            return None

        # Read result.md
        result_md_path = doc_dir / "result.md"
        if not result_md_path.exists():
            print(f"[KEYWORDS] result.md not found in {doc_dir} — skipping keyword counting.")
            return None

        with open(result_md_path, "r", encoding="utf-8") as f:
            text = f.read()

        # Count
        report = KeywordCounter.count_keywords(
            text=text,
            keywords=keywords,
            document_id=document_id,
            source_file=str(result_md_path),
            enable_llm=enable_llm,
            min_similarity=min_similarity,
            llm_config=llm_config,
        )

        # Export
        KeywordExporter.export_markdown(report, doc_dir)

        return report

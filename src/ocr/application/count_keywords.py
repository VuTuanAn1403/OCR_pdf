"""Count keywords in exported result.md without loading OCR models."""
import logging
import json
from pathlib import Path

from src.ocr.domain.models.keyword import KeywordReport
from src.ocr.infrastructure.export.keyword_exporter import KeywordExporter
from src.ocr.infrastructure.export.completion import MARKER_NAME, mark_completed, verify_completed
from src.ocr.infrastructure.postprocessing.keyword_counter import KeywordCounter
from src.ocr.runtime.paths import PROJECT_ROOT, runtime_resource

logger = logging.getLogger(__name__)


class CountKeywordsUseCase:
    @staticmethod
    def resolve_keywords_file(keywords_file: str | Path | None = None) -> Path:
        if keywords_file is not None:
            return Path(keywords_file).expanduser().resolve()
        if (PROJECT_ROOT / "pyproject.toml").is_file():
            return PROJECT_ROOT / "config/keywords.txt"
        return runtime_resource("keywords.txt")

    @classmethod
    def execute(cls, doc_dir: Path, document_id: str,
                keywords_file: str | Path | None = None) -> KeywordReport:
        keywords = KeywordCounter.load_keywords(cls.resolve_keywords_file(keywords_file))
        source = Path(doc_dir) / "result.md"
        report = KeywordCounter.count_keywords(source.read_text(encoding="utf-8-sig"), keywords,
                                               document_id, str(source))
        # A standalone/cache refresh changes a hashed artifact. Renew only a
        # previously valid marker; never certify damaged OCR output as complete.
        validation_status = None
        if verify_completed(Path(doc_dir)):
            validation_status = json.loads((Path(doc_dir) / MARKER_NAME).read_text(
                encoding="utf-8"))["validation_status"]
        path = KeywordExporter.export_excel(report, Path(doc_dir))
        if validation_status is not None:
            mark_completed(Path(doc_dir), validation_status)
        logger.info("Keyword report: %s (%d matches)", path, report.total_matches)
        return report

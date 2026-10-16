"""Versioned V6.3 contracts shared by the CPU supervisor and workers."""
from dataclasses import dataclass

KEYWORD_SCHEMA = "keyword-counts-v1"
POLICY = "literal_v1"
POLICY_VERSION = "nfc-casefold-literal-v1"
KEYWORD_ARTIFACTS = ("keyword_counts.json", "keyword_validation.json", "count_keywords.xlsx", "keyword_receipt.json")


class DocumentProcessingError(RuntimeError):
    pass


class PublicationError(RuntimeError):
    pass


class IdentityMismatch(RuntimeError):
    pass


@dataclass(frozen=True)
class DocumentProcessingResult:
    document_id: str
    output: str
    report_sha256: str
    completion_sha256: str
    validation: str


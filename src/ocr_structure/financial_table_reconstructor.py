"""Stable imports for financial geometry; implementation lives in financial_tables.

Adapted from VuTuanAn1403/OCR_pdf commit 37ca301304d84bfc4cc0d2cca3a296d513bb8ec7.
See FINANCIAL_GEOMETRY.md for source-preservation rules and validation scope.
"""
from .financial_tables.reconstructor import FinancialTableReconstructor
from .financial_tables.columns import ColumnBoundary
from .financial_tables.patterns import (
    FINANCIAL_NUM_RE, HEADER_KEYWORDS_NORM, ENTITY_BULLET_RE, TOTAL_ROW_RE,
    SECTION_TITLE_RE, strip_accents, is_total_line,
)

__all__ = [
    "FinancialTableReconstructor", "ColumnBoundary", "FINANCIAL_NUM_RE",
    "HEADER_KEYWORDS_NORM", "ENTITY_BULLET_RE", "TOTAL_ROW_RE", "SECTION_TITLE_RE",
    "strip_accents", "is_total_line",
]

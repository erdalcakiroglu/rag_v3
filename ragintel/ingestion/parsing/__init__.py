"""İP-2 parse alt paketi: ParsedDocument kontratı + backend'ler + adaptör."""

from .adapter import ParseAdapter, ParseAttempt, ParseResult
from .backends import get_backend
from .parsed_document import (
    Figure,
    Page,
    ParsedDocument,
    Section,
    Table,
)
from .quality import compute_parse_metrics

__all__ = [
    "ParsedDocument",
    "Page",
    "Section",
    "Table",
    "Figure",
    "ParseAdapter",
    "ParseResult",
    "ParseAttempt",
    "get_backend",
    "compute_parse_metrics",
]

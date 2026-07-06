"""Golden set şeması ve loader dış yüzeyi (İP-2.1a)."""

from .loader import (
    EvidenceMismatch,
    EvalSchemaMissingError,
    EvidenceValidationError,
    GoldenLoadResult,
    GoldenSetLoadError,
    load_golden_set,
)
from .models import (
    GoldEvidence,
    GoldenCategory,
    GoldenRecord,
    GoldenSetValidationError,
    load_golden_jsonl,
)

__all__ = [
    "EvidenceMismatch",
    "EvalSchemaMissingError",
    "EvidenceValidationError",
    "GoldEvidence",
    "GoldenCategory",
    "GoldenLoadResult",
    "GoldenRecord",
    "GoldenSetLoadError",
    "GoldenSetValidationError",
    "load_golden_jsonl",
    "load_golden_set",
]

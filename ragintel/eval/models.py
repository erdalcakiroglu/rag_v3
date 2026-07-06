"""Golden set JSONL şeması ve yükleme ön-doğrulamaları (İP-2.1a)."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..text import normalize_for_quote


class GoldenSetValidationError(RuntimeError):
    """JSONL şeması veya benzersizlik kuralları ihlal edildi."""


class GoldenCategory(str, Enum):
    SINGLE_FACT = "single_fact"
    SYNTHESIS = "synthesis"
    MULTI_HOP = "multi_hop"
    TABLE_BASED = "table_based"
    UNANSWERABLE = "unanswerable"
    CITATION_SENSITIVE = "citation_sensitive"


class GoldEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    sheet: str | None = Field(default=None, min_length=1)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_location(self) -> "GoldEvidence":
        if (self.page is None) == (self.sheet is None):
            raise ValueError("gold_evidence kaydı tam olarak bir location taşımalı: page veya sheet")
        return self


class GoldenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    ideal_answer: str = Field(min_length=1)
    category: GoldenCategory
    difficulty: int = Field(ge=1, le=3)
    gold_evidence: list[GoldEvidence] = Field(default_factory=list)
    doc_scope: str = Field(min_length=1)
    answerable: bool
    created_by: str = Field(min_length=1)
    notes: str = ""

    @model_validator(mode="after")
    def _check_answerability(self) -> "GoldenRecord":
        if self.answerable and not self.gold_evidence:
            raise ValueError("answerable kayıt en az bir gold_evidence taşımalı")
        if not self.answerable and self.gold_evidence:
            raise ValueError("answerable=false kayıt gold_evidence taşımamalı")
        if self.category == GoldenCategory.UNANSWERABLE and self.answerable:
            raise ValueError("unanswerable kategori answerable=false olmalı")
        if self.category != GoldenCategory.UNANSWERABLE and not self.answerable:
            raise ValueError("answerable=false yalnızca unanswerable kategori için geçerli")
        return self

    @property
    def normalized_question(self) -> str:
        return normalize_for_quote(self.question)

    def payload_hashable(self) -> dict:
        return self.model_dump(mode="json")


def load_golden_jsonl(path: str | Path) -> list[GoldenRecord]:
    """JSONL dosyasını okur, şema ve duplicate soru/id kurallarını doğrular."""
    path = Path(path)
    records: list[GoldenRecord] = []
    seen_ids: dict[str, int] = {}
    seen_questions: dict[str, int] = {}

    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise GoldenSetValidationError(
                    f"{path}:{lineno} JSON parse hatası: {exc.msg}"
                ) from exc
            try:
                record = GoldenRecord.model_validate(payload)
            except ValidationError as exc:
                raise GoldenSetValidationError(
                    f"{path}:{lineno} şema hatası: {exc.errors()}"
                ) from exc

            prev_id_line = seen_ids.get(record.id)
            if prev_id_line is not None:
                raise GoldenSetValidationError(
                    f"{path}:{lineno} duplicate id: {record.id!r} "
                    f"(ilk tanım satır {prev_id_line})"
                )
            qkey = record.normalized_question
            prev_q_line = seen_questions.get(qkey)
            if prev_q_line is not None:
                raise GoldenSetValidationError(
                    f"{path}:{lineno} duplicate question: {record.question!r} "
                    f"(ilk tanım satır {prev_q_line})"
                )

            seen_ids[record.id] = lineno
            seen_questions[qkey] = lineno
            records.append(record)

    return records

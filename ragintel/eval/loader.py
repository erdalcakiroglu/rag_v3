"""Golden set loader: JSONL -> DB, idempotent ve evidence doğrulamalı (İP-2.1a)."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from .models import GoldenRecord, load_golden_jsonl
from . import repository as repo
from ..text import normalize_for_quote


class GoldenSetLoadError(RuntimeError):
    """Golden set DB'ye yazılamadı."""


class EvalSchemaMissingError(GoldenSetLoadError):
    """FAZ 2 eval tabloları DB'de hazır değil."""


@dataclass(frozen=True)
class EvidenceMismatch:
    record_id: str
    question: str
    file_name: str
    quote: str
    doc_scope: str
    page: int | None = None
    sheet: str | None = None
    reason: str = "quote_not_found"


class EvidenceValidationError(GoldenSetLoadError):
    """Gold evidence corpus ile eşleşmedi; yazım yapılmadı."""

    def __init__(self, mismatches: list[EvidenceMismatch]):
        self.mismatches = mismatches
        msg = f"{len(mismatches)} gold_evidence eşleşmedi"
        super().__init__(msg)


@dataclass(frozen=True)
class GoldenLoadResult:
    set_version: str
    source_path: str
    inserted: int
    skipped: int
    no_op: bool


def _set_payload_hash(records: list[GoldenRecord]) -> str:
    payload = [rec.payload_hashable() for rec in records]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _record_payload_hash(record: GoldenRecord) -> str:
    canonical = json.dumps(
        record.payload_hashable(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_evidence(conn, records: list[GoldenRecord]) -> list[EvidenceMismatch]:
    mismatches: list[EvidenceMismatch] = []
    for record in records:
        for ev in record.gold_evidence:
            if not repo.corpus_files_exist(
                conn, file_name=ev.file_name, doc_scope=record.doc_scope
            ):
                mismatches.append(
                    EvidenceMismatch(
                        record_id=record.id,
                        question=record.question,
                        file_name=ev.file_name,
                        quote=ev.quote,
                        doc_scope=record.doc_scope,
                        page=ev.page,
                        sheet=ev.sheet,
                        reason="file_not_found",
                    )
                )
                continue

            candidates = repo.list_candidate_chunks(
                conn,
                file_name=ev.file_name,
                doc_scope=record.doc_scope,
                page=ev.page,
                sheet=ev.sheet,
            )
            if not candidates:
                mismatches.append(
                    EvidenceMismatch(
                        record_id=record.id,
                        question=record.question,
                        file_name=ev.file_name,
                        quote=ev.quote,
                        doc_scope=record.doc_scope,
                        page=ev.page,
                        sheet=ev.sheet,
                        reason="location_not_found",
                    )
                )
                continue

            quote_norm = normalize_for_quote(ev.quote)
            if not any(quote_norm in (c["chunk_text_norm"] or "") for c in candidates):
                mismatches.append(
                    EvidenceMismatch(
                        record_id=record.id,
                        question=record.question,
                        file_name=ev.file_name,
                        quote=ev.quote,
                        doc_scope=record.doc_scope,
                        page=ev.page,
                        sheet=ev.sheet,
                        reason="quote_not_found",
                    )
                )
    return mismatches


def load_golden_set(db, path: str | Path, *, set_version: str) -> GoldenLoadResult:
    """JSONL'i doğrular, evidence'ı corpus'ta arar ve DB'ye yazar."""
    path = Path(path)
    records = load_golden_jsonl(path)
    set_hash = _set_payload_hash(records)

    with db.connection() as conn:
        if not repo.eval_schema_ready(conn):
            raise EvalSchemaMissingError(
                "Eval tabloları bulunamadı. Önce docs/FAZ2_Sema.sql dosyasını DB'de uygulayın."
            )

        mismatches = _validate_evidence(conn, records)
        if mismatches:
            raise EvidenceValidationError(mismatches)

        existing_hash = repo.get_set_payload_hash(conn, set_version)
        if existing_hash == set_hash:
            return GoldenLoadResult(
                set_version=set_version,
                source_path=str(path),
                inserted=0,
                skipped=len(records),
                no_op=True,
            )

        repo.replace_set(
            conn,
            version=set_version,
            source_path=str(path),
            payload_hash=set_hash,
            records=[
                {
                    "record_id": rec.id,
                    "question": rec.question,
                    "question_norm": rec.normalized_question,
                    "ideal_answer": rec.ideal_answer,
                    "category": rec.category.value,
                    "difficulty": rec.difficulty,
                    "gold_evidence": [ev.model_dump(mode="json") for ev in rec.gold_evidence],
                    "doc_scope": rec.doc_scope,
                    "answerable": rec.answerable,
                    "created_by": rec.created_by,
                    "notes": rec.notes,
                    "payload_hash": _record_payload_hash(rec),
                }
                for rec in records
            ],
        )

    return GoldenLoadResult(
        set_version=set_version,
        source_path=str(path),
        inserted=len(records),
        skipped=0,
        no_op=False,
    )

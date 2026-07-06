"""QC Konsolidasyonu + bileşik kalite skoru (İP-9).

Dosya COMPLETED olduğunda orchestrator sonunda çağrılır (`finalize_file`):
  1. Chunk QC taramaları (empty/duplicate/too_short/too_long) -> qc_findings.
     Duplicate: chunk_text_norm ile DOSYA İÇİ (dosyalar arası rapor işi, silinmez).
  2. Bileşik skor: quality_score = Σ(weights × alt_skor); ağırlıklar
     app_config('quality').weights, alt skorlar metrics_ingestion.detail'den.
     intake alt skora GİRMEZ (Ek2); embed altyapı kesintisi skora yansımaz
     (o dosyalar COMPLETED değil).

`backfill_scores`: mevcut COMPLETED ama quality_score IS NULL kayıtları doldurur.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config.loader import EffectiveConfig, load_config
from ..database.config_store import make_db_reader
from ..database.ingestion_repo import (
    completed_without_score,
    delete_chunk_qc_findings,
    insert_qc_finding,
    list_chunks_for_qc,
    set_quality_score,
    step_metrics,
)
from ..observability.logging import get_logger

# Bileşik skora giren adımlar (intake HARİÇ — Ek2).
SCORE_STEPS = ("parse", "clean", "chunk", "embed")


@dataclass
class FinalizeResult:
    file_id: int
    quality_score: float | None
    sub_scores: dict
    qc_findings: dict


class QCConsolidator:
    def __init__(self, db, *, config: EffectiveConfig | None = None, logger=None):
        self.db = db
        self.cfg = config or (load_config(db_reader=make_db_reader(db)) if db
                              else load_config())
        ch = self.cfg.group("chunking")
        self.min_tokens = int(ch.min_tokens)
        self.max_tokens = int(ch.max_tokens)
        w = self.cfg.group("quality").weights
        self.weights = {"parse": w.parse, "clean": w.clean,
                        "chunk": w.chunk, "embed": w.embed}
        self.log = logger or get_logger("ingestion.qc")

    # -- public ---------------------------------------------------------------
    def finalize_file(self, file_id: int) -> FinalizeResult:
        qc = self.run_chunk_qc(file_id)
        score, subs = self.compute_and_write_score(file_id)
        self.log.info("qc_finalized", file_id=file_id, quality_score=score, **qc)
        return FinalizeResult(file_id, score, subs, qc)

    def backfill_scores(self) -> dict:
        """COMPLETED ama skorsuz dosyaları doldurur (QC + skor)."""
        with self.db.connection() as conn:
            targets = completed_without_score(conn)
        for fid in targets:
            self.finalize_file(fid)
        self.log.info("backfill_complete", count=len(targets))
        return {"backfilled": len(targets)}

    # -- chunk QC -------------------------------------------------------------
    def run_chunk_qc(self, file_id: int) -> dict:
        with self.db.connection() as conn:
            chunks = list_chunks_for_qc(conn, file_id)
            delete_chunk_qc_findings(conn, file_id)   # idempotent

            counts = {"empty_chunk": 0, "too_short": 0, "too_long": 0,
                      "duplicate_chunk": 0}
            seen: dict[str, int] = {}
            for c in chunks:
                norm = c["chunk_text_norm"] or ""
                if not norm.strip():
                    self._finding(conn, file_id, c["chunk_id"], "empty_chunk",
                                  f"chunk_index={c['chunk_index']}")
                    counts["empty_chunk"] += 1
                    continue
                if c["token_count"] < self.min_tokens:
                    self._finding(conn, file_id, c["chunk_id"], "too_short",
                                  f"token_count={c['token_count']} < {self.min_tokens}")
                    counts["too_short"] += 1
                if c["token_count"] > self.max_tokens:
                    self._finding(conn, file_id, c["chunk_id"], "too_long",
                                  f"token_count={c['token_count']} > {self.max_tokens}")
                    counts["too_long"] += 1
                # DOSYA İÇİ duplicate (chunk_text_norm hash'i).
                key = str(hash(norm))
                if key in seen:
                    self._finding(conn, file_id, c["chunk_id"], "duplicate_chunk",
                                  f"chunk_index={c['chunk_index']} eş={seen[key]}")
                    counts["duplicate_chunk"] += 1
                else:
                    seen[key] = c["chunk_index"]
        return counts

    def _finding(self, conn, file_id, chunk_id, finding, detail):
        insert_qc_finding(conn, file_id=file_id, finding=finding,
                          detail=detail, chunk_id=chunk_id)

    # -- bileşik skor ---------------------------------------------------------
    def compute_and_write_score(self, file_id: int) -> tuple[float | None, dict]:
        with self.db.connection() as conn:
            metrics = step_metrics(conn, file_id)

        subs = self._extract_sub_scores(metrics)
        num = sum(self.weights[k] * v for k, v in subs.items() if v is not None)
        den = sum(self.weights[k] for k, v in subs.items() if v is not None)
        score = round(num / den, 2) if den else None

        with self.db.connection() as conn:
            set_quality_score(conn, file_id, score)
        return score, subs

    def _extract_sub_scores(self, metrics: list[tuple[str, dict]]) -> dict:
        parse_details = [d for s, d in metrics if s == "parse"]
        clean_details = [d for s, d in metrics if s == "clean"]
        chunk_details = [d for s, d in metrics if s == "chunk"]
        embed_details = [d for s, d in metrics if s == "embed"]

        subs: dict[str, float | None] = {}
        # parse: OCR fallback varsa son (kabul edilen) denemenin skoru.
        if parse_details:
            final = max(parse_details, key=lambda d: d.get("attempt", 1))
            subs["parse"] = final.get("parse_score")
        if clean_details:
            d = clean_details[-1]
            subs["clean"] = d.get("clean_score")
            if subs["clean"] is None and d.get("retention_ratio") is not None:
                subs["clean"] = round(min(d["retention_ratio"], 1.0) * 100.0, 2)
        if chunk_details:
            subs["chunk"] = chunk_details[-1].get("chunk_score")
        if embed_details:
            subs["embed"] = embed_details[-1].get("embed_score")
        return subs

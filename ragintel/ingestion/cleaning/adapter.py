"""Cleaning Adaptörü (İP-3 orkestrasyonu).

- clean_document ile temizler; retention ölçer.
- metrics_ingestion(step='clean') yazar.
- Retention hard-fail (< eşik) -> core_files FAILED; pipeline devam eder.
- Soft flag'ler -> qc_findings: retention < low -> 'low_retention';
  retention > high (hiç temizlenmemiş) -> 'no_cleaning_effect'.
- Tüm eşikler app_config('quality').clean'den; kodda sabit yok.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ...config.loader import EffectiveConfig, load_config
from ...database.config_store import make_db_reader
from ...database.ingestion_repo import insert_metric, insert_qc_finding, mark_file_failed
from ...observability.logging import bind_context, clear_context, get_logger
from ...observability.tracing import set_span_attributes, start_span
from .cleaner import CleanResult, clean_document

CLEAN_STEP = "clean"


@dataclass
class CleanOutcome:
    file_id: int
    status: str                 # 'CLEANED' | 'FAILED'
    result: CleanResult
    findings: list[str] = field(default_factory=list)
    fail_reason: str | None = None


class CleanAdapter:
    def __init__(self, db=None, *, config: EffectiveConfig | None = None, logger=None):
        self.db = db
        if config is not None:
            self.cfg = config
        elif db is not None:
            self.cfg = load_config(db_reader=make_db_reader(db))
        else:
            self.cfg = load_config()
        self.clean_cfg = self.cfg.group("quality").clean
        self.log = logger or get_logger("ingestion.clean")

    # Saf temizlik (DB yok) — birim test ve pipeline in-memory kullanımı.
    def clean_parsed(self, parsed) -> CleanResult:
        return clean_document(parsed)

    def clean_file(self, file_id: int, parsed) -> CleanOutcome:
        bind_context(file_id=file_id)
        try:
            with start_span("ingest.clean", file_id=file_id, component="clean"):
                t0 = time.perf_counter()
                result = clean_document(parsed)
                duration_ms = int((time.perf_counter() - t0) * 1000)

                retention = result.metrics["retention_ratio"]
                ct = self.clean_cfg
                findings: list[str] = []
                status, reason = "CLEANED", None

                if retention < ct.hard_fail_retention:
                    status = "FAILED"
                    reason = (f"cleaning hard-fail: retention {retention} < "
                              f"{ct.hard_fail_retention} (aşırı temizlik)")
                else:
                    if retention < ct.soft_flag_retention_low:
                        findings.append("low_retention")
                    if retention > ct.soft_flag_retention_high:
                        findings.append("no_cleaning_effect")

                clean_score = round(min(retention, 1.0) * 100.0, 2)
                set_span_attributes(
                    clean_status=status,
                    clean_retention_ratio=retention,
                    clean_findings=",".join(findings) if findings else "",
                    clean_score=clean_score,
                )
                detail = {**result.metrics, "clean_score": clean_score,
                          "status": status, "findings": findings}
                with self.db.connection() as conn:
                    insert_metric(conn, file_id=file_id, step=CLEAN_STEP,
                                  duration_ms=duration_ms,
                                  ok=(status != "FAILED"), detail=detail)
                    for f in findings:
                        insert_qc_finding(conn, file_id=file_id, finding=f,
                                          detail=f"retention_ratio={retention}")
                    if status == "FAILED":
                        mark_file_failed(conn, file_id, reason)

                if status == "FAILED":
                    self.log.warning("clean_failed", retention=retention, reason=reason)
                else:
                    self.log.info("clean_ok", retention=retention, findings=findings)
                return CleanOutcome(file_id, status, result, findings, reason)
        finally:
            clear_context()

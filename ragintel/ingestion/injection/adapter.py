"""Injection Adaptörü (İP-4) — tarama + DB yan etkileri.

Bloklamaz: şüphede core_files.injection_flag=true + qc_findings('injection_suspect');
şüpheli span'ler detail'e. Metrik: metrics_ingestion(step='injection_scan').
"""

from __future__ import annotations

from dataclasses import dataclass

from ...config.loader import EffectiveConfig, load_config
from ...database.config_store import make_db_reader
from ...database.ingestion_repo import (
    insert_metric,
    insert_qc_finding,
    set_injection_flag,
)
from ...observability.logging import bind_context, clear_context, get_logger
from ...observability.tracing import set_span_attributes, start_span
from .scanner import InjectionResult, InjectionScanner

INJECTION_STEP = "injection_scan"


@dataclass
class InjectionOutcome:
    file_id: int
    flagged: bool
    result: InjectionResult


class InjectionAdapter:
    def __init__(self, db=None, *, config: EffectiveConfig | None = None, logger=None):
        self.db = db
        self.cfg = config or (load_config(db_reader=make_db_reader(db)) if db
                              else load_config())
        self.scanner = InjectionScanner(self.cfg)
        self.log = logger or get_logger("ingestion.injection")

    def scan_text(self, text: str, *, raw_text: str | None = None) -> InjectionResult:
        return self.scanner.scan(text, raw_text=raw_text)

    def scan_file(self, file_id: int, text: str, *,
                  raw_text: str | None = None) -> InjectionOutcome:
        bind_context(file_id=file_id)
        try:
            with start_span("ingest.injection", file_id=file_id, component="injection"):
                res = self.scanner.scan(text, raw_text=raw_text)
                spans = [{"rule": f.rule, "start": f.start, "end": f.end,
                          "evidence": f.evidence} for f in res.findings[:50]]
                set_span_attributes(
                    injection_flagged=res.flagged,
                    injection_scan_ms=res.scan_ms,
                    injection_findings=len(res.findings),
                )
                detail = {"flagged": res.flagged, "counts": res.counts,
                          "scan_ms": res.scan_ms, "spans": spans}
                with self.db.connection() as conn:
                    insert_metric(conn, file_id=file_id, step=INJECTION_STEP,
                                  duration_ms=int(res.scan_ms), ok=True, detail=detail)
                    if res.flagged:
                        set_injection_flag(conn, file_id, True)
                        summary = ", ".join(
                            f"{k}={v}" for k, v in res.counts.items() if v)
                        insert_qc_finding(conn, file_id=file_id,
                                          finding="injection_suspect",
                                          detail=f"injection şüphesi: {summary}")
                if res.flagged:
                    self.log.warning("injection_flagged", counts=res.counts)
                return InjectionOutcome(file_id, res.flagged, res)
        finally:
            clear_context()

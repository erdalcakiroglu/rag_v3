"""FAZ 7 RagRuntime — API'nin çekirdek servis nesnesi.

- Agentic graph'ı (FAZ 4) sarar; PostgresSaver thread_id=session_id ile çok-turlu.
- Girdi guardrail: boş/uzun soru reddi + İP-4 injection (FLAG-ONLY: reddetme,
  işaretle+devam). user_ctx MVP resolver — LLM'e SIZMAZ (mevcut kural).
- feedback → Langfuse score; health → DB/Ollama/TEI/Langfuse.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass

import httpx

from ..agents.graph import build_agent_graph, run_agent
from ..config.loader import EffectiveConfig
from ..config.settings import LangfuseSettings, LiteLLMSettings, OllamaSettings, TeiSettings
from ..ingestion.injection.scanner import InjectionScanner
from ..observability.logging import get_logger
from ..observability.tracing import set_span_attributes, start_span
from ..retrieval import ContextBuilder, RetrievalService
from ..agents.tools import ToolRegistry


class InputRejected(ValueError):
    """Boş / çok uzun soru — 400."""


def derive_health_status(checks: dict) -> str:
    """Ollama/DB down → unhealthy (pipeline çalışmaz); TEI down → degraded (akış çalışır)."""
    if checks.get("ollama") != "ok" or checks.get("db") != "ok":
        return "unhealthy"
    if checks.get("tei") != "ok":
        return "degraded"
    return "healthy"


@dataclass
class AskResult:
    session_id: str
    final_response: dict
    injection_flagged: bool
    trace_id: str


class RagRuntime:
    def __init__(self, *, db, config: EffectiveConfig, gateway, checkpointer,
                 service=None, context_builder=None, registry=None, langfuse: LangfuseSettings | None = None):
        self.db = db
        self.cfg = config
        self.log = get_logger("api.runtime")
        self.injection = InjectionScanner(config)
        self.max_q = int(config.group("agent").max_question_chars)
        self.langfuse = langfuse or LangfuseSettings()
        service = service or RetrievalService(db=db, config=config)
        context_builder = context_builder or ContextBuilder(db=db, config=config)
        registry = registry or ToolRegistry(service)
        self.app = build_agent_graph(
            gateway=gateway, context_builder=context_builder, registry=registry,
            config=config, checkpointer=checkpointer)
        self._lock = threading.Lock()  # MVP: graph invocation'ları serileştir (tek-bağlantı saver)

    # -- user_ctx (MVP; gerçek AuthN FAZ 6/7) ----------------------------------
    def resolve_user_ctx(self, user_id: str | None) -> dict:
        # TODO(FAZ6-7): gerçek kimlik doğrulama + role/scope çözümü. Şimdilik
        # header'dan user_id (yoksa 'dev'); allowed_doc_scopes SABİT ['default'].
        return {
            "user_id": user_id or "dev",
            "tenant_id": "default",
            "roles": ["user"],
            "allowed_doc_scopes": ["default"],
        }

    # -- /api/ask --------------------------------------------------------------
    def ask(self, question: str, session_id: str | None, user_id: str | None) -> AskResult:
        q = (question or "").strip()
        if not q:
            raise InputRejected("Soru boş olamaz.")
        if len(q) > self.max_q:
            raise InputRejected(f"Soru çok uzun (>{self.max_q} karakter).")
        session_id = session_id or f"sess-{uuid.uuid4().hex[:16]}"
        inj = self.injection.scan(q)
        user_ctx = self.resolve_user_ctx(user_id)
        initial = {"query": q, "user_ctx": user_ctx, "session_id": session_id, "retrieved": []}
        run_cfg = {"configurable": {"thread_id": session_id}}

        with start_span("api.request", session_id=session_id, injection_flagged=inj.flagged) as span:
            ctx = span.get_span_context()
            trace_id = f"{ctx.trace_id:032x}" if ctx.is_valid else uuid.uuid4().hex
            if inj.flagged:
                # FLAG-ONLY: reddetme, işaretle + logla, devam et.
                set_span_attributes(injection_findings=",".join(sorted(inj.counts)))
                self.log.warning("api_injection_flagged", session_id=session_id, counts=inj.counts)
            t0 = time.perf_counter()
            try:
                with self._lock:
                    out = run_agent(self.app, initial, config=run_cfg)
                final = out.get("final_response") or self._error_response(trace_id, t0, "empty")
            except Exception as exc:
                # LLM/altyapı hatası API'yi ÇÖKERTMEZ — dürüst fallback döner.
                set_span_attributes(error=type(exc).__name__)
                self.log.error("api_ask_failed", session_id=session_id, error=str(exc)[:200])
                final = self._error_response(trace_id, t0, type(exc).__name__)
        return AskResult(session_id=session_id, final_response=final,
                         injection_flagged=inj.flagged, trace_id=trace_id)

    @staticmethod
    def _error_response(trace_id: str, t0: float, reason: str) -> dict:
        return {
            "answer": "Sistem şu anda yanıt üretemedi (dil modeli geçici olarak erişilemedi). "
                      "Lütfen kısa süre sonra tekrar deneyin.",
            "sources": [], "confidence": "low", "followups": [],
            "meta": {"iterations": 0, "tokens": 0, "latency_ms": int((time.perf_counter() - t0) * 1000),
                     "model": "", "trace_id": trace_id, "generated_at": int(time.time())},
        }

    # -- /api/feedback → Langfuse score (7e amaç-4) ----------------------------
    def feedback(self, *, session_id: str, trace_id: str, rating: int, comment: str | None) -> dict:
        if not self.langfuse.enabled:
            self.log.info("feedback_langfuse_disabled", session_id=session_id, rating=rating)
            return {"status": "langfuse_disabled", "recorded": False}
        payload = {
            "id": f"fb-{uuid.uuid4().hex}",
            "traceId": trace_id,
            "name": "user_feedback",
            "value": int(rating),
            "dataType": "NUMERIC",
        }
        if comment:
            payload["comment"] = comment
        try:
            r = httpx.post(
                f"{self.langfuse.host.rstrip('/')}/api/public/scores",
                json=payload, auth=(self.langfuse.public_key, self.langfuse.secret_key), timeout=8)
            r.raise_for_status()
            return {"status": "ok", "recorded": True}
        except Exception as exc:
            self.log.warning("feedback_post_failed", error=str(exc))
            return {"status": "error", "recorded": False, "detail": str(exc)[:120]}

    # -- /api/health -----------------------------------------------------------
    def health(self) -> dict:
        checks = {"db": self._check_db(), "ollama": self._check_http(OllamaSettings().base_url + "/api/tags"),
                  "tei": self._check_http(TeiSettings().rerank_url.rstrip("/") + "/health"),
                  "langfuse": "enabled" if self.langfuse.enabled else "disabled"}
        return {"status": derive_health_status(checks), "checks": checks}

    def _check_db(self) -> str:
        try:
            with self.db.connection() as conn:
                conn.execute("SELECT 1;")
            return "ok"
        except Exception as exc:
            return f"down:{str(exc)[:40]}"

    @staticmethod
    def _check_http(url: str) -> str:
        try:
            httpx.get(url, timeout=8).raise_for_status()   # yavaş-ama-ayakta backend'e tolerans
            return "ok"
        except Exception:
            return "down"

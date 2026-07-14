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
from ..database import figure_repo, table_repo
from ..ingestion.injection.scanner import InjectionScanner
from ..observability.logging import get_logger
from ..observability.tracing import set_span_attributes, start_span
from ..retrieval import ContextBuilder, RetrievalService
from ..agents.tools import ToolRegistry
from .auth import DbUserResolver


class InputRejected(ValueError):
    """Boş / çok uzun soru — 400."""


def derive_health_status(checks: dict) -> str:
    """Ollama/DB down → unhealthy (pipeline çalışmaz); TEI down → degraded (akış çalışır).

    M-4: TEI "disabled" (URL tanımsız + rerank_backend=passthrough) degrade ETMEZ —
    yapılandırılmamış opsiyonel bileşen, arızalı bileşen değildir.
    """
    if checks.get("ollama") != "ok" or checks.get("db") != "ok":
        return "unhealthy"
    if checks.get("tei") not in ("ok", "disabled"):
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
                 service=None, context_builder=None, registry=None, langfuse: LangfuseSettings | None = None,
                 resolver=None):
        self.db = db
        self.cfg = config
        self.log = get_logger("api.runtime")
        # FAZ 6 AuthN: Bearer token → user_ctx. LDAP resolver (FAZ 9) buraya enjekte edilir.
        self.resolver = resolver or DbUserResolver(db)
        self.injection = InjectionScanner(config)
        self.max_q = int(config.group("agent").max_question_chars)
        self.langfuse = langfuse or LangfuseSettings()
        service = service or RetrievalService(db=db, config=config)
        context_builder = context_builder or ContextBuilder(db=db, config=config)
        registry = registry or ToolRegistry(service)
        # warm-up için tutulur: context_builder→tokenizer, service→embedder ön-ısıtma
        self.context_builder = context_builder
        self.service = service
        self.app = build_agent_graph(
            gateway=gateway, context_builder=context_builder, registry=registry,
            config=config, checkpointer=checkpointer)
        self._lock = threading.Lock()  # MVP: graph invocation'ları serileştir (tek-bağlantı saver)
        self._warm = threading.Event()  # tokenizer yüklenene kadar "warming"

    # -- warm-up (tokenizer ön-ısıtma) -----------------------------------------
    @property
    def is_warm(self) -> bool:
        return self._warm.is_set()

    def warm_up(self) -> None:
        """Soğuk-yükleme cezalarını startup'a taşır (§9b/9). İki bağımsız adım;
        biri hata verse diğeri çalışır ve flag yine set edilir (warm-up bloklamaz,
        sadece o bileşen ilk istekte yavaş olur):
          - tokenizer: BGE-M3 AutoTokenizer (context_builder her turda token sayar)
          - embedder: remote Ollama bge-m3 ilk /api/embed round-trip'i (~4.6s)"""
        try:
            self._warm_component(
                "tokenizer",
                lambda: self.context_builder.token_counter.count("ısınma"),
                available=lambda: hasattr(getattr(self.context_builder, "token_counter", None), "count"),
            )
            self._warm_component(
                "embedder",
                lambda: self.service.embedder.embed_batch(["ısınma"]),
                available=lambda: hasattr(getattr(self.service, "embedder", None), "embed_batch"),
            )
        finally:
            self._warm.set()

    def _warm_component(self, name: str, action, *, available) -> None:
        try:
            if not available():
                return
            t0 = time.perf_counter()
            action()
            self.log.info("warmup_done", component=name, warmup_ms=int((time.perf_counter() - t0) * 1000))
        except Exception as exc:
            self.log.warning("warmup_failed", component=name, error=str(exc)[:200])

    def warm_up_async(self) -> None:
        """Warm-up'ı arka planda başlatır (startup bloklamaz; health 'warming' döner)."""
        threading.Thread(target=self.warm_up, name="ragintel-warmup", daemon=True).start()

    # -- /api/ask (FAZ 6: Bearer token → user_ctx, fail-closed) -----------------
    def ask(self, question: str, session_id: str | None, token: str | None) -> AskResult:
        # AuthN ÖNCE: geçersiz/eksik token → Unauthorized (API 401). Fail-closed.
        user_ctx = self.resolver.resolve(token)
        q = (question or "").strip()
        if not q:
            raise InputRejected("Soru boş olamaz.")
        if len(q) > self.max_q:
            raise InputRejected(f"Soru çok uzun (>{self.max_q} karakter).")
        session_id = session_id or f"sess-{uuid.uuid4().hex[:16]}"
        inj = self.injection.scan(q)
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
        self._enrich_table_refs(final)
        self._enrich_figures(final, user_ctx)   # M-7: kaynağın sayfasındaki görseller
        return AskResult(session_id=session_id, final_response=final,
                         injection_flagged=inj.flagged, trace_id=trace_id)

    # -- M-2: kaynak zenginleştirme (additive) ---------------------------------
    def _enrich_table_refs(self, final: dict) -> None:
        """Tablo-kökenli kaynaklara `table_ref` ekler. Tablo-kökenli OLMAYAN kaynaklara
        dokunmaz. Çözümleme hatası /api/ask'i ÇÖKERTMEZ — kaynaklar zenginleştirilmeden
        döner (tablo düğmesi çıkmaz, cevap+alıntı aynen çalışır)."""
        srcs = list(final.get("sources") or [])
        srcs += list((final.get("meta") or {}).get("reviewed_sources") or [])
        chunk_ids = [int(s["chunk_id"]) for s in srcs if s.get("chunk_id") is not None]
        if not chunk_ids:
            return
        try:
            with self.db.connection() as conn:
                refs = table_repo.resolve_table_refs(conn, chunk_ids)
        except Exception as exc:
            self.log.warning("table_ref_resolve_failed", error=str(exc)[:200])
            return
        for src in srcs:
            ref = refs.get(int(src["chunk_id"]))
            if ref is not None:
                src["table_ref"] = ref

    # -- M-7: kaynağın SAYFASINDAKİ görseller (additive) ------------------------
    def _enrich_figures(self, final: dict, user_ctx: dict) -> None:
        """Kaynaklara `figures` (aynı dosya+sayfa görselleri) ekler. Scope korumalı:
        listeleme de fail-closed'dır — kullanıcının göremeyeceği dosyanın görseli
        listede BİLE görünmez (yoksa 404 veren bir düğme çizip varlığı sızdırırdık).
        Hata /api/ask'i ÇÖKERTMEZ (table_ref ile aynı sözleşme)."""
        srcs = list(final.get("sources") or [])
        srcs += list((final.get("meta") or {}).get("reviewed_sources") or [])
        chunk_ids = [int(s["chunk_id"]) for s in srcs if s.get("chunk_id") is not None]
        if not chunk_ids:
            return
        scopes = list(user_ctx.get("allowed_doc_scopes") or [])
        try:
            with self.db.connection() as conn:
                by_chunk = figure_repo.list_figures_for_chunks(conn, chunk_ids, scopes)
        except Exception as exc:
            self.log.warning("figure_enrich_failed", error=str(exc)[:200])
            return
        for src in srcs:
            figs = by_chunk.get(int(src["chunk_id"]))
            if figs:
                src["figures"] = figs

    # -- M-2: GET /api/table/{table_id} (scope fail-closed) ---------------------
    def table(self, table_id: int, user_ctx: dict) -> dict | None:
        """Scope'a uygunsa tablo payload'ı, değilse/yoksa None (çağıran 404'e çevirir)."""
        with self.db.connection() as conn:
            return table_repo.get_table_for_scopes(
                conn, table_id, list(user_ctx.get("allowed_doc_scopes") or []))

    # -- M-7: GET /api/figure/{figure_id} (scope fail-closed — M-2 deseni) -------
    def figure(self, figure_id: int, user_ctx: dict) -> dict | None:
        """Scope'a uygunsa görsel meta'sı (storage_path dahil), değilse/yoksa None.

        Görüntüsü kaydedilmemiş kayıt (storage_path NULL) da None sayılır: gösterilecek
        bir şey yok, çağıran 404 döner — 'var ama boş' diye ayırt edilebilir bir yanıt
        vermek scope dışı/var olmayan ayrımını da sızdırma riskine sokar."""
        with self.db.connection() as conn:
            fig = figure_repo.get_figure_for_scopes(
                conn, figure_id, list(user_ctx.get("allowed_doc_scopes") or []))
        if fig is None or not fig.get("storage_path"):
            return None
        return fig

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
                json=payload, auth=(self.langfuse.public_key, self.langfuse.secret_key),
                timeout=self.cfg.group("api").feedback_timeout)
            r.raise_for_status()
            return {"status": "ok", "recorded": True}
        except Exception as exc:
            self.log.warning("feedback_post_failed", error=str(exc))
            return {"status": "error", "recorded": False, "detail": str(exc)[:120]}

    # -- /api/health -----------------------------------------------------------
    def health(self) -> dict:
        api_cfg = self.cfg.group("api")
        tei_url = TeiSettings().rerank_url
        _ol = OllamaSettings()
        checks = {"db": self._check_db(),
                  "ollama": self._check_http(_ol.base_url + "/api/tags",
                                             api_cfg.health_timeout, _ol.api_key),
                  # M-4: TEI OPSİYONEL — URL tanımsızsa "down" değil "disabled".
                  "tei": (self._check_http(tei_url.rstrip("/") + "/health", api_cfg.health_timeout)
                          if tei_url else "disabled"),
                  "langfuse": "enabled" if self.langfuse.enabled else "disabled"}
        warm = self.is_warm
        checks["warmup"] = "ok" if warm else "warming"
        base = derive_health_status(checks)
        # Warming, unhealthy'yi MASKELEMEZ (db/ollama down daha kritik); sadece
        # aksi halde çalışır durumdayken "henüz ilk istek yavaş olur" sinyali.
        status = base if base == "unhealthy" or warm else "warming"
        return {"status": status, "checks": checks}

    def _check_db(self) -> str:
        try:
            with self.db.connection() as conn:
                conn.execute("SELECT 1;")
            return "ok"
        except Exception as exc:
            return f"down:{str(exc)[:40]}"

    @staticmethod
    def _check_http(url: str, timeout: float = 8.0, api_key: str = "") -> str:
        """M-9: auth'lu uç (H200/Open WebUI) anahtarsız istekte 401 döner — başlık
        gönderilmezse sağlıklı sistem 'down' görünürdü."""
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            httpx.get(url, timeout=timeout, headers=headers).raise_for_status()   # yavaş-ama-ayakta backend'e tolerans
            return "ok"
        except Exception:
            return "down"

"""FAZ 7 RagRuntime — API'nin çekirdek servis nesnesi.

- Agentic graph'ı (FAZ 4) sarar; PostgresSaver thread_id=session_id ile çok-turlu.
- Girdi guardrail: boş/uzun soru reddi + İP-4 injection (FLAG-ONLY: reddetme,
  işaretle+devam). user_ctx MVP resolver — LLM'e SIZMAZ (mevcut kural).
- feedback → Langfuse score; health → DB/Ollama/TEI/Langfuse.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass

import httpx

from ..agents.graph import build_agent_graph, run_agent, run_agent_stream
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


# M-15: ilerleme olaylarında gösterilen arama sorgusunun üst sınırı. Sorgu MODEL
# üretimidir; kırpma hem yükü küçük tutar hem de kontrolsüz uzunlukta metnin UI'a
# akmasını engeller (UI tarafında ayrıca metin olarak basılır, HTML olarak değil).
_EVENT_QUERY_MAX = 120


@dataclass
class _AskCtx:
    """`ask` / `ask_stream` prologue çıktısı — güvenlik sözleşmesinin taşıyıcısı."""
    q: str
    user_ctx: dict
    session_id: str
    inj: object          # InjectionScanner.scan sonucu (.flagged / .counts)


def tool_event(call: dict) -> dict:
    """Tool çağrısından SIZDIRILABİLİR alan seçimi — allowlist, denylist DEĞİL.

    Argüman sözlüğü OLDUĞU GİBİ geçirilmez: `tools_node` `user_ctx`'i runtime'da
    enjekte ediyor ve tool şeması ileride büyüyebilir; bugün güvenli olan bir sözlük
    yarın scope taşıyabilir. Denylist ("şu anahtarları çıkar") o gün sessizce
    yanılır; allowlist yanılmaz. Yalnız `query` (kırpılmış) ve SAYILAR çıkar."""
    args = call.get("arguments") or {}
    out: dict = {"name": str(call.get("name", ""))[:40]}
    query = args.get("query")
    if isinstance(query, str) and query.strip():
        out["query"] = query.strip()[:_EVENT_QUERY_MAX]
    ids = args.get("chunk_ids")
    if isinstance(ids, list):
        out["chunk_count"] = len(ids)
    return out


def node_event(node: str, update: dict, t_ms: int) -> dict | None:
    """Düğüm çıktısını KULLANICIYA GÖSTERİLEBİLİR olaya indirger (allowlist).

    `agent` düğümü tool ÇAĞIRMAYA KARAR verdiğinde olay çağrıdan ÖNCE gider —
    kullanıcı beklemeye BAŞLADIĞINDA görür, bittikten sonra değil. Bilinmeyen düğüm
    adı `None` döner: grafa yeni düğüm eklenirse içeriği kendiliğinden akmaz."""
    if node == "agent":
        calls = update.get("pending_tool_calls") or []
        if calls:
            return {"event": "tool", "data": {"t_ms": t_ms, "calls": [tool_event(c) for c in calls]}}
        return {"event": "step", "data": {"stage": "agent", "t_ms": t_ms}}
    if node == "tools":
        return {"event": "retrieved",
                "data": {"total": len(update.get("retrieved") or []), "t_ms": t_ms}}
    if node in ("prepare", "validate", "compose", "fallback"):
        return {"event": "step", "data": {"stage": node, "t_ms": t_ms}}
    return None


def derive_health_status(checks: dict) -> str:
    """Ollama/DB down → unhealthy (pipeline çalışmaz); TEI down → degraded (akış çalışır).

    M-4: TEI "disabled" (URL tanımsız + rerank_backend=passthrough) degrade ETMEZ —
    yapılandırılmamış opsiyonel bileşen, arızalı bileşen değildir.
    """
    if checks.get("ollama") != "ok" or checks.get("db") != "ok":
        return "unhealthy"
    if checks.get("tei") not in ("ok", "disabled"):
        return "degraded"
    # M-12 Redis eki: yapılandırılmış ama erişilemez Redis → DEGRADED (login/oturum yolu çökük;
    # admin DB token yolu aktif). "disabled" (yapılandırılmamış) degrade ETMEZ.
    if checks.get("redis") == "down":
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
                 resolver=None, session_store=None):
        self.db = db
        self.cfg = config
        self.log = get_logger("api.runtime")
        # M-12 Redis eki: login oturumlarının Redis deposu. Yoksa Null (login çalışmaz;
        # admin DB token yolu sürer). Uçlar login/logout/iptal için `rt().session_store`'u kullanır.
        from .session_store import NullSessionStore
        self.session_store = session_store or NullSessionStore()
        # FAZ 6 AuthN: Bearer token → user_ctx. LDAP resolver (FAZ 9) buraya enjekte edilir.
        self.resolver = resolver or DbUserResolver(db, store=self.session_store)
        self.injection = InjectionScanner(config)
        self.max_q = int(config.group("agent").max_question_chars)
        self.langfuse = langfuse or LangfuseSettings()
        service = service or RetrievalService(db=db, config=config)
        context_builder = context_builder or ContextBuilder(db=db, config=config)
        registry = registry or ToolRegistry(service, memory_reader=self._read_conversation_memory)
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
    def _ask_prologue(self, question: str, session_id: str | None, token: str | None) -> _AskCtx:
        """AuthN + girdi doğrulama + injection taraması. `ask` ve `ask_stream` AYNI
        prologue'u kullanır — güvenlik sözleşmesi tek yerde yaşasın, iki uç arasında
        sapamasın (fail-closed sırası: token → boş/uzunluk → tarama)."""
        # AuthN ÖNCE: geçersiz/eksik token → Unauthorized (API 401). Fail-closed.
        user_ctx = self.resolver.resolve(token)
        q = (question or "").strip()
        if not q:
            raise InputRejected("Soru boş olamaz.")
        if len(q) > self.max_q:
            raise InputRejected(f"Soru çok uzun (>{self.max_q} karakter).")
        session_id = session_id or f"sess-{uuid.uuid4().hex[:16]}"
        return _AskCtx(q=q, user_ctx=user_ctx, session_id=session_id, inj=self.injection.scan(q))

    def _ask_epilogue(self, ctx: _AskCtx, final: dict, trace_id: str) -> AskResult:
        """Zenginleştirme + geçmiş kaydı. `ask` ve `ask_stream` AYNI epilogue'u kullanır."""
        self._enrich_table_refs(final)
        self._enrich_figures(final, ctx.user_ctx)   # M-7: kaynağın sayfasındaki görseller
        # M-13: konuşma geçmişi (best-effort — başarısızlık cevabı ÇÖKERTMEZ; şema
        # uygulanmadıysa sessizce atlar). scope SNAPSHOT salt bilgidir (yetki değil).
        self._record_history(ctx.session_id, ctx.user_ctx, ctx.q, final, trace_id)
        return AskResult(session_id=ctx.session_id, final_response=final,
                         injection_flagged=ctx.inj.flagged, trace_id=trace_id)

    def ask(self, question: str, session_id: str | None, token: str | None) -> AskResult:
        ctx = self._ask_prologue(question, session_id, token)
        initial = {"query": ctx.q, "user_ctx": ctx.user_ctx, "session_id": ctx.session_id, "retrieved": []}
        run_cfg = {"configurable": {"thread_id": ctx.session_id}}

        with start_span("api.request", session_id=ctx.session_id, injection_flagged=ctx.inj.flagged) as span:
            span_ctx = span.get_span_context()
            trace_id = f"{span_ctx.trace_id:032x}" if span_ctx.is_valid else uuid.uuid4().hex
            self._note_injection(ctx)
            t0 = time.perf_counter()
            try:
                with self._lock:
                    out = run_agent(self.app, initial, config=run_cfg)
                final = out.get("final_response") or self._error_response(trace_id, t0, "empty")
            except Exception as exc:
                # LLM/altyapı hatası API'yi ÇÖKERTMEZ — dürüst fallback döner.
                set_span_attributes(error=type(exc).__name__)
                self.log.error("api_ask_failed", session_id=ctx.session_id, error=str(exc)[:200])
                final = self._error_response(trace_id, t0, type(exc).__name__)
        return self._ask_epilogue(ctx, final, trace_id)

    def _note_injection(self, ctx: _AskCtx) -> None:
        if ctx.inj.flagged:
            # FLAG-ONLY: reddetme, işaretle + logla, devam et.
            set_span_attributes(injection_findings=",".join(sorted(ctx.inj.counts)))
            self.log.warning("api_injection_flagged", session_id=ctx.session_id, counts=ctx.inj.counts)

    # -- M-15: /api/ask/stream — AŞAMA streaming'i ------------------------------
    def ask_stream(self, question: str, session_id: str | None, token: str | None):
        """İlerleme olayları üretir; generator'ın dönüş değeri `AskResult`'tır.

        NE AKMAZ: cevap metni. Bu mimaride nihai cevap LLM'den akmaz — ajan
        `submit_answer` tool'uyla teslim eder, metin `compose` düğümünde kurulur.
        Dolayısıyla token-streaming YOKTUR; akan şey AŞAMA sinyalidir. Ölçülen kazanç
        gerçek gecikmede değil ALGILANAN gecikmededir: ilk sinyal ~11.5-21.2 sn yerine
        ~0.1 sn'de gelir (M-15 anatomisi §2: soru süresi ≈ tur × 3.2 sn).

        GÜVENLİK: olay yükü allowlist'tir — aşama adı, tool adı, model-üretimi arama
        sorgusu (kısaltılmış) ve SAYILAR. Chunk metni, doküman adı, `user_ctx`,
        `allowed_doc_scopes` HİÇBİR olayda yer almaz (bkz. `_tool_event`).

        AuthN/girdi hataları İLK `next()`'te fırlar (generator gövdesi o an başlar) →
        uç, SSE gövdesi açılmadan 401/400 dönebilir.
        """
        ctx = self._ask_prologue(question, session_id, token)
        initial = {"query": ctx.q, "user_ctx": ctx.user_ctx, "session_id": ctx.session_id, "retrieved": []}
        run_cfg = {"configurable": {"thread_id": ctx.session_id}}
        t0 = time.perf_counter()

        def ms() -> int:
            return int((time.perf_counter() - t0) * 1000)

        with start_span("api.request", session_id=ctx.session_id, injection_flagged=ctx.inj.flagged) as span:
            span_ctx = span.get_span_context()
            trace_id = f"{span_ctx.trace_id:032x}" if span_ctx.is_valid else uuid.uuid4().hex
            self._note_injection(ctx)
            # İlk olay: kilit BEKLENMEDEN gider → TTFB kuyruğa girmeden ölçülür.
            yield {"event": "open", "data": {"session_id": ctx.session_id,
                                             "injection_flagged": ctx.inj.flagged, "t_ms": ms()}}
            final: dict | None = None
            try:
                with self._lock:
                    gen = run_agent_stream(self.app, initial, config=run_cfg)
                    while True:
                        try:
                            node, update = next(gen)
                        except StopIteration as stop:
                            out = stop.value or {}
                            break
                        event = node_event(node, update, ms())
                        if event is not None:
                            yield event
                final = out.get("final_response") or self._error_response(trace_id, t0, "empty")
            except Exception as exc:
                set_span_attributes(error=type(exc).__name__)
                self.log.error("api_ask_failed", session_id=ctx.session_id, error=str(exc)[:200])
                final = self._error_response(trace_id, t0, type(exc).__name__)
        result = self._ask_epilogue(ctx, final, trace_id)
        yield {"event": "final", "data": result.final_response}
        return result


    def _read_conversation_memory(self, conversation_id: str, user_id: str,
                                  limit_turns: int) -> list[dict]:
        """M-14 agent-pull çok-tur hafıza: bu oturumun ÖNCEKİ turlarının Q/A metnini (en yeni
        `limit_turns` tur = 2×limit mesaj) döndürür. SAHİPLİK fail-closed: get_conversation_messages
        başkasının/olmayan sohbette None → boş. Yalnız user/assistant metni (chunk/scope/tool YOK).
        Geçmiş, kullanıcının KENDİ önceki cevaplarıdır; retrieval her istekte canlı user_ctx ile
        sınırlı olduğundan yeni bir ifşa değil. DB hatası hafızayı boşaltır, ask'ı bozmaz."""
        from ..database import conversation_repo
        if not conversation_id or not user_id:
            return []
        try:
            with self.db.connection() as conn:
                msgs = conversation_repo.get_conversation_messages(conn, conversation_id, user_id)
        except Exception as exc:
            self.log.warning("memory_read_failed", session_id=conversation_id, error=str(exc)[:120])
            return []
        if not msgs:                       # None (sahip değil/yok) veya boş → hafıza yok
            return []
        tail = msgs[-2 * max(1, limit_turns):]
        return [{"role": m["role"], "content": m["content"]} for m in tail]

    def _record_history(self, session_id: str, user_ctx: dict, question: str,
                        final: dict, trace_id: str) -> None:
        from ..database import conversation_repo
        try:
            answer = str((final or {}).get("answer") or "")
            with self.db.connection() as conn:
                if not conversation_repo.conversations_ready(conn):
                    return
                conversation_repo.record_turn(
                    conn, conversation_id=session_id, user_id=user_ctx["user_id"],
                    question=question, answer=answer,
                    scopes=list(user_ctx.get("allowed_doc_scopes") or []), trace_id=trace_id)
        except Exception as exc:
            self.log.warning("history_record_failed", session_id=session_id, error=str(exc)[:120])

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
        # M-12 Redis eki: login oturum deposu. Yapılandırılmamışsa "disabled" (degrade ETMEZ —
        # login opsiyonel, admin DB token yolu var). Yapılandırılmış ama erişilemezse "down" →
        # derive_health_status DEGRADED yapar (login yolu gerçekten çökük; admin yolu aktif).
        _ss = getattr(self, "session_store", None)
        if _ss is None or not getattr(_ss, "enabled", False):
            checks["redis"] = "disabled"
        else:
            checks["redis"] = "ok" if _ss.ping() else "down"
        warm = self.is_warm
        checks["warmup"] = "ok" if warm else "warming"
        base = derive_health_status(checks)
        # Warming, unhealthy'yi MASKELEMEZ (db/ollama down daha kritik); sadece
        # aksi halde çalışır durumdayken "henüz ilk istek yavaş olur" sinyali.
        status = base if base == "unhealthy" or warm else "warming"
        # M-10/0: HANGİ KOD koşuyor? İmaja build'de gömülür (Dockerfile ARG GIT_SHA).
        # deploy.sh bunu dağıttığı sürümle kıyaslar → yanlış/cache'li imaj sessizce
        # eski kodu sunamaz. Konteyner dışında (lokal koşum) "unknown" döner.
        result = {"status": status, "checks": checks,
                  "git_sha": os.environ.get("RAGINTEL_GIT_SHA", "unknown")}
        # M-12 Redis eki: Redis down iken hangi yolun etkilendiğini AÇIKÇA söyle.
        if checks.get("redis") == "down":
            result["redis_note"] = ("Redis erişilemiyor: login/oturum yolu etkilendi "
                                    "(yeni giriş + mevcut oturumlar çözülemez); admin/servis "
                                    "DB token yolu aktif.")
        return result

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

"""FAZ 4 uçtan uca CANLI smoke (@db @slow).

Gerçek zincir: FAZ 1 korpusu (DB) → search_hybrid → ContextBuilder → agent
(LiteLLM/Ollama, RAGINTEL_AGENT_MODEL config'ten) → validate → compose.

Bu bir SMOKE'tur: graf akışının canlı altyapıyla uçtan uca koştuğunu, tam span
ağacının üretildiğini ve TEI kapalıyken rerank fallback'inin çalıştığını doğrular.
Küçük modelin yanıt KALİTESİ hard-assert EDİLMEZ (H200 notu) — davranış
gözlemleri rapora yazdırılır. Ollama/DB yoksa test atlanır.

Çalıştırma:  pytest tests/test_faz4_live_smoke.py -m "db and slow" -s
Env (opsiyonel):  RAGINTEL_AGENT_MODEL, RAGINTEL_LLM_API_BASE
"""

from __future__ import annotations

import os

import httpx
import pytest

from ragintel.agents.graph import build_agent_graph, run_agent
from ragintel.agents.tools import ToolRegistry
from ragintel.config.loader import load_config
from ragintel.config.settings import LiteLLMSettings, OllamaSettings
from ragintel.llm.gateway import LiteLLMGateway
from ragintel.observability.tracing import (
    _reset_tracing_for_tests,
    configure_tracing,
    force_flush_tracing,
)
from ragintel.retrieval import ContextBuilder, RetrievalService

pytestmark = [pytest.mark.db, pytest.mark.slow]

_AGENT_MODEL = os.environ.get("RAGINTEL_AGENT_MODEL", "llama3.2:3b")
_AGENT_BASE = os.environ.get("RAGINTEL_LLM_API_BASE", "http://finagoseek.finagotech.com.tr:11434")
_SCOPE = "default"


def _reachable(url: str) -> bool:
    try:
        httpx.get(url.rstrip("/") + "/api/tags", timeout=5).raise_for_status()
        return True
    except Exception:
        return False


def _require_ollama():
    if not _reachable(_AGENT_BASE):
        pytest.skip(f"Agent Ollama erişilemez: {_AGENT_BASE}")
    if not _reachable(OllamaSettings().base_url):
        pytest.skip(f"Embedding Ollama erişilemez: {OllamaSettings().base_url}")


def _user_ctx():
    return {"user_id": "smoke", "tenant_id": "t1", "roles": ["user"], "allowed_doc_scopes": [_SCOPE]}


def _build_app(live_db, *, rerank_backend="passthrough"):
    # Küçük/yavaş modele alan tanı ama sınırla (H200 öncesi dev).
    cfg = load_config(
        db_reader=lambda: {
            "retrieval": {"rerank_backend": rerank_backend},
            "agent": {"max_iterations": 3, "timeout_sec": 240},
        }
    )
    service = RetrievalService(db=live_db, config=cfg)
    context_builder = ContextBuilder(db=live_db, config=cfg)
    gateway = LiteLLMGateway(model=_AGENT_MODEL, settings=LiteLLMSettings(api_base=_AGENT_BASE))
    app = build_agent_graph(
        gateway=gateway, context_builder=context_builder, registry=ToolRegistry(service), config=cfg
    )
    return app, service


def _run(app, query, session):
    _reset_tracing_for_tests()
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    configure_tracing(exporter=exporter, force=True)
    initial = {"query": query, "user_ctx": _user_ctx(), "session_id": session, "retrieved": []}
    out = run_agent(app, initial)
    force_flush_tracing()
    spans = {s.name for s in exporter.get_finished_spans()}
    return out, spans


def _report(title, query, out, spans):
    final = out.get("final_response") or {}
    validation = out.get("validation") or {}
    budget = out.get("budget") or {}
    print(f"\n===== {title} =====")
    print(f"Soru: {query}")
    print(f"Yanıt: {(final.get('answer') or '')[:400]}")
    print(f"confidence={final.get('confidence')} | validation.passed={validation.get('passed')} "
          f"| coverage={validation.get('coverage')}")
    print(f"issues={validation.get('issues')}")
    print(f"sources={[(s['n'], s['chunk_id'], s['file_name']) for s in final.get('sources', [])]}")
    print(f"iterations={budget.get('iteration')} tokens_used={budget.get('tokens_used')} "
          f"retry_count={out.get('retry_count')}")
    print(f"spans={sorted(spans)}")


def _assert_tree(spans):
    # Kök + prepare + en az bir agent.step her akışta olmalı.
    for name in ("agent.run", "agent.prepare", "agent.step"):
        assert name in spans, f"span eksik: {name} ({spans})"
    # Normal akış validate+compose/fallback üretir; wall-clock timeout doğrudan fallback'e gider.
    assert ("agent.validate" in spans) or ("agent.fallback" in spans), spans
    assert ("agent.compose" in spans) or ("agent.fallback" in spans), spans


# --- (a) Korpustan cevaplanabilir Türkçe soru --------------------------------
def test_a_answerable_turkish_question(live_db):
    _require_ollama()
    app, _ = _build_app(live_db)
    query = "Türkiye'nin karbon emisyonu azaltım hedefi ve Yeşil Mutabakat kapsamında karbon ayak izi için ne planlanıyor?"
    out, spans = _run(app, query, "smoke-a")
    _report("(a) CEVAPLANABİLİR", query, out, spans)
    assert out.get("final_response") is not None
    _assert_tree(spans)


# --- (b) Korpusta olmayan soru → dürüst 'bulunamadı' / fallback --------------
def test_b_out_of_corpus_question_is_honest(live_db):
    _require_ollama()
    app, _ = _build_app(live_db)
    query = "İstanbul'daki en iyi pizza restoranı hangisidir ve menüsünde neler var?"
    out, spans = _run(app, query, "smoke-b")
    _report("(b) KORPUS DIŞI", query, out, spans)
    final = out.get("final_response") or {}
    validation = out.get("validation") or {}
    assert final is not None
    _assert_tree(spans)
    # Dürüstlük: ya düşük güven/fallback ya da 'bulunamadı'; uydurma citation PASS etmemeli.
    honest = (
        final.get("confidence") == "low"
        or not validation.get("passed", False)
        or "bulunama" in (final.get("answer") or "").lower()
    )
    assert honest, "Korpus dışı soruda uydurma yüksek-güven yanıt üretilmemeli"


# --- (c) TEI kapalıyken rerank fallback ile akış tamamlanır -------------------
def test_c_tei_down_flow_completes_with_rerank_fallback(live_db):
    _require_ollama()
    from ragintel.config.settings import TeiSettings

    app, service = _build_app(live_db, rerank_backend="tei")
    # TEI'yi erişilemez bir adrese sabitle → fail-open passthrough beklenir.
    # (Bootstrap ayarları OS env yok saydığından init-kwarg ile enjekte edilir.)
    service.tei_settings = TeiSettings(rerank_url="http://127.0.0.1:59999")

    # Akışın uçtan uca tamamlandığını göster.
    query = "Karbon vergisinin yenilenebilir enerji teşvikleriyle ilişkisi nedir?"
    out, spans = _run(app, query, "smoke-c")
    _report("(c) TEI KAPALI", query, out, spans)
    assert out.get("final_response") is not None
    _assert_tree(spans)

    # rerank fallback'ini deterministik doğrula: TEI down → passthrough sonuç döner (crash yok).
    sample = service.search_hybrid(query, top_k=6, user_ctx=_user_ctx())
    assert sample, "korpustan sonuç gelmedi"
    ranked = service.rerank(query, [c["chunk_id"] for c in sample], user_ctx=_user_ctx())
    assert len(ranked) == len(sample)  # fail-open: eleman kaybı yok
    print(f"\n[c] rerank fallback OK — {len(ranked)} sonuç TEI kapalıyken passthrough ile döndü")

"""İP-3.3 rerank: passthrough + TEI + fail-open."""

from __future__ import annotations

import json

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ragintel.config.loader import load_config
from ragintel.retrieval import RetrievalService
from ragintel.observability.tracing import _reset_tracing_for_tests, configure_tracing, force_flush_tracing


class _Embedder:
    model_name = "bge-m3@ollama"

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class _Store:
    def __init__(self, texts=None):
        self.texts = texts or {}

    def rerank_texts(self, *, chunk_ids, allowed_doc_scopes):
        return [{"chunk_id": chunk_id, "text": self.texts[chunk_id]} for chunk_id in chunk_ids]


def _svc(*, backend="passthrough", client=None, texts=None, retries=1, timeout=5.0):
    cfg = load_config(
        db_reader=lambda: {
            "retrieval": {
                "rerank_backend": backend,
                "rerank_retries": retries,
                "rerank_timeout_sec": timeout,
            }
        }
    )
    return RetrievalService(
        config=cfg,
        embedder=_Embedder(),
        store=_Store(texts),
        rerank_client=client,
    )


def _ctx(scopes=None):
    return {"user_id": "u", "tenant_id": "t", "roles": ["reader"], "allowed_doc_scopes": scopes or ["default"]}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mock", timeout=5.0)


def test_passthrough_rerank_preserves_input_order_and_scores():
    svc = _svc(backend="passthrough")
    out = svc.rerank("q", [9, 3, 5], user_ctx=_ctx())
    assert out == [
        {"chunk_id": 9, "rerank_score": 0.0},
        {"chunk_id": 3, "rerank_score": 0.0},
        {"chunk_id": 5, "rerank_score": 0.0},
    ]


def test_tei_backend_reorders_by_scores():
    def handler(request):
        body = json.loads(request.content)
        assert body["query"] == "karbon vergisi"
        assert body["texts"] == ["alakasiz spor haberi", "karbon vergisi aciklamasi"]
        return httpx.Response(200, json=[{"index": 0, "score": 0.01}, {"index": 1, "score": 0.99}])

    svc = _svc(
        backend="tei",
        client=_client(handler),
        texts={1: "alakasiz spor haberi", 2: "karbon vergisi aciklamasi"},
    )
    out = svc.rerank("karbon vergisi", [1, 2], user_ctx=_ctx())
    assert out == [
        {"chunk_id": 2, "rerank_score": 0.99},
        {"chunk_id": 1, "rerank_score": 0.01},
    ]


def test_tei_fail_open_on_timeout_returns_passthrough_and_span_flag():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ReadTimeout("read timed out")

    _reset_tracing_for_tests()
    exporter = InMemorySpanExporter()
    configure_tracing(exporter=exporter, force=True)
    svc = _svc(
        backend="tei",
        client=_client(handler),
        texts={7: "a", 8: "b"},
        retries=1,
    )
    out = svc.rerank("q", [7, 8], user_ctx=_ctx())
    force_flush_tracing()

    assert out == [
        {"chunk_id": 7, "rerank_score": 0.0},
        {"chunk_id": 8, "rerank_score": 0.0},
    ]
    assert calls["n"] == 2
    span = [s for s in exporter.get_finished_spans() if s.name == "retrieval.rerank"][-1]
    assert span.attributes["backend"] == "tei"
    assert span.attributes["rerank_fallback"] is True


def test_rerank_span_carries_backend_attribute():
    _reset_tracing_for_tests()
    exporter = InMemorySpanExporter()
    configure_tracing(exporter=exporter, force=True)
    svc = _svc(backend="passthrough")
    svc.rerank("q", [1, 2], user_ctx=_ctx())
    force_flush_tracing()
    span = [s for s in exporter.get_finished_spans() if s.name == "retrieval.rerank"][-1]
    assert span.attributes["backend"] == "passthrough"
    assert span.attributes["result_count"] == 2

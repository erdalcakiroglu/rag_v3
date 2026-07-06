"""İP-3.0 retrieval kontrat ve span testleri."""

from __future__ import annotations

import inspect

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ragintel.config.loader import load_config
from ragintel.observability.tracing import (
    _reset_tracing_for_tests,
    configure_tracing,
    force_flush_tracing,
)
from ragintel.retrieval import RetrievalService


class _Embedder:
    model_name = "bge-m3@ollama"

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class _Store:
    def __init__(self):
        self.calls = []

    def search_vector(self, **kwargs):
        self.calls.append(("search_vector", kwargs))
        return [{
            "chunk_id": 1,
            "text": "dogal metin",
            "score": 0.9,
            "source": {"file_id": 5, "file_name": "a.pdf", "page": 1, "section": "S", "version": 1},
            "retrieval_method": "vector",
        }]

    def search_hybrid(self, **kwargs):
        self.calls.append(("search_hybrid", kwargs))
        return []

    def lookup_document(self, **kwargs):
        self.calls.append(("lookup_document", kwargs))
        return []


def _svc(store=None):
    return RetrievalService(
        config=load_config(),
        embedder=_Embedder(),
        store=store or _Store(),
    )


def test_signatures_match_faz4_contract_shape():
    svc = _svc()
    assert list(inspect.signature(svc.search_hybrid).parameters) == ["query", "top_k", "filters", "user_ctx"]
    assert list(inspect.signature(svc.search_vector).parameters) == ["query", "top_k", "filters", "user_ctx"]
    assert list(inspect.signature(svc.lookup_document).parameters) == ["chunk_id", "window", "file_id", "page", "user_ctx"]
    assert list(inspect.signature(svc.rerank).parameters) == ["query", "chunk_ids", "user_ctx"]


def test_contract_calls_store_and_preserves_chunk_text():
    store = _Store()
    svc = _svc(store)
    user_ctx = {"user_id": "u", "tenant_id": "t", "roles": ["reader"], "allowed_doc_scopes": ["default"]}
    out = svc.search_vector("karbon vergisi", user_ctx=user_ctx)
    assert out[0]["text"] == "dogal metin"
    assert store.calls[0][0] == "search_vector"
    assert store.calls[0][1]["allowed_doc_scopes"] == ["default"]


def test_every_call_produces_span_and_fail_closed():
    _reset_tracing_for_tests()
    exporter = InMemorySpanExporter()
    configure_tracing(exporter=exporter, force=True)
    svc = _svc()

    out = svc.search_vector("q", user_ctx={"user_id": "u", "tenant_id": "t", "roles": [], "allowed_doc_scopes": []})
    assert out == []
    force_flush_tracing()
    spans = exporter.get_finished_spans()
    span = [s for s in spans if s.name == "retrieval.search_vector"][-1]
    assert span.attributes["fail_closed"] is True
    assert span.attributes["result_count"] == 0

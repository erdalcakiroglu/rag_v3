"""İP-2.2 tracing: span ağacı ve fire-and-forget exporter davranışı."""

from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ragintel.config.loader import load_config
from ragintel.ingestion.chunking.chunk import Chunk
from ragintel.ingestion.embedding import EmbeddingService, OllamaEmbedder
from ragintel.observability.tracing import (
    _reset_tracing_for_tests,
    configure_tracing,
    force_flush_tracing,
    start_span,
)
from tests import _ollama_mock as om


def _cfg(batch_size=2):
    return load_config(db_reader=lambda: {
        "embedding": {"model": "BAAI/bge-m3", "dim": om.DIM,
                      "batch_size": batch_size, "normalize": True}})


def _chunks(n):
    return [Chunk(i, f"metin {i}", f"metin {i}", 5) for i in range(n)]


def test_span_tree_contains_root_embed_and_http_children():
    _reset_tracing_for_tests()
    exporter = InMemorySpanExporter()
    configure_tracing(exporter=exporter, force=True)

    svc = EmbeddingService(
        config=_cfg(batch_size=2),
        embedder=OllamaEmbedder("http://mock", client=om.make_client(om.ok_handler())),
        retries=1,
        backoff_base=0.0,
        sleep=lambda _: None,
    )

    with start_span("ingest.run", file_id=42, component="orchestrator") as root:
        with start_span("ingest.embed", file_id=42, component="embed"):
            svc.embed_chunks(_chunks(3))
    force_flush_tracing()

    spans = exporter.get_finished_spans()
    by_name = {}
    for span in spans:
        by_name.setdefault(span.name, []).append(span)

    assert len(by_name["ingest.run"]) == 1
    assert len(by_name["ingest.embed"]) == 1
    assert len(by_name["embed.http"]) == 2
    root = by_name["ingest.run"][0]
    embed = by_name["ingest.embed"][0]
    http_parent_ids = {s.parent.span_id for s in by_name["embed.http"]}
    assert embed.parent.span_id == root.context.span_id
    assert embed.context.span_id in http_parent_ids
    assert root.attributes["file_id"] == 42


class _ExplodingExporter:
    def export(self, spans):
        raise RuntimeError("boom")

    def shutdown(self):
        return None


def test_exporter_failure_does_not_break_pipeline():
    _reset_tracing_for_tests()
    configure_tracing(exporter=_ExplodingExporter(), force=True)

    svc = EmbeddingService(
        config=_cfg(batch_size=2),
        embedder=OllamaEmbedder("http://mock", client=om.make_client(om.ok_handler())),
        retries=1,
        backoff_base=0.0,
        sleep=lambda _: None,
    )

    with start_span("ingest.run", file_id=7, component="orchestrator"):
        res = svc.embed_chunks(_chunks(2))
    assert res.metrics["embedded"] == 2
    force_flush_tracing()

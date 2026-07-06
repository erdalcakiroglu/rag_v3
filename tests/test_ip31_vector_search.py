"""İP-3.1 vector search: RBAC fail-closed, filtreler, sentetik top-k ve span süreleri."""

from __future__ import annotations

from datetime import date
import math

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ragintel.config.loader import load_config
from ragintel.database import storage_repo
from ragintel.ingestion.embedding.embedder import EmbeddingBackendError
from ragintel.observability.tracing import _reset_tracing_for_tests, configure_tracing, force_flush_tracing
from ragintel.retrieval import RetrievalBackendError, RetrievalService

pytestmark = pytest.mark.db

TEST_SCOPE_A = "__ip31_scope_a__"
TEST_SCOPE_B = "__ip31_scope_b__"
DIM = 1024


class _Embedder:
    model_name = "bge-m3@ollama"

    def __init__(self, mapping=None, *, exc=None):
        self.mapping = mapping or {}
        self.exc = exc

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self.exc is not None:
            raise self.exc
        return [self.mapping[t] for t in texts]


def _cfg():
    return load_config(db_reader=lambda: {"retrieval": {"default_top_k": 5, "max_top_k": 20, "vector_ef_search": 64}})


def _vec(a: float, b: float, c: float) -> list[float]:
    return [a, b, c] + [0.0] * (DIM - 3)


def _insert_file(db, *, name, checksum, scope, file_type="pdf", language="tr", created_at="2026-07-01T10:00:00+03:00"):
    with db.connection() as conn:
        return conn.execute(
            """
            INSERT INTO core_files
            (file_name, file_type, file_size, checksum, source_path, doc_scope, language, status, created_at)
            VALUES (%s, %s, 10, %s, 'p', %s, %s, 'COMPLETED', %s)
            RETURNING file_id;
            """,
            (name, file_type, checksum, scope, language, created_at),
        ).fetchone()[0]


def _insert_chunk_with_vector(db, *, file_id, chunk_index, text, norm, vector, page=1, section="Genel"):
    with db.connection() as conn:
        storage_repo.register_vector(conn)
        chunk_id = conn.execute(
            """
            INSERT INTO core_chunks
            (file_id, chunk_index, chunk_text, chunk_text_norm, token_count, page_number, section_title)
            VALUES (%s, %s, %s, %s, 10, %s, %s)
            RETURNING chunk_id;
            """,
            (file_id, chunk_index, text, norm, page, section),
        ).fetchone()[0]
        from pgvector import Vector
        conn.execute(
            "INSERT INTO core_vectors (chunk_id, embedding, model_name) VALUES (%s, %s, 'bge-m3@ollama');",
            (chunk_id, Vector(vector)),
        )
        return chunk_id


@pytest.fixture(autouse=True)
def _clean(live_db):
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope IN (%s, %s);", (TEST_SCOPE_A, TEST_SCOPE_B))
    yield
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope IN (%s, %s);", (TEST_SCOPE_A, TEST_SCOPE_B))


def _service(db, mapping=None, *, exc=None):
    return RetrievalService(db, config=_cfg(), embedder=_Embedder(mapping, exc=exc))


def test_senthetic_top_k_and_filters(live_db):
    f1 = _insert_file(live_db, name="a.pdf", checksum="ip31a", scope=TEST_SCOPE_A, file_type="pdf", language="tr")
    f2 = _insert_file(live_db, name="b.docx", checksum="ip31b", scope=TEST_SCOPE_A, file_type="docx", language="en", created_at="2026-06-01T10:00:00+03:00")
    _insert_chunk_with_vector(live_db, file_id=f1, chunk_index=0, text="Karbon vergisi metni", norm="karbon", vector=_vec(1.0, 0.0, 0.0), section="Vergi")
    _insert_chunk_with_vector(live_db, file_id=f2, chunk_index=0, text="English climate text", norm="english", vector=_vec(0.0, 1.0, 0.0), section="Intro")

    svc = _service(live_db, {"soru_tr": _vec(1.0, 0.0, 0.0), "soru_en": _vec(0.0, 1.0, 0.0)})
    user_ctx = {"user_id": "u", "tenant_id": "t", "roles": ["reader"], "allowed_doc_scopes": [TEST_SCOPE_A]}

    out = svc.search_vector("soru_tr", top_k=10, user_ctx=user_ctx)
    assert out[0]["source"]["file_name"] == "a.pdf"
    assert out[0]["retrieval_method"] == "vector"

    assert svc.search_vector("soru_en", filters={"file_type": "docx"}, user_ctx=user_ctx)[0]["source"]["file_name"] == "b.docx"
    assert svc.search_vector("soru_en", filters={"language": "en"}, user_ctx=user_ctx)[0]["source"]["file_name"] == "b.docx"
    assert svc.search_vector("soru_tr", filters={"section": "Vergi"}, user_ctx=user_ctx)[0]["source"]["file_name"] == "a.pdf"
    assert svc.search_vector("soru_tr", filters={"date_from": date(2026, 7, 1)}, user_ctx=user_ctx)[0]["source"]["file_name"] == "a.pdf"
    assert svc.search_vector("soru_tr", filters={"date_to": date(2026, 6, 15)}, user_ctx=user_ctx)[0]["source"]["file_name"] == "b.docx"


def test_fail_closed_and_cross_scope_zero_leakage(live_db):
    f1 = _insert_file(live_db, name="a.pdf", checksum="ip31c", scope=TEST_SCOPE_A)
    f2 = _insert_file(live_db, name="b.pdf", checksum="ip31d", scope=TEST_SCOPE_B)
    _insert_chunk_with_vector(live_db, file_id=f1, chunk_index=0, text="A scope", norm="a", vector=_vec(1.0, 0.0, 0.0))
    _insert_chunk_with_vector(live_db, file_id=f2, chunk_index=0, text="B scope", norm="b", vector=_vec(1.0, 0.0, 0.0))

    svc = _service(live_db, {"q": _vec(1.0, 0.0, 0.0)})
    assert svc.search_vector("q", user_ctx={"user_id": "u", "tenant_id": "t", "roles": [], "allowed_doc_scopes": []}) == []

    out = svc.search_vector("q", user_ctx={"user_id": "u", "tenant_id": "t", "roles": [], "allowed_doc_scopes": [TEST_SCOPE_A]})
    assert {c["source"]["file_name"] for c in out} == {"a.pdf"}


def test_lookup_document_obeys_scope(live_db):
    f1 = _insert_file(live_db, name="lookup.pdf", checksum="ip31e", scope=TEST_SCOPE_A)
    c0 = _insert_chunk_with_vector(live_db, file_id=f1, chunk_index=0, text="ilk", norm="ilk", vector=_vec(1.0, 0.0, 0.0), page=1)
    _insert_chunk_with_vector(live_db, file_id=f1, chunk_index=1, text="orta", norm="orta", vector=_vec(1.0, 0.0, 0.0), page=1)
    _insert_chunk_with_vector(live_db, file_id=f1, chunk_index=2, text="son", norm="son", vector=_vec(1.0, 0.0, 0.0), page=2)

    svc = _service(live_db, {"q": _vec(1.0, 0.0, 0.0)})
    user_ctx = {"user_id": "u", "tenant_id": "t", "roles": [], "allowed_doc_scopes": [TEST_SCOPE_A]}
    by_chunk = svc.lookup_document(chunk_id=c0, window=1, user_ctx=user_ctx)
    assert [c["text"] for c in by_chunk] == ["ilk", "orta"]
    by_page = svc.lookup_document(file_id=f1, page=2, user_ctx=user_ctx)
    assert [c["text"] for c in by_page] == ["son"]


def test_backend_error_is_readable(live_db):
    svc = _service(live_db, exc=EmbeddingBackendError("ollama down"))
    with pytest.raises(RetrievalBackendError, match="Sorgu embedding alınamadı"):
        svc.search_vector("q", user_ctx={"user_id": "u", "tenant_id": "t", "roles": [], "allowed_doc_scopes": [TEST_SCOPE_A]})


def test_span_p95_reports_embed_and_db_separately(live_db):
    f1 = _insert_file(live_db, name="perf.pdf", checksum="ip31f", scope=TEST_SCOPE_A)
    for i in range(10):
        angle = i / 10
        _insert_chunk_with_vector(live_db, file_id=f1, chunk_index=i, text=f"chunk {i}", norm=f"chunk {i}", vector=_vec(1.0 - angle, angle, 0.0), page=1)

    mapping = {f"q{i}": _vec(1.0 - (i / 10), i / 10, 0.0) for i in range(10)}
    svc = _service(live_db, mapping)
    user_ctx = {"user_id": "u", "tenant_id": "t", "roles": [], "allowed_doc_scopes": [TEST_SCOPE_A]}

    _reset_tracing_for_tests()
    exporter = InMemorySpanExporter()
    configure_tracing(exporter=exporter, force=True)
    for i in range(10):
        out = svc.search_vector(f"q{i}", user_ctx=user_ctx)
        assert out
    force_flush_tracing()
    spans = [s for s in exporter.get_finished_spans() if s.name == "retrieval.search_vector"]
    embed_ms = sorted(int(s.attributes["query_embed_ms"]) for s in spans)
    db_ms = sorted(int(s.attributes["db_ms"]) for s in spans)
    p95_embed = embed_ms[math.ceil(len(embed_ms) * 0.95) - 1]
    p95_db = db_ms[math.ceil(len(db_ms) * 0.95) - 1]
    print(f"\n[IP-3.1 p95] embed_ms={p95_embed} db_ms={p95_db}")
    assert p95_embed >= 0
    assert p95_db >= 0


def test_top_k_is_capped_by_config(live_db):
    f1 = _insert_file(live_db, name="cap.pdf", checksum="ip31g", scope=TEST_SCOPE_A)
    for i in range(25):
        _insert_chunk_with_vector(live_db, file_id=f1, chunk_index=i, text=f"cap {i}", norm=f"cap {i}", vector=_vec(1.0 - (i / 25), i / 25, 0.0))
    svc = _service(live_db, {"q": _vec(1.0, 0.0, 0.0)})
    user_ctx = {"user_id": "u", "tenant_id": "t", "roles": [], "allowed_doc_scopes": [TEST_SCOPE_A]}
    out = svc.search_vector("q", top_k=99, user_ctx=user_ctx)
    assert len(out) == 20

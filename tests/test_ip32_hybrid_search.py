"""İP-3.2 hybrid search: tek-SQL CTE, RRF/ağırlıklı füzyon ve sparse RBAC."""

from __future__ import annotations

from datetime import date

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ragintel.config.loader import load_config
from ragintel.database import storage_repo
from ragintel.observability.tracing import _reset_tracing_for_tests, configure_tracing, force_flush_tracing
from ragintel.retrieval import RetrievalService
from ragintel.retrieval import repository as repo
from ragintel.text import normalize_for_search

pytestmark = pytest.mark.db

TEST_SCOPE_A = "__ip32_scope_a__"
TEST_SCOPE_B = "__ip32_scope_b__"
DIM = 1024


class _Embedder:
    model_name = "bge-m3@ollama"

    def __init__(self, mapping):
        self.mapping = mapping

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.mapping[t] for t in texts]


def _cfg(**overrides):
    retrieval = {
        "default_top_k": 10,
        "max_top_k": 20,
        "vector_ef_search": 64,
        "hybrid_fusion": "rrf",
        "hybrid_rrf_k": 60,
        "hybrid_dense_weight": 1.0,
        "hybrid_sparse_weight": 1.0,
        "hybrid_sparse_variant": "simple",
    }
    retrieval.update(overrides)
    return load_config(db_reader=lambda: {"retrieval": retrieval})


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


def _service(db, mapping, **retrieval_cfg):
    return RetrievalService(
        db,
        config=_cfg(**retrieval_cfg),
        embedder=_Embedder(mapping),
    )


def _ctx(scope=TEST_SCOPE_A):
    return {"user_id": "u", "tenant_id": "t", "roles": ["reader"], "allowed_doc_scopes": [scope]}


def test_hybrid_returns_dense_only_sparse_only_and_both_scenarios(live_db):
    dense_only = _insert_file(live_db, name="dense.pdf", checksum="ip32dense", scope=TEST_SCOPE_A)
    sparse_only = _insert_file(live_db, name="sparse.pdf", checksum="ip32sparse", scope=TEST_SCOPE_A)
    both = _insert_file(live_db, name="both.pdf", checksum="ip32both", scope=TEST_SCOPE_A)

    _insert_chunk_with_vector(
        live_db,
        file_id=dense_only,
        chunk_index=0,
        text="Anlamsal olarak yakin ama nadir anahtar icermeyen belge",
        norm="anlamsal olarak yakin ama nadir anahtar icermeyen belge",
        vector=_vec(1.0, 0.0, 0.0),
    )
    _insert_chunk_with_vector(
        live_db,
        file_id=sparse_only,
        chunk_index=0,
        text="nadiranahtar yalnizca bu belgede geciyor",
        norm="nadiranahtar yalnizca bu belgede geciyor",
        vector=_vec(0.0, 1.0, 0.0),
    )
    _insert_chunk_with_vector(
        live_db,
        file_id=both,
        chunk_index=0,
        text="ortakanahtar ortakanahtar hem lexical hem anlamsal eslesme",
        norm="ortakanahtar ortakanahtar hem lexical hem anlamsal eslesme",
        vector=_vec(0.0, 0.0, 1.0),
    )

    svc = _service(
        live_db,
        {
            "dense_query": _vec(1.0, 0.0, 0.0),
            "nadiranahtar": _vec(0.0, 0.0, 1.0),
            "ortakanahtar": _vec(0.0, 0.0, 1.0),
        },
    )

    out_dense = svc.search_hybrid("dense_query", top_k=2, user_ctx=_ctx())
    assert out_dense[0]["source"]["file_name"] == "dense.pdf"
    assert out_dense[0]["retrieval_method"] == "hybrid"

    out_sparse = svc.search_hybrid("nadiranahtar", top_k=2, user_ctx=_ctx())
    assert {c["source"]["file_name"] for c in out_sparse} >= {"sparse.pdf"}

    out_both = svc.search_hybrid("ortakanahtar", top_k=2, user_ctx=_ctx())
    assert out_both[0]["source"]["file_name"] == "both.pdf"
    assert out_both[0]["detail"]["dense_rank"] == 1
    assert out_both[0]["detail"]["sparse_rank"] == 1


def test_rrf_scores_match_manual_expected(live_db):
    a = _insert_file(live_db, name="a.pdf", checksum="ip32rrfa", scope=TEST_SCOPE_A)
    b = _insert_file(live_db, name="b.pdf", checksum="ip32rrfb", scope=TEST_SCOPE_A)
    c = _insert_file(live_db, name="c.pdf", checksum="ip32rrfc", scope=TEST_SCOPE_A)

    _insert_chunk_with_vector(live_db, file_id=a, chunk_index=0, text="dense only belge", norm="dense only belge", vector=_vec(1.0, 0.0, 0.0))
    _insert_chunk_with_vector(live_db, file_id=b, chunk_index=0, text="rrfmark rrfmark rrfmark", norm="rrfmark rrfmark rrfmark", vector=_vec(0.7, 0.3, 0.0))
    _insert_chunk_with_vector(live_db, file_id=c, chunk_index=0, text="rrfmark rrfmark", norm="rrfmark rrfmark", vector=_vec(0.8, 0.2, 0.0))

    svc = _service(live_db, {"rrfmark": _vec(1.0, 0.0, 0.0)}, hybrid_rrf_k=60)
    out = svc.search_hybrid("rrfmark", top_k=3, user_ctx=_ctx())

    expected = {
        "a.pdf": 1 / (60 + 1),
        "b.pdf": 1 / (60 + 3) + 1 / (60 + 1),
        "c.pdf": 1 / (60 + 2) + 1 / (60 + 2),
    }
    by_name = {item["source"]["file_name"]: item for item in out}
    assert list(by_name) == ["b.pdf", "c.pdf", "a.pdf"]
    for name, score in expected.items():
        assert by_name[name]["score"] == pytest.approx(score, rel=1e-9)


def test_rrf_k_and_weight_config_change_ranking(live_db):
    sparse_rank_map = {1: 20, 5: 5}
    remaining = [r for r in range(1, 21) if r not in sparse_rank_map.values()]
    for dense_rank in range(2, 21):
        if dense_rank not in sparse_rank_map:
            sparse_rank_map[dense_rank] = remaining.pop()

    for dense_rank in range(1, 21):
        file_id = _insert_file(
            live_db,
            name=f"rrf_{dense_rank:02d}.pdf",
            checksum=f"ip32rrf{dense_rank:02d}",
            scope=TEST_SCOPE_A,
        )
        sparse_rank = sparse_rank_map[dense_rank]
        reps = 21 - sparse_rank
        _insert_chunk_with_vector(
            live_db,
            file_id=file_id,
            chunk_index=0,
            text=("rrfflip " * reps).strip(),
            norm=("rrfflip " * reps).strip(),
            vector=_vec(1.0 - (dense_rank * 0.01), dense_rank * 0.01, 0.0),
        )

    small_k = _service(live_db, {"rrfflip": _vec(1.0, 0.0, 0.0)}, hybrid_rrf_k=1)
    large_k = _service(live_db, {"rrfflip": _vec(1.0, 0.0, 0.0)}, hybrid_rrf_k=60)
    low_rank = [c["source"]["file_name"] for c in small_k.search_hybrid("rrfflip", top_k=20, user_ctx=_ctx())]
    high_rank = [c["source"]["file_name"] for c in large_k.search_hybrid("rrfflip", top_k=20, user_ctx=_ctx())]
    assert low_rank.index("rrf_01.pdf") < low_rank.index("rrf_05.pdf")
    assert high_rank.index("rrf_05.pdf") < high_rank.index("rrf_01.pdf")

    dense_first = _insert_file(live_db, name="weighted_dense.pdf", checksum="ip32wd", scope=TEST_SCOPE_A, created_at="2026-06-01T10:00:00+03:00")
    sparse_first = _insert_file(live_db, name="weighted_sparse.pdf", checksum="ip32ws", scope=TEST_SCOPE_A, created_at="2026-07-01T10:00:00+03:00")
    _insert_chunk_with_vector(
        live_db,
        file_id=dense_first,
        chunk_index=0,
        text="agirlik agirlik",
        norm="agirlik agirlik",
        vector=_vec(0.0, 1.0, 0.0),
    )
    _insert_chunk_with_vector(
        live_db,
        file_id=sparse_first,
        chunk_index=0,
        text="agirlik agirlik agirlik agirlik agirlik",
        norm="agirlik agirlik agirlik agirlik agirlik",
        vector=_vec(1.0, 0.0, 0.0),
    )

    svc_dense = _service(
        live_db,
        {"agirlik": _vec(0.0, 1.0, 0.0)},
        hybrid_fusion="weighted",
        hybrid_dense_weight=1.0,
        hybrid_sparse_weight=0.0,
    )
    svc_sparse = _service(
        live_db,
        {"agirlik": _vec(0.0, 1.0, 0.0)},
        hybrid_fusion="weighted",
        hybrid_dense_weight=0.0,
        hybrid_sparse_weight=1.0,
    )
    dense_out = svc_dense.search_hybrid("agirlik", top_k=2, user_ctx=_ctx())
    sparse_out = svc_sparse.search_hybrid("agirlik", top_k=2, user_ctx=_ctx())
    assert dense_out[0]["source"]["file_name"] == "weighted_dense.pdf"
    assert sparse_out[0]["source"]["file_name"] == "weighted_sparse.pdf"


def test_sparse_fail_closed_and_cross_scope_zero_leakage(live_db):
    allowed = _insert_file(live_db, name="allowed.pdf", checksum="ip32fa", scope=TEST_SCOPE_A)
    blocked = _insert_file(live_db, name="blocked.pdf", checksum="ip32fb", scope=TEST_SCOPE_B)
    _insert_chunk_with_vector(live_db, file_id=allowed, chunk_index=0, text="scopea metni", norm="scopea metni", vector=_vec(1.0, 0.0, 0.0))
    _insert_chunk_with_vector(live_db, file_id=blocked, chunk_index=0, text="sizintikelime sizintikelime", norm="sizintikelime sizintikelime", vector=_vec(1.0, 0.0, 0.0))

    svc = _service(live_db, {"sizintikelime": _vec(1.0, 0.0, 0.0)})
    assert svc.search_hybrid("sizintikelime", user_ctx={"user_id": "u", "tenant_id": "t", "roles": [], "allowed_doc_scopes": []}) == []
    out = svc.search_hybrid("sizintikelime", top_k=5, user_ctx=_ctx())
    assert {c["source"]["file_name"] for c in out} == {"allowed.pdf"}


def test_single_sql_cte_and_sparse_variant_selection(live_db, monkeypatch):
    file_id = _insert_file(live_db, name="sql.pdf", checksum="ip32sql", scope=TEST_SCOPE_A)
    _insert_chunk_with_vector(
        live_db,
        file_id=file_id,
        chunk_index=0,
        text="varyantkelime varyantkelime",
        norm="varyantkelime varyantkelime",
        vector=_vec(1.0, 0.0, 0.0),
    )

    class _CountingConn:
        def __init__(self, inner):
            self.inner = inner
            self.calls: list[str] = []

        def execute(self, sql, params=None):
            self.calls.append(sql)
            return self.inner.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    with live_db.connection() as inner:
        storage_repo.register_vector(inner)
        conn = _CountingConn(inner)
        monkeypatch.setattr(repo, "register_vector", lambda _: None)
        out = repo.search_hybrid(
            conn,
            query_vector=_vec(1.0, 0.0, 0.0),
            normalized_query=normalize_for_search("varyantkelime"),
            allowed_doc_scopes=[TEST_SCOPE_A],
            top_k=5,
            ef_search=64,
            filters={"file_type": "pdf", "language": "tr", "date_from": date(2026, 6, 1)},
            fusion_strategy="rrf",
            rrf_k=60,
            dense_weight=1.0,
            sparse_weight=1.0,
            sparse_variant="trgm",
        )
    assert out[0]["source"]["file_name"] == "sql.pdf"
    # ASIL İDDİA: arama TEK ana sorguyla yapılır (N+1 yok). Bunu "toplam çağrı = 3"
    # diye ölçmek kırılgandı — yeni bir `SET LOCAL` (M-7 hnsw.iterative_scan) eklenince
    # test, davranış hiç bozulmadığı hâlde kırıldı. İddia artık niyete bağlı:
    # ana CTE sorgusu TEK, geri kalan her şey oturum ayarı (SET LOCAL).
    ana = [c for c in conn.calls if "WITH sq AS" in c]
    ayar = [c for c in conn.calls if c.strip().upper().startswith("SET LOCAL")]
    assert len(ana) == 1, "arama tek ana CTE sorgusuyla yapılmalı (N+1 yok)"
    assert len(ana) + len(ayar) == len(conn.calls), "ana sorgu + SET LOCAL dışında çağrı olmamalı"
    assert any("SET LOCAL hnsw.ef_search" in c for c in ayar)
    assert any("pg_trgm.word_similarity_threshold" in c for c in ayar)
    assert "word_similarity" in ana[0]          # trgm gerçek sparse skoru
    assert ana[0].count("SELECT") >= 3


def test_hybrid_span_contains_variant_and_rank_detail(live_db):
    file_id = _insert_file(live_db, name="span.pdf", checksum="ip32span", scope=TEST_SCOPE_A)
    _insert_chunk_with_vector(
        live_db,
        file_id=file_id,
        chunk_index=0,
        text="spankelime spankelime",
        norm="spankelime spankelime",
        vector=_vec(1.0, 0.0, 0.0),
    )
    svc = _service(
        live_db,
        {"spankelime": _vec(1.0, 0.0, 0.0)},
        hybrid_sparse_variant="trgm",
    )

    _reset_tracing_for_tests()
    exporter = InMemorySpanExporter()
    configure_tracing(exporter=exporter, force=True)
    out = svc.search_hybrid("spankelime", top_k=1, user_ctx=_ctx())
    assert out
    force_flush_tracing()
    span = [s for s in exporter.get_finished_spans() if s.name == "retrieval.search_hybrid"][-1]
    assert span.attributes["sparse_variant"] == "trgm"
    assert "d1/s1" in span.attributes["rank_detail"]

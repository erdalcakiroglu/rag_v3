"""Retrieval SQL katmani (İP-3.1 dense vector search)."""

from __future__ import annotations

from datetime import date
from typing import Any

import psycopg
from pgvector import Vector

from ..database.storage_repo import register_vector


def _coerce_date(value: date | str | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(value)


# M-7: HNSW arama-zamanı ayarları — vektörün GEÇTİĞİ HER yola uygulanır
# (search_vector + search_hybrid'in dense bacağı). İP-2.4 benchmark'ı da bu iki
# fonksiyonu çağırdığı için ÖLÇÜM ile ÜRETİM aynı ayarla koşar; aksi hâlde mühür
# üretimi temsil etmez.
_ITERATIVE_SCAN_VALUES = ("off", "relaxed_order", "strict_order")


def _apply_hnsw_settings(conn: psycopg.Connection, ef_search: int, iterative_scan: str) -> None:
    """`SET LOCAL` (transaction-scope) — pool'a dönen bağlantıya SIZMAZ.

    `iterative_scan` GUC adı/değeri SQL'e string olarak gömülür (parametre olamaz),
    bu yüzden ALLOWLIST'ten geçer — config'ten gelse bile ham metin SQL'e girmez.
    """
    # Doğrulama ÖNCE (SQL'den önce): geçersiz değer hiçbir sorgu çalıştırmadan reddedilir.
    if iterative_scan not in _ITERATIVE_SCAN_VALUES:
        raise ValueError(f"Geçersiz hnsw_iterative_scan: {iterative_scan!r}")
    conn.execute(f"SET LOCAL hnsw.ef_search = {int(ef_search)};")
    # pgvector < 0.8'de bu GUC yoktur → sessizce eski davranışa düş (arama ÇÖKMESİN).
    try:
        conn.execute(f"SET LOCAL hnsw.iterative_scan = '{iterative_scan}';")
    except psycopg.errors.UndefinedObject:
        conn.rollback()
        conn.execute(f"SET LOCAL hnsw.ef_search = {int(ef_search)};")


def _where_filters(filters: dict[str, Any] | None, params: list[Any]) -> str:
    filters = filters or {}
    clauses: list[str] = []
    if filters.get("file_type"):
        clauses.append("f.file_type = %s")
        params.append(filters["file_type"])
    if filters.get("language"):
        clauses.append("f.language = %s")
        params.append(filters["language"])
    if filters.get("section"):
        clauses.append("c.section_title = %s")
        params.append(filters["section"])
    date_from = _coerce_date(filters.get("date_from"))
    if date_from is not None:
        clauses.append("f.created_at::date >= %s")
        params.append(date_from)
    date_to = _coerce_date(filters.get("date_to"))
    if date_to is not None:
        clauses.append("f.created_at::date <= %s")
        params.append(date_to)
    return (" AND " + " AND ".join(clauses)) if clauses else ""


def search_vector(
    conn: psycopg.Connection,
    *,
    query_vector: list[float],
    allowed_doc_scopes: list[str],
    top_k: int,
    ef_search: int,
    iterative_scan: str = "relaxed_order",
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not allowed_doc_scopes:
        return []
    register_vector(conn)
    _apply_hnsw_settings(conn, ef_search, iterative_scan)
    params: list[Any] = [allowed_doc_scopes]
    where = _where_filters(filters, params)
    rows = conn.execute(
        f"""
        SELECT
            c.chunk_id,
            c.chunk_text,
            1 - (v.embedding <=> %s::vector) AS score,
            f.file_id,
            f.file_name,
            c.page_number,
            c.section_title,
            f.doc_version
        FROM core_vectors v
        JOIN core_chunks c USING (chunk_id)
        JOIN core_files f USING (file_id)
        WHERE f.doc_scope = ANY(%s){where}
        ORDER BY v.embedding <=> %s::vector
        LIMIT %s;
        """,
        (Vector(query_vector), *params, Vector(query_vector), int(top_k)),
    ).fetchall()
    return [
        {
            "chunk_id": row[0],
            "text": row[1],
            "score": float(row[2]),
            "source": {
                "file_id": row[3],
                "file_name": row[4],
                "page": row[5],
                "section": row[6],
                "version": row[7],
            },
            "retrieval_method": "vector",
        }
        for row in rows
    ]


def _sparse_variant_sql(variant: str) -> tuple[str, str, str]:
    """(sq_body, match_clause, score_expr) — sparse CTE parçaları (İP-3.6).

    - simple:  önceden hesaplı `c.tsv` (simple config) + ts_rank_cd. Türkçe
      aksanlara duyarsız DEĞİL; 'İsveç' ≠ 'isvec'.
    - unaccent: sorgu ve metin `unaccent()` ile aksansızlaştırılıp tsvector'e
      çevrilir → 'İsveç'/'isvec' eşleşir. (unaccent extension; runtime tsvector.)
    - trgm: pg_trgm word_similarity — morfoloji/yazım toleransı (ekli Türkçe
      kelimeleri kısmi yakalar). tsquery YOK; ham metin benzerliği.
    """
    if variant == "simple":
        return "plainto_tsquery('simple', %s) AS query", "c.tsv @@ sq.query", "ts_rank_cd(c.tsv, sq.query)"
    if variant == "unaccent":
        return (
            "plainto_tsquery('simple', unaccent(%s)) AS query",
            "to_tsvector('simple', unaccent(c.chunk_text)) @@ sq.query",
            "ts_rank_cd(to_tsvector('simple', unaccent(c.chunk_text)), sq.query)",
        )
    if variant == "trgm":
        return "%s AS q", "sq.q <%% c.chunk_text", "word_similarity(sq.q, c.chunk_text)"
    raise ValueError(f"desteklenmeyen sparse variant: {variant}")


def search_hybrid(
    conn: psycopg.Connection,
    *,
    query_vector: list[float],
    normalized_query: str,
    allowed_doc_scopes: list[str],
    top_k: int,
    ef_search: int,
    iterative_scan: str = "relaxed_order",
    filters: dict[str, Any] | None = None,
    fusion_strategy: str,
    rrf_k: int,
    dense_weight: float,
    sparse_weight: float,
    sparse_variant: str,
) -> list[dict[str, Any]]:
    if not allowed_doc_scopes:
        return []
    register_vector(conn)
    _apply_hnsw_settings(conn, ef_search, iterative_scan)
    if sparse_variant == "trgm":
        # word_similarity eşiği (varsayılan 0.6 fazla eler); aday havuzu için düşür.
        conn.execute("SET LOCAL pg_trgm.word_similarity_threshold = 0.2;")

    dense_params: list[Any] = [allowed_doc_scopes]
    sparse_params: list[Any] = [allowed_doc_scopes]
    dense_where = _where_filters(filters, dense_params)
    sparse_where = _where_filters(filters, sparse_params)
    sq_body, match_clause, score_expr = _sparse_variant_sql(sparse_variant)

    rows = conn.execute(
        f"""
        WITH sq AS (
            SELECT {sq_body}
        ),
        dense AS (
            SELECT
                c.chunk_id,
                c.chunk_text,
                1 - (v.embedding <=> %s::vector) AS dense_score,
                row_number() OVER (ORDER BY v.embedding <=> %s::vector, c.chunk_id) AS dense_rank,
                f.file_id,
                f.file_name,
                c.page_number,
                c.section_title,
                f.doc_version
            FROM core_vectors v
            JOIN core_chunks c USING (chunk_id)
            JOIN core_files f USING (file_id)
            WHERE f.doc_scope = ANY(%s){dense_where}
            ORDER BY v.embedding <=> %s::vector, c.chunk_id
            LIMIT %s
        ),
        sparse AS (
            SELECT
                c.chunk_id,
                c.chunk_text,
                {score_expr} AS sparse_score,
                row_number() OVER (ORDER BY {score_expr} DESC, c.chunk_id) AS sparse_rank,
                f.file_id,
                f.file_name,
                c.page_number,
                c.section_title,
                f.doc_version
            FROM core_chunks c
            JOIN core_files f USING (file_id)
            CROSS JOIN sq
            WHERE f.doc_scope = ANY(%s)
              AND {match_clause}{sparse_where}
            ORDER BY {score_expr} DESC, c.chunk_id
            LIMIT %s
        ),
        fused AS (
            SELECT
                COALESCE(d.chunk_id, s.chunk_id) AS chunk_id,
                COALESCE(d.chunk_text, s.chunk_text) AS chunk_text,
                COALESCE(d.file_id, s.file_id) AS file_id,
                COALESCE(d.file_name, s.file_name) AS file_name,
                COALESCE(d.page_number, s.page_number) AS page_number,
                COALESCE(d.section_title, s.section_title) AS section_title,
                COALESCE(d.doc_version, s.doc_version) AS doc_version,
                d.dense_score,
                d.dense_rank,
                s.sparse_score,
                s.sparse_rank,
                CASE
                    WHEN %s = 'weighted' THEN
                        COALESCE(%s * d.dense_score, 0.0) + COALESCE(%s * s.sparse_score, 0.0)
                    ELSE
                        COALESCE(%s / (%s + d.dense_rank), 0.0) +
                        COALESCE(%s / (%s + s.sparse_rank), 0.0)
                END AS fused_score
            FROM dense d
            FULL OUTER JOIN sparse s USING (chunk_id)
        )
        SELECT
            chunk_id,
            chunk_text,
            fused_score,
            file_id,
            file_name,
            page_number,
            section_title,
            doc_version,
            dense_rank,
            dense_score,
            sparse_rank,
            sparse_score
        FROM fused
        ORDER BY fused_score DESC, chunk_id
        LIMIT %s;
        """,
        (
            normalized_query,
            Vector(query_vector),
            Vector(query_vector),
            *dense_params,
            Vector(query_vector),
            int(top_k),
            *sparse_params,
            int(top_k),
            fusion_strategy,
            float(dense_weight),
            float(sparse_weight),
            float(dense_weight),
            int(rrf_k),
            float(sparse_weight),
            int(rrf_k),
            int(top_k),
        ),
    ).fetchall()
    return [
        {
            "chunk_id": row[0],
            "text": row[1],
            "score": float(row[2]),
            "source": {
                "file_id": row[3],
                "file_name": row[4],
                "page": row[5],
                "section": row[6],
                "version": row[7],
            },
            "retrieval_method": "hybrid",
            "detail": {
                "dense_rank": row[8],
                "dense_score": None if row[9] is None else float(row[9]),
                "sparse_rank": row[10],
                "sparse_score": None if row[11] is None else float(row[11]),
            },
        }
        for row in rows
    ]


def lookup_document(
    conn: psycopg.Connection,
    *,
    allowed_doc_scopes: list[str],
    chunk_id: int | None = None,
    window: int = 2,
    file_id: int | None = None,
    page: int | None = None,
) -> list[dict[str, Any]]:
    if not allowed_doc_scopes:
        return []
    if chunk_id is not None:
        row = conn.execute(
            """
            SELECT f.file_id, c.chunk_index
            FROM core_chunks c JOIN core_files f USING (file_id)
            WHERE c.chunk_id = %s AND f.doc_scope = ANY(%s);
            """,
            (chunk_id, allowed_doc_scopes),
        ).fetchone()
        if row is None:
            return []
        rows = conn.execute(
            """
            SELECT
                c.chunk_id, c.chunk_text, 1.0 AS score,
                f.file_id, f.file_name, c.page_number, c.section_title, f.doc_version
            FROM core_chunks c JOIN core_files f USING (file_id)
            WHERE c.file_id = %s
              AND c.chunk_index BETWEEN %s AND %s
              AND f.doc_scope = ANY(%s)
            ORDER BY c.chunk_index;
            """,
            (row[0], row[1] - int(window), row[1] + int(window), allowed_doc_scopes),
        ).fetchall()
    else:
        if file_id is None or page is None:
            raise ValueError("lookup_document chunk_id veya (file_id + page) ister")
        rows = conn.execute(
            """
            SELECT
                c.chunk_id, c.chunk_text, 1.0 AS score,
                f.file_id, f.file_name, c.page_number, c.section_title, f.doc_version
            FROM core_chunks c JOIN core_files f USING (file_id)
            WHERE c.file_id = %s
              AND c.page_number = %s
              AND f.doc_scope = ANY(%s)
            ORDER BY c.chunk_index;
            """,
            (file_id, page, allowed_doc_scopes),
        ).fetchall()
    return [
        {
            "chunk_id": row[0],
            "text": row[1],
            "score": float(row[2]),
            "source": {
                "file_id": row[3],
                "file_name": row[4],
                "page": row[5],
                "section": row[6],
                "version": row[7],
            },
            "retrieval_method": "lookup",
        }
        for row in rows
    ]


def rerank_texts(
    conn: psycopg.Connection,
    *,
    chunk_ids: list[int],
    allowed_doc_scopes: list[str],
) -> list[dict[str, Any]]:
    if not chunk_ids or not allowed_doc_scopes:
        return []
    rows = conn.execute(
        """
        SELECT c.chunk_id, c.chunk_text
        FROM core_chunks c
        JOIN core_files f USING (file_id)
        WHERE c.chunk_id = ANY(%s)
          AND f.doc_scope = ANY(%s);
        """,
        (chunk_ids, allowed_doc_scopes),
    ).fetchall()
    text_map = {int(row[0]): row[1] for row in rows}
    return [{"chunk_id": chunk_id, "text": text_map[chunk_id]} for chunk_id in chunk_ids if chunk_id in text_map]

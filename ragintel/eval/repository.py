"""Golden set saklama ve corpus lookup SQL katmanı (İP-2.1a)."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

def eval_schema_ready(conn: psycopg.Connection) -> bool:
    """Eval tabloları elle uygulanmış mı? Kod asla kalıcı CREATE yapmaz."""
    rows = conn.execute(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = current_schema()
          AND tablename IN ('eval_golden_sets', 'eval_golden_records');
        """
    ).fetchall()
    return {row[0] for row in rows} == {"eval_golden_sets", "eval_golden_records"}


def get_set_payload_hash(conn: psycopg.Connection, version: str) -> str | None:
    row = conn.execute(
        "SELECT payload_hash FROM eval_golden_sets WHERE set_version = %s;",
        (version,),
    ).fetchone()
    return row[0] if row else None


def replace_set(
    conn: psycopg.Connection,
    *,
    version: str,
    source_path: str,
    payload_hash: str,
    records: list[dict[str, Any]],
) -> None:
    conn.execute("DELETE FROM eval_golden_sets WHERE set_version = %s;", (version,))
    conn.execute(
        """
        INSERT INTO eval_golden_sets (set_version, payload_hash, source_path, record_count, updated_at)
        VALUES (%s, %s, %s, %s, now());
        """,
        (version, payload_hash, source_path, len(records)),
    )

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO eval_golden_records (
                set_version, record_id, question, question_norm, ideal_answer,
                category, difficulty, gold_evidence, doc_scope, answerable,
                created_by, notes, payload_hash, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now());
            """,
            [
                (
                    version,
                    rec["record_id"],
                    rec["question"],
                    rec["question_norm"],
                    rec["ideal_answer"],
                    rec["category"],
                    rec["difficulty"],
                    Jsonb(rec["gold_evidence"]),
                    rec["doc_scope"],
                    rec["answerable"],
                    rec["created_by"],
                    rec["notes"],
                    rec["payload_hash"],
                )
                for rec in records
            ],
        )


def corpus_files_exist(
    conn: psycopg.Connection,
    *,
    file_name: str,
    doc_scope: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM core_files
        WHERE file_name = %s AND doc_scope = %s AND status = 'COMPLETED'
        LIMIT 1;
        """,
        (file_name, doc_scope),
    ).fetchone()
    return row is not None


def list_candidate_chunks(
    conn: psycopg.Connection,
    *,
    file_name: str,
    doc_scope: str,
    page: int | None,
    sheet: str | None,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT f.file_id, c.chunk_id, c.chunk_text_norm, c.page_number, c.sheet_name "
        "FROM core_chunks c JOIN core_files f USING (file_id) "
        "WHERE f.file_name = %s AND f.doc_scope = %s AND f.status = 'COMPLETED'"
    )
    params: list[Any] = [file_name, doc_scope]
    if page is not None:
        sql += " AND c.page_number = %s"
        params.append(page)
    if sheet is not None:
        sql += " AND c.sheet_name = %s"
        params.append(sheet)
    rows = conn.execute(sql + " ORDER BY f.file_id, c.chunk_index;", tuple(params)).fetchall()
    return [
        {
            "file_id": row[0],
            "chunk_id": row[1],
            "chunk_text_norm": row[2],
            "page_number": row[3],
            "sheet_name": row[4],
        }
        for row in rows
    ]


def list_golden_records(conn: psycopg.Connection, version: str) -> list[dict[str, Any]]:
    """Bir set_version'ın tüm golden kayıtlarını okur (retrieval benchmark girdisi)."""
    rows = conn.execute(
        """
        SELECT record_id, question, ideal_answer, category, difficulty,
               gold_evidence, doc_scope, answerable, created_by, notes
        FROM eval_golden_records WHERE set_version = %s ORDER BY record_id;
        """,
        (version,),
    ).fetchall()
    return [
        {
            "id": r[0], "question": r[1], "ideal_answer": r[2], "category": r[3],
            "difficulty": r[4], "gold_evidence": r[5], "doc_scope": r[6],
            "answerable": r[7], "created_by": r[8], "notes": r[9],
        }
        for r in rows
    ]


def count_set_records(conn: psycopg.Connection, version: str) -> int:
    row = conn.execute(
        "SELECT count(*) FROM eval_golden_records WHERE set_version = %s;",
        (version,),
    ).fetchone()
    return int(row[0])

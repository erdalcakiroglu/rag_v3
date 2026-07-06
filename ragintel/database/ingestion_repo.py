"""core_files ve metrics_ingestion DB işlemleri (İP-1).

SQL bu katmanda toplanır (İP-0 katmanlamasıyla tutarlı). Tüm işlemler bir
psycopg connection üzerinden çalışır; transaction sınırını çağıran (scanner)
yönetir — böylece dosya başına tek transaction (dedup-check + insert + metric)
atomik olur.
"""

from __future__ import annotations

from typing import Any

import psycopg


def find_file_by_checksum(conn: psycopg.Connection, checksum: str) -> dict | None:
    """Verilen checksum'a sahip mevcut kaydı döndürür (dedup için)."""
    row = conn.execute(
        "SELECT file_id, file_name, doc_version, status "
        "FROM core_files WHERE checksum = %s LIMIT 1;",
        (checksum,),
    ).fetchone()
    if row is None:
        return None
    return {"file_id": row[0], "file_name": row[1],
            "doc_version": row[2], "status": row[3]}


def max_doc_version_for_name(conn: psycopg.Connection, file_name: str) -> int | None:
    """Aynı isimli dosyalar için en yüksek doc_version; yoksa None."""
    row = conn.execute(
        "SELECT max(doc_version) FROM core_files WHERE file_name = %s;",
        (file_name,),
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def insert_core_file(
    conn: psycopg.Connection,
    *,
    file_name: str,
    file_type: str,
    file_size: int,
    checksum: str,
    source_path: str,
    doc_scope: str,
    doc_version: int,
    status: str,
    fail_reason: str | None,
    upload_user: str | None,
) -> int | None:
    """core_files'a kayıt açar; file_id döndürür.

    UNIQUE(checksum, doc_version) çakışmasında (yarış / yeniden tarama) None
    döner — çağıran bunu SKIP olarak ele alır (idempotent).
    """
    row = conn.execute(
        """
        INSERT INTO core_files
            (file_name, file_type, file_size, checksum, source_path,
             doc_scope, doc_version, status, fail_reason, upload_user)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (checksum, doc_version) DO NOTHING
        RETURNING file_id;
        """,
        (file_name, file_type, file_size, checksum, source_path,
         doc_scope, doc_version, status, fail_reason, upload_user),
    ).fetchone()
    return row[0] if row else None


def get_file(conn: psycopg.Connection, file_id: int) -> dict | None:
    """İP-2 için dosya kaydını okur (parse girdisi)."""
    row = conn.execute(
        "SELECT file_id, file_name, file_type, source_path, status "
        "FROM core_files WHERE file_id = %s;",
        (file_id,),
    ).fetchone()
    if row is None:
        return None
    return {"file_id": row[0], "file_name": row[1], "file_type": row[2],
            "source_path": row[3], "status": row[4]}


def list_pending_files(conn: psycopg.Connection, limit: int | None = None) -> list[dict]:
    """PENDING dosyaları döndürür (İP-2 batch)."""
    sql = ("SELECT file_id, file_name, file_type, source_path, status "
           "FROM core_files WHERE status = 'PENDING' ORDER BY file_id")
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql + ";").fetchall()
    return [{"file_id": r[0], "file_name": r[1], "file_type": r[2],
             "source_path": r[3], "status": r[4]} for r in rows]


def set_file_language(conn: psycopg.Connection, file_id: int, language: str | None) -> None:
    conn.execute(
        "UPDATE core_files SET language = %s WHERE file_id = %s;",
        (language, file_id),
    )


def mark_file_failed(conn: psycopg.Connection, file_id: int, reason: str) -> None:
    conn.execute(
        "UPDATE core_files SET status = 'FAILED', fail_reason = %s WHERE file_id = %s;",
        (reason, file_id),
    )


# --- İP-10 durum makinesi yardımcıları ---------------------------------------
def set_status(conn: psycopg.Connection, file_id: int, status: str) -> None:
    conn.execute("UPDATE core_files SET status = %s WHERE file_id = %s;",
                 (status, file_id))


def set_injection_flag(conn: psycopg.Connection, file_id: int, flag: bool = True) -> None:
    """İP-4: dosya düzeyi injection şüphesi bayrağı (bloklamaz)."""
    conn.execute("UPDATE core_files SET injection_flag = %s WHERE file_id = %s;",
                 (flag, file_id))


# --- İP-9 QC + skor yardımcıları ---------------------------------------------
def set_quality_score(conn: psycopg.Connection, file_id: int, score: float | None) -> None:
    conn.execute("UPDATE core_files SET quality_score = %s WHERE file_id = %s;",
                 (score, file_id))


def list_chunks_for_qc(conn: psycopg.Connection, file_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT chunk_id, chunk_index, chunk_text_norm, token_count "
        "FROM core_chunks WHERE file_id = %s ORDER BY chunk_index;",
        (file_id,),
    ).fetchall()
    return [{"chunk_id": r[0], "chunk_index": r[1], "chunk_text_norm": r[2],
             "token_count": r[3]} for r in rows]


def delete_chunk_qc_findings(conn: psycopg.Connection, file_id: int) -> None:
    """İP-9 tekrar çalıştırıldığında yinelenmesin: chunk-düzeyi QC bulgularını sil."""
    conn.execute(
        "DELETE FROM qc_findings WHERE file_id = %s AND finding = ANY(%s);",
        (file_id, ["empty_chunk", "duplicate_chunk", "too_short", "too_long"]),
    )


def step_metrics(conn: psycopg.Connection, file_id: int) -> list[tuple[str, dict]]:
    rows = conn.execute(
        "SELECT step, detail FROM metrics_ingestion WHERE file_id = %s ORDER BY metric_id;",
        (file_id,),
    ).fetchall()
    return [(s, d or {}) for s, d in rows]


def completed_without_score(conn: psycopg.Connection) -> list[int]:
    rows = conn.execute(
        "SELECT file_id FROM core_files "
        "WHERE status = 'COMPLETED' AND quality_score IS NULL ORDER BY file_id;"
    ).fetchall()
    return [r[0] for r in rows]


def revert_to_pending(conn: psycopg.Connection, file_id: int) -> None:
    """PROCESSING -> PENDING (altyapı hatası; retry_count ARTMAZ)."""
    conn.execute(
        "UPDATE core_files SET status = 'PENDING' WHERE file_id = %s;", (file_id,))


def list_failed_retryable(conn: psycopg.Connection, max_retry: int) -> list[dict]:
    rows = conn.execute(
        "SELECT file_id, retry_count FROM core_files "
        "WHERE status = 'FAILED' AND retry_count < %s ORDER BY file_id;",
        (max_retry,),
    ).fetchall()
    return [{"file_id": r[0], "retry_count": r[1]} for r in rows]


def increment_retry_and_pending(conn: psycopg.Connection, file_id: int) -> None:
    conn.execute(
        "UPDATE core_files SET status = 'PENDING', retry_count = retry_count + 1, "
        "fail_reason = NULL WHERE file_id = %s;", (file_id,))


def list_stuck_processing(conn: psycopg.Connection, minutes: int) -> list[int]:
    rows = conn.execute(
        "SELECT file_id FROM core_files WHERE status = 'PROCESSING' "
        "AND updated_at < now() - make_interval(mins => %s) ORDER BY file_id;",
        (minutes,),
    ).fetchall()
    return [r[0] for r in rows]


def status_counts(conn: psycopg.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, count(*) FROM core_files GROUP BY status;").fetchall()
    return {s: c for s, c in rows}


def intake_made_copy(conn: psycopg.Connection, file_id: int) -> bool:
    """İntake raw kopya oluşturdu mu? (İP-1 intake metriği detail.copied).
    False/kayıt yok -> intake'te başarısız (oversize gibi) -> REPROCESS intake'ten."""
    row = conn.execute(
        "SELECT (detail->>'copied')::boolean FROM metrics_ingestion "
        "WHERE file_id = %s AND step = 'intake' ORDER BY metric_id DESC LIMIT 1;",
        (file_id,),
    ).fetchone()
    return bool(row[0]) if row and row[0] is not None else False


def insert_metric(
    conn: psycopg.Connection,
    *,
    file_id: int,
    step: str,
    duration_ms: int,
    ok: bool,
    detail: dict[str, Any] | None = None,
) -> None:
    """metrics_ingestion'a adım metriği yazar."""
    from psycopg.types.json import Jsonb

    conn.execute(
        """
        INSERT INTO metrics_ingestion (file_id, step, duration_ms, ok, detail)
        VALUES (%s, %s, %s, %s, %s);
        """,
        (file_id, step, duration_ms, ok,
         Jsonb(detail) if detail is not None else None),
    )


def insert_qc_finding(
    conn: psycopg.Connection,
    *,
    file_id: int,
    finding: str,
    detail: str | None = None,
    chunk_id: int | None = None,
) -> None:
    """qc_findings'e bir kalite/güvenlik bulgusu ekler (bloklamaz, işaretler)."""
    conn.execute(
        """
        INSERT INTO qc_findings (file_id, chunk_id, finding, detail)
        VALUES (%s, %s, %s, %s);
        """,
        (file_id, chunk_id, finding, detail),
    )

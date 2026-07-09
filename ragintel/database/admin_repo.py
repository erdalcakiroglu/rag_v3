"""FAZ 7 — Admin panel SQL katmanı: app_config yazımı + core_files + qc_findings.

Config yazımı DOĞRULANMIŞ değer bekler (pydantic doğrulama API katmanında; bozuk config
DB'ye YAZILMAZ). Buradaki fonksiyonlar yalnızca kalıcılık (persistence).
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb


# --- Config ------------------------------------------------------------------
def list_config(conn: psycopg.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT config_key, config_value, description, updated_by, updated_at "
        "FROM app_config ORDER BY config_key;"
    ).fetchall()
    return [
        {"group": r[0], "value": r[1], "description": r[2], "updated_by": r[3], "updated_at": str(r[4])}
        for r in rows
    ]


def write_config(conn: psycopg.Connection, *, group: str, value: dict, updated_by: str,
                 description: str | None = None) -> None:
    """DOĞRULANMIŞ config değerini yazar (upsert). Çağıran pydantic ile doğrulamış olmalı."""
    conn.execute(
        "INSERT INTO app_config (config_key, config_value, description, updated_by, updated_at) "
        "VALUES (%s, %s, %s, %s, now()) "
        "ON CONFLICT (config_key) DO UPDATE SET config_value = EXCLUDED.config_value, "
        "description = COALESCE(EXCLUDED.description, app_config.description), "
        "updated_by = EXCLUDED.updated_by, updated_at = now();",
        (group, Jsonb(value), description, updated_by),
    )


# --- core_files --------------------------------------------------------------
def list_files(conn: psycopg.Connection, *, limit: int = 300) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT file_id, file_name, file_type, doc_scope, status, quality_score, "
        "retry_count, fail_reason, injection_flag, created_at, updated_at "
        "FROM core_files ORDER BY updated_at DESC LIMIT %s;",
        (limit,),
    ).fetchall()
    return [
        {"file_id": r[0], "file_name": r[1], "file_type": r[2], "doc_scope": r[3], "status": r[4],
         "quality_score": float(r[5]) if r[5] is not None else None, "retry_count": r[6],
         "fail_reason": r[7], "injection_flag": bool(r[8]),
         "created_at": str(r[9]), "updated_at": str(r[10])}
        for r in rows
    ]


# --- qc_findings -------------------------------------------------------------
def list_findings(conn: psycopg.Connection, *, finding_type: str | None = None,
                  only_open: bool = False, limit: int = 500) -> list[dict[str, Any]]:
    sql = ("SELECT q.finding_id, q.file_id, f.file_name, q.finding, q.detail, q.resolved, q.created_at "
           "FROM qc_findings q JOIN core_files f USING (file_id) WHERE 1=1")
    params: list[Any] = []
    if finding_type:
        sql += " AND q.finding = %s"
        params.append(finding_type)
    if only_open:
        sql += " AND NOT q.resolved"
    sql += " ORDER BY q.resolved, q.created_at DESC LIMIT %s;"
    params.append(limit)
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [
        {"finding_id": r[0], "file_id": r[1], "file_name": r[2], "finding": r[3],
         "detail": r[4], "resolved": bool(r[5]), "created_at": str(r[6])}
        for r in rows
    ]


def finding_counts(conn: psycopg.Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT count(*) FILTER (WHERE NOT resolved), count(*) FILTER (WHERE resolved), count(*) "
        "FROM qc_findings;"
    ).fetchone()
    return {"open": int(row[0]), "resolved": int(row[1]), "total": int(row[2])}


def set_finding_resolved(conn: psycopg.Connection, finding_id: int, resolved: bool) -> int:
    cur = conn.execute("UPDATE qc_findings SET resolved = %s WHERE finding_id = %s;", (resolved, finding_id))
    return cur.rowcount

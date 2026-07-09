"""FAZ 6/7 — kullanıcı deposu SQL katmanı (AuthN + admin CRUD). Token HASH'iyle çözüm."""

from __future__ import annotations

from typing import Any

import psycopg


def users_table_ready(conn: psycopg.Connection) -> bool:
    """`ragintel.users` elle uygulanmış mı? (Kod kalıcı CREATE yapmaz.)"""
    row = conn.execute(
        "SELECT 1 FROM pg_tables WHERE schemaname = current_schema() AND tablename = 'users';"
    ).fetchone()
    return row is not None


def _has_column(conn: psycopg.Connection, column: str) -> bool:
    """DDL-geçiş güvenliği: is_admin (FAZ7) uygulanmadan da auth çalışsın."""
    row = conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name = 'users' AND column_name = %s;",
        (column,),
    ).fetchone()
    return row is not None


def get_active_user_by_token_hash(conn: psycopg.Connection, token_hash: str) -> dict[str, Any] | None:
    """Aktif kullanıcıyı token HASH'iyle çözer. Bulunamazsa None (resolver 401'e çevirir)."""
    has_admin = _has_column(conn, "is_admin")
    cols = "user_id, display_name, allowed_doc_scopes, tenant_id, roles" + (", is_admin" if has_admin else "")
    row = conn.execute(
        f"SELECT {cols} FROM ragintel.users WHERE api_token_hash = %s AND active LIMIT 1;",
        (token_hash,),
    ).fetchone()
    if row is None:
        return None
    return {
        "user_id": row[0],
        "display_name": row[1],
        "allowed_doc_scopes": list(row[2] or []),
        "tenant_id": row[3] or "default",
        "roles": list(row[4] or ["user"]),
        "is_admin": bool(row[5]) if has_admin else False,
    }


# --- FAZ 7 admin CRUD --------------------------------------------------------
def list_users(conn: psycopg.Connection) -> list[dict[str, Any]]:
    has_admin = _has_column(conn, "is_admin")
    admin_col = "is_admin" if has_admin else "false AS is_admin"
    rows = conn.execute(
        f"SELECT user_id, display_name, allowed_doc_scopes, active, {admin_col}, created_at "
        "FROM ragintel.users ORDER BY user_id;"
    ).fetchall()
    return [
        {"user_id": r[0], "display_name": r[1], "allowed_doc_scopes": list(r[2] or []),
         "active": bool(r[3]), "is_admin": bool(r[4]), "created_at": str(r[5])}
        for r in rows
    ]


def user_exists(conn: psycopg.Connection, user_id: str) -> bool:
    return conn.execute("SELECT 1 FROM ragintel.users WHERE user_id = %s;", (user_id,)).fetchone() is not None


def create_user(conn: psycopg.Connection, *, user_id: str, token_hash: str, display_name: str | None,
                allowed_doc_scopes: list[str], is_admin: bool = False) -> None:
    """Yeni kullanıcı (token HASH'i yazılır; düz metin ÇAĞIRANDA tek kez gösterilir)."""
    has_admin = _has_column(conn, "is_admin")
    if has_admin:
        conn.execute(
            "INSERT INTO ragintel.users (user_id, api_token_hash, display_name, allowed_doc_scopes, is_admin) "
            "VALUES (%s, %s, %s, %s, %s);",
            (user_id, token_hash, display_name, allowed_doc_scopes, is_admin),
        )
    else:
        conn.execute(
            "INSERT INTO ragintel.users (user_id, api_token_hash, display_name, allowed_doc_scopes) "
            "VALUES (%s, %s, %s, %s);",
            (user_id, token_hash, display_name, allowed_doc_scopes),
        )


def set_scopes(conn: psycopg.Connection, user_id: str, scopes: list[str]) -> int:
    cur = conn.execute("UPDATE ragintel.users SET allowed_doc_scopes = %s WHERE user_id = %s;",
                       (scopes, user_id))
    return cur.rowcount


def set_active(conn: psycopg.Connection, user_id: str, active: bool) -> int:
    cur = conn.execute("UPDATE ragintel.users SET active = %s WHERE user_id = %s;", (active, user_id))
    return cur.rowcount

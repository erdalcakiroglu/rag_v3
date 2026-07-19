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
    # M-12: email/status DDL uygulanmadan da liste çalışsın (sabit fallback).
    email_col = "email" if _has_column(conn, "email") else "NULL::text AS email"
    status_col = "status" if _has_column(conn, "status") else "'active'::text AS status"
    rows = conn.execute(
        f"SELECT user_id, display_name, allowed_doc_scopes, active, {admin_col}, created_at, "
        f"{email_col}, {status_col} "
        "FROM ragintel.users ORDER BY (status = 'pending') DESC, user_id;"
        if _has_column(conn, "status") else
        f"SELECT user_id, display_name, allowed_doc_scopes, active, {admin_col}, created_at, "
        f"{email_col}, {status_col} FROM ragintel.users ORDER BY user_id;"
    ).fetchall()
    return [
        {"user_id": r[0], "display_name": r[1], "allowed_doc_scopes": list(r[2] or []),
         "active": bool(r[3]), "is_admin": bool(r[4]), "created_at": str(r[5]),
         "email": r[6], "status": r[7]}
        for r in rows
    ]


def user_exists(conn: psycopg.Connection, user_id: str) -> bool:
    return conn.execute("SELECT 1 FROM ragintel.users WHERE user_id = %s;", (user_id,)).fetchone() is not None


# --- M-12 email+şifre self-kayıt (admin-onaylı) + giriş ----------------------
def email_auth_ready(conn: psycopg.Connection) -> bool:
    """FAZ6_Sema_Ek1 (email/password_hash/status) uygulanmış mı? Uygulanmadan self-kayıt
    çalışamaz (Bearer yolu etkilenmez); uç bunu 503'e çevirir (fail-safe)."""
    return _has_column(conn, "email") and _has_column(conn, "password_hash") and _has_column(conn, "status")


def email_exists(conn: psycopg.Connection, email_lower: str) -> bool:
    """Büyük/küçük harf-duyarsız email kaydı var mı (lower(email) benzersiz index'iyle uyumlu)."""
    return conn.execute(
        "SELECT 1 FROM ragintel.users WHERE email IS NOT NULL AND lower(email) = %s LIMIT 1;",
        (email_lower,),
    ).fetchone() is not None


def register_pending_user(conn: psycopg.Connection, *, user_id: str, email_lower: str,
                          password_hash: str, display_name: str | None) -> None:
    """Self-kayıt: status='pending', active=false, scope=[] (BOŞ = fail-closed), TOKEN YOK.
    Admin onaylayana dek ne giriş ne veri. Şifre yalnızca HASH'iyle yazılır."""
    conn.execute(
        "INSERT INTO ragintel.users "
        "(user_id, email, password_hash, display_name, status, active, allowed_doc_scopes) "
        "VALUES (%s, %s, %s, %s, 'pending', false, '{}');",
        (user_id, email_lower, password_hash, display_name),
    )


def get_auth_user_by_email(conn: psycopg.Connection, email_lower: str) -> dict[str, Any] | None:
    """Giriş için email→kimlik kaydı (password_hash + status + active). Yoksa None
    (çağıran dummy-verify ile timing'i eşitler → enumeration nötr)."""
    has_admin = _has_column(conn, "is_admin")
    admin_col = ", is_admin" if has_admin else ""
    row = conn.execute(
        f"SELECT user_id, password_hash, status, active, allowed_doc_scopes{admin_col} "
        "FROM ragintel.users WHERE email IS NOT NULL AND lower(email) = %s LIMIT 1;",
        (email_lower,),
    ).fetchone()
    if row is None:
        return None
    return {
        "user_id": row[0], "password_hash": row[1], "status": row[2], "active": bool(row[3]),
        "allowed_doc_scopes": list(row[4] or []),
        "is_admin": bool(row[5]) if has_admin else False,
    }


def set_session_token(conn: psycopg.Connection, user_id: str, token_hash: str) -> int:
    """Login başarısında opak oturum token'ının HASH'ini api_token_hash'e yazar (tek aktif
    oturum: yeni login eskisini geçersizler). Sonraki Bearer istekleri mevcut resolver'la çözülür."""
    cur = conn.execute(
        "UPDATE ragintel.users SET api_token_hash = %s, updated_at = now() WHERE user_id = %s;",
        (token_hash, user_id),
    )
    return cur.rowcount


def approve_user(conn: psycopg.Connection, user_id: str, scopes: list[str]) -> int:
    """Admin onayı: status='active' + active=true + scope ata. Yalnız pending kullanıcıyı
    onaylar (zaten aktif/servis kullanıcısını yanlışlıkla değiştirmez)."""
    cur = conn.execute(
        "UPDATE ragintel.users SET status = 'active', active = true, allowed_doc_scopes = %s, "
        "updated_at = now() WHERE user_id = %s AND status = 'pending';",
        (scopes, user_id),
    )
    return cur.rowcount


def reject_user(conn: psycopg.Connection, user_id: str) -> int:
    """Admin reddi/askıya alma: status='disabled' + active=false (Bearer/login her ikisi de kapanır)."""
    cur = conn.execute(
        "UPDATE ragintel.users SET status = 'disabled', active = false, updated_at = now() "
        "WHERE user_id = %s;",
        (user_id,),
    )
    return cur.rowcount


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

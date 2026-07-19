"""M-13 — konuşma geçmişi ("sohbetlerim") SQL katmanı.

conversation_id = session_id (= checkpoint thread_id). Transcript conversation_messages'tan
okunur (checkpoint'e bağımlı DEĞİL). SAHİPLİK fail-closed: her okuma/yazma user_id eşleşmesi
ister; başka kullanıcının conversation'ı → yok gibi (None/0 → uç 404). Şema ELLE uygulanır
(FAZ7_Sema_Ek2); uygulanmadan record_turn sessizce atlar (ask'ı bozmaz).
"""

from __future__ import annotations

from typing import Any

import psycopg


def conversations_ready(conn: psycopg.Connection) -> bool:
    """FAZ7_Sema_Ek2 uygulanmış mı? Uygulanmadan geçmiş yazımı ATLANIR (ask bozulmaz)."""
    rows = conn.execute(
        "SELECT count(*) FROM pg_tables WHERE schemaname = current_schema() "
        "AND tablename IN ('conversations', 'conversation_messages');"
    ).fetchone()
    return bool(rows and rows[0] == 2)


def _title_from(question: str) -> str:
    q = " ".join((question or "").split())
    return (q[:50] + "…") if len(q) > 50 else (q or "(boş soru)")


def record_turn(conn: psycopg.Connection, *, conversation_id: str, user_id: str,
                question: str, answer: str, scopes: list[str], trace_id: str | None) -> bool:
    """Bir turu (soru+cevap) kaydeder. Yeni session → conversation oluşur (title=ilk soru,
    scope SNAPSHOT). Var olan → last_at güncellenir. SAHİPLİK: conversation başka kullanıcıya
    aitse HİÇBİR ŞEY yazılmaz (çapraz-yazım yok) → False döner. Sahipse/yeniyse → True."""
    row = conn.execute(
        "SELECT user_id FROM ragintel.conversations WHERE conversation_id = %s;",
        (conversation_id,),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO ragintel.conversations (conversation_id, user_id, title, allowed_doc_scopes) "
            "VALUES (%s, %s, %s, %s);",
            (conversation_id, user_id, _title_from(question), list(scopes or [])),
        )
    elif row[0] != user_id:
        return False                              # başka kullanıcının sohbeti → fail-closed
    else:
        conn.execute(
            "UPDATE ragintel.conversations SET last_at = now() WHERE conversation_id = %s;",
            (conversation_id,),
        )
    seq = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM ragintel.conversation_messages WHERE conversation_id = %s;",
        (conversation_id,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO ragintel.conversation_messages (conversation_id, seq, role, content, trace_id) "
        "VALUES (%s, %s, 'user', %s, %s), (%s, %s, 'assistant', %s, %s);",
        (conversation_id, seq + 1, question, trace_id,
         conversation_id, seq + 2, answer, trace_id),
    )
    return True


def list_conversations(conn: psycopg.Connection, user_id: str) -> list[dict[str, Any]]:
    """Kullanıcının canlı (silinmemiş) sohbetleri, en son etkileşim üstte."""
    rows = conn.execute(
        "SELECT conversation_id, title, allowed_doc_scopes, created_at, last_at "
        "FROM ragintel.conversations WHERE user_id = %s AND deleted_at IS NULL "
        "ORDER BY last_at DESC;",
        (user_id,),
    ).fetchall()
    return [
        {"conversation_id": r[0], "title": r[1], "allowed_doc_scopes": list(r[2] or []),
         "created_at": str(r[3]), "last_at": str(r[4])}
        for r in rows
    ]


def get_conversation_messages(conn: psycopg.Connection, conversation_id: str,
                              user_id: str) -> list[dict[str, Any]] | None:
    """SAHİPLİK fail-closed: sohbet kullanıcıya ait ve silinmemişse mesajları (sırayla);
    aksi halde None (uç 404'e çevirir — varlık sızmaz, M-2 deseni)."""
    owner = conn.execute(
        "SELECT 1 FROM ragintel.conversations "
        "WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL;",
        (conversation_id, user_id),
    ).fetchone()
    if owner is None:
        return None
    rows = conn.execute(
        "SELECT seq, role, content, trace_id, created_at FROM ragintel.conversation_messages "
        "WHERE conversation_id = %s ORDER BY seq;",
        (conversation_id,),
    ).fetchall()
    return [
        {"seq": r[0], "role": r[1], "content": r[2], "trace_id": r[3], "created_at": str(r[4])}
        for r in rows
    ]


def soft_delete(conn: psycopg.Connection, conversation_id: str, user_id: str) -> int:
    """Kendi sohbetini soft-delete eder (deleted_at). Veri/audit DURUR; liste'de görünmez.
    Sahiplik: yalnız kendi + halihazırda silinmemiş → rowcount (0 = yok/başkasının → 404)."""
    cur = conn.execute(
        "UPDATE ragintel.conversations SET deleted_at = now() "
        "WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL;",
        (conversation_id, user_id),
    )
    return cur.rowcount

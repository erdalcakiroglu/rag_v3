"""M-13 — konuşma geçmişi uçları ("sohbetlerim"; Bearer auth, sahiplik fail-closed).

Transcript conversation_messages'tan gelir (checkpoint'e bağımlı DEĞİL → Redis down olsa da
Postgres'ten erişilir). SAHİPLİK: başka kullanıcının conversation_id'si → 404 (varlık sızmaz,
M-2 deseni). /api/ask konuşmayı ctx.user_id'ye yazar (bkz. runtime._record_history).
"""

from __future__ import annotations

from fastapi import Header, HTTPException

from ..database import conversation_repo
from .auth import Unauthorized, bearer_token


def register_conversation_routes(app, rt) -> None:

    def _ctx(authorization: str | None) -> dict:
        try:
            return rt().resolver.resolve(bearer_token(authorization))
        except Unauthorized as exc:
            raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"})

    @app.get("/api/conversations")
    def list_conversations(authorization: str | None = Header(default=None)):
        ctx = _ctx(authorization)
        with rt().db.connection() as conn:
            if not conversation_repo.conversations_ready(conn):
                return {"conversations": []}     # şema uygulanmadıysa boş (bozulmaz)
            return {"conversations": conversation_repo.list_conversations(conn, ctx["user_id"])}

    @app.get("/api/conversations/{conversation_id}")
    def get_conversation(conversation_id: str, authorization: str | None = Header(default=None)):
        ctx = _ctx(authorization)
        with rt().db.connection() as conn:
            msgs = (conversation_repo.get_conversation_messages(conn, conversation_id, ctx["user_id"])
                    if conversation_repo.conversations_ready(conn) else None)
        if msgs is None:
            # sahiplik fail-closed: başkasının/yok → 404 (403 DEĞİL — varlık sızmaz, M-2).
            raise HTTPException(404, "Konuşma bulunamadı")
        return {"conversation_id": conversation_id, "messages": msgs}

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str, authorization: str | None = Header(default=None)):
        ctx = _ctx(authorization)
        with rt().db.connection() as conn:
            ok = (conversation_repo.conversations_ready(conn)
                  and conversation_repo.soft_delete(conn, conversation_id, ctx["user_id"]) > 0)
        if not ok:
            raise HTTPException(404, "Konuşma bulunamadı")   # yok/başkasının → 404
        return {"status": "ok", "conversation_id": conversation_id, "deleted": True}

"""FAZ 7 — Admin panel API (/admin sayfası + /api/admin/* veri uçları).

Her veri ucu fail-closed: geçersiz token → 401; admin değil → 403. Config yazımı
KAYDETMEDEN ÖNCE ilgili pydantic modeliyle doğrulanır (bozuk config DB'ye yazılmaz).
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Body, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError

from ..config.settings import GROUP_MODELS
from ..database import admin_repo, user_repo
from .auth import Forbidden, Unauthorized, bearer_token, hash_token, require_admin

_STATIC = Path(__file__).parent / "static"


class UserCreate(BaseModel):
    user_id: str = Field(min_length=1)
    display_name: str | None = None
    allowed_doc_scopes: list[str] = Field(default_factory=lambda: ["default"])
    is_admin: bool = False


def register_admin_routes(app, rt) -> None:
    """Admin uçlarını app'e ekler. `rt` → çalışan RagRuntime döndüren çağrılabilir."""

    def _admin(authorization: str | None) -> dict:
        """Auth + admin (fail-closed). 401 (token) → 403 (admin değil)."""
        try:
            ctx = rt().resolver.resolve(bearer_token(authorization))
        except Unauthorized as exc:
            raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"})
        try:
            return require_admin(ctx)
        except Forbidden as exc:
            raise HTTPException(403, str(exc))

    # -- UI sayfası (kabuk; veri uçları guard'lı, JS 403'ü gösterir) -----------
    @app.get("/admin", response_class=HTMLResponse)
    def admin_page() -> str:
        return (_STATIC / "admin.html").read_text(encoding="utf-8")

    # -- 1) CONFIG ------------------------------------------------------------
    @app.get("/api/admin/config")
    def get_config(authorization: str | None = Header(default=None)):
        _admin(authorization)
        with rt().db.connection() as conn:
            groups = admin_repo.list_config(conn)
        return {"groups": groups, "known_groups": sorted(GROUP_MODELS)}

    @app.post("/api/admin/config/{group}")
    def save_config(group: str, value: dict = Body(...), authorization: str | None = Header(default=None)):
        ctx = _admin(authorization)
        model = GROUP_MODELS.get(group)
        if model is None:
            raise HTTPException(400, f"Bilinmeyen config grubu: {group}")
        # KRİTİK: kaydetmeden ÖNCE doğrula — bozuk config DB'ye YAZILMAZ.
        try:
            validated = model(**value).model_dump()
        except ValidationError as exc:
            raise HTTPException(400, {"error": "config doğrulama hatası", "detail": exc.errors()})
        with rt().db.connection() as conn:
            admin_repo.write_config(conn, group=group, value=validated, updated_by=ctx["user_id"])
        return {"status": "ok", "group": group, "updated_by": ctx["user_id"], "value": validated}

    # -- 2) USERS -------------------------------------------------------------
    @app.get("/api/admin/users")
    def get_users(authorization: str | None = Header(default=None)):
        _admin(authorization)
        with rt().db.connection() as conn:
            return {"users": user_repo.list_users(conn)}

    @app.post("/api/admin/users")
    def create_user(req: UserCreate, authorization: str | None = Header(default=None)):
        _admin(authorization)
        raw = secrets.token_urlsafe(24)  # token yalnızca burada üretilir; hash saklanır
        with rt().db.connection() as conn:
            if user_repo.user_exists(conn, req.user_id):
                raise HTTPException(409, f"Kullanıcı zaten var: {req.user_id}")
            user_repo.create_user(conn, user_id=req.user_id, token_hash=hash_token(raw),
                                  display_name=req.display_name, allowed_doc_scopes=req.allowed_doc_scopes,
                                  is_admin=req.is_admin)
        # Token TEK KEZ döner — bir daha gösterilemez (DB'de yalnızca hash).
        return {"user_id": req.user_id, "token": raw,
                "note": "Token yalnızca ŞİMDİ gösterilir; güvenli saklayın. DB'de yalnızca hash tutulur."}

    @app.post("/api/admin/users/{user_id}/scopes")
    def set_scopes(user_id: str, scopes: list[str] = Body(..., embed=True),
                   authorization: str | None = Header(default=None)):
        _admin(authorization)
        with rt().db.connection() as conn:
            if user_repo.set_scopes(conn, user_id, scopes) == 0:
                raise HTTPException(404, f"Kullanıcı yok: {user_id}")
        return {"status": "ok", "user_id": user_id, "allowed_doc_scopes": scopes}

    @app.post("/api/admin/users/{user_id}/active")
    def set_active(user_id: str, active: bool = Body(..., embed=True),
                   authorization: str | None = Header(default=None)):
        ctx = _admin(authorization)
        # Kendi hesabını deaktive etme koruması.
        if not active and user_id == ctx["user_id"]:
            raise HTTPException(400, "Kendi hesabınızı deaktive edemezsiniz")
        with rt().db.connection() as conn:
            if user_repo.set_active(conn, user_id, active) == 0:
                raise HTTPException(404, f"Kullanıcı yok: {user_id}")
        return {"status": "ok", "user_id": user_id, "active": active}

    # -- 3) DOKÜMAN & QC ------------------------------------------------------
    @app.get("/api/admin/files")
    def get_files(authorization: str | None = Header(default=None)):
        _admin(authorization)
        with rt().db.connection() as conn:
            return {"files": admin_repo.list_files(conn)}

    @app.get("/api/admin/qc")
    def get_qc(finding_type: str | None = None, only_open: bool = False,
               authorization: str | None = Header(default=None)):
        _admin(authorization)
        with rt().db.connection() as conn:
            return {"findings": admin_repo.list_findings(conn, finding_type=finding_type, only_open=only_open),
                    "counts": admin_repo.finding_counts(conn)}

    @app.post("/api/admin/qc/{finding_id}/resolve")
    def resolve_qc(finding_id: int, resolved: bool = Body(True, embed=True),
                   authorization: str | None = Header(default=None)):
        _admin(authorization)
        with rt().db.connection() as conn:
            if admin_repo.set_finding_resolved(conn, finding_id, resolved) == 0:
                raise HTTPException(404, f"Bulgu yok: {finding_id}")
            counts = admin_repo.finding_counts(conn)
        return {"status": "ok", "finding_id": finding_id, "resolved": resolved, "counts": counts}

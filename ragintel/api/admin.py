"""FAZ 7 — Admin panel API (/admin sayfası + /api/admin/* veri uçları).

Her veri ucu fail-closed: geçersiz token → 401; admin değil → 403. Config yazımı
KAYDETMEDEN ÖNCE ilgili pydantic modeliyle doğrulanır (bozuk config DB'ye yazılmaz).
"""

from __future__ import annotations

import secrets
from copy import deepcopy
from pathlib import Path

from fastapi import Body, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError

from ..config.settings import GROUP_MODELS
from ..config.ui_schema import UnknownField, build_ui_schema, resolve_field
from ..database import admin_repo, user_repo
from .auth import Forbidden, Unauthorized, bearer_token, hash_token, require_admin
from .config_errors import field_errors, safe_details

_STATIC = Path(__file__).parent / "static"


class UserCreate(BaseModel):
    user_id: str = Field(min_length=1)
    display_name: str | None = None
    allowed_doc_scopes: list[str] = Field(default_factory=lambda: ["default"])
    is_admin: bool = False


class ConfigFieldPatch(BaseModel):
    """M-5 alan-bazlı düzenleme: tek parametre değişimi = tek kayıt."""

    path: list[str] = Field(min_length=1)   # ör. ["weights", "parse"]
    value: object = None


def _deep_set(data: dict, path: list[str], value: object) -> dict:
    """`path` boyunca (kopya üzerinde) tek yaprağı yazar; ara düğümler dict olmalı."""
    out = deepcopy(data)
    node = out
    for part in path[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
        node[part] = child
        node = child
    node[path[-1]] = value
    return out


def _deep_get(data: dict, path: list[str]):
    node: object = data
    for part in path:
        node = node[part]           # type: ignore[index]
    return node


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
    # M-5: form ŞEMASI pydantic'ten türer (GROUP_MODELS tek doğruluk kaynağı).
    # Statik yol — /api/admin/config/{group} ile çakışmaması için ondan ÖNCE tanımlı.
    @app.get("/api/admin/config/schema")
    def get_config_schema(authorization: str | None = Header(default=None)):
        _admin(authorization)
        return build_ui_schema()

    @app.get("/api/admin/config")
    def get_config(authorization: str | None = Header(default=None)):
        _admin(authorization)
        with rt().db.connection() as conn:
            groups = admin_repo.list_config(conn)
        return {"groups": groups, "known_groups": sorted(GROUP_MODELS)}

    @app.post("/api/admin/config/{group}")
    def save_config(group: str, value: dict = Body(...), authorization: str | None = Header(default=None)):
        """Ham JSON modu: TÜM grubu yazar (gelişmiş/fallback ekran)."""
        ctx = _admin(authorization)
        model = GROUP_MODELS.get(group)
        if model is None:
            raise HTTPException(400, f"Bilinmeyen config grubu: {group}")
        # KRİTİK: kaydetmeden ÖNCE doğrula — bozuk config DB'ye YAZILMAZ.
        try:
            validated = model(**value).model_dump()
        except ValidationError as exc:
            raise HTTPException(400, {"error": "config doğrulama hatası",
                                      "fields": field_errors(exc), "detail": safe_details(exc)})
        with rt().db.connection() as conn:
            admin_repo.write_config(conn, group=group, value=validated, updated_by=ctx["user_id"])
        return {"status": "ok", "group": group, "updated_by": ctx["user_id"], "value": validated}

    @app.patch("/api/admin/config/{group}")
    def patch_config(group: str, patch: ConfigFieldPatch,
                     authorization: str | None = Header(default=None)):
        """M-5: ALAN-BAZLI kayıt — tek parametre değişimi = tek `jsonb_set` yazımı.

        Doğrulama TÜM GRUP üzerinde yapılır (çapraz-alan koruması: ör. quality
        ağırlık toplamı, max_top_k ≥ default_top_k). Hata varsa DB'ye HİÇBİR ŞEY
        yazılmaz; mesaj alana iliştirilmiş TR olarak döner.
        """
        ctx = _admin(authorization)
        model = GROUP_MODELS.get(group)
        if model is None:
            raise HTTPException(400, f"Bilinmeyen config grubu: {group}")
        # 1) Yol modelde var mı? (uydurma anahtar jsonb_set ile YARATILMASIN)
        try:
            resolve_field(model, patch.path)
        except UnknownField as exc:
            raise HTTPException(400, {"error": f"Bilinmeyen alan: {exc.args[0]}",
                                      "fields": [{"field": ".".join(patch.path),
                                                  "path": patch.path,
                                                  "message": "Bu alan modelde tanımlı değil."}]})

        with rt().db.connection() as conn:
            current = admin_repo.get_config_value(conn, group)
            # 2) Mevcut grup + tek alan değişimi → TÜM grubu yeniden doğrula.
            candidate = _deep_set(current or {}, patch.path, patch.value)
            try:
                validated = model(**candidate).model_dump()
            except ValidationError as exc:
                raise HTTPException(400, {"error": "config doğrulama hatası",
                                          "fields": field_errors(exc), "detail": safe_details(exc)})
            # 3) Yazılan değer, istemcinin ham girdisi değil DOĞRULANMIŞ/dönüştürülmüş
            #    olandır ("16" → 16). Tek yaprak yazılır; grubun geri kalanına dokunulmaz.
            leaf = _deep_get(validated, patch.path)
            if current is None:      # grup DB'de hiç yok → tam grup yazımına düş
                admin_repo.write_config(conn, group=group, value=validated, updated_by=ctx["user_id"])
            else:
                admin_repo.patch_config_field(conn, group=group, path=patch.path,
                                              value=leaf, updated_by=ctx["user_id"])
        return {"status": "ok", "group": group, "path": patch.path,
                "value": leaf, "updated_by": ctx["user_id"]}

    @app.get("/api/admin/config/{group}/audit")
    def get_config_audit(group: str, authorization: str | None = Header(default=None)):
        """M-6: SALT-OKUNUR — grup için son 10 değişiklik (old/new + kim/ne zaman).

        Kayıtlar TRIGGER'la yazılır (panelden VE doğrudan SQL'den yapılan
        değişiklikler yakalanır) — bu uç yalnızca gösterir, YAZMAZ.
        """
        _admin(authorization)
        with rt().db.connection() as conn:
            return {"group": group, "audit": admin_repo.list_config_audit(conn, group)}

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

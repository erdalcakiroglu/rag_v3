"""M-12 — Email+şifre kimlik uçları: self-kayıt (admin-onaylı) + giriş.

FAZ 6 Bearer modelinin ÜSTÜNE gelir (yerine değil): login başarısında üretilen opak
session token'ı mevcut Bearer plumbing'iyle çözülür. Politika config-first
(`app_config('auth')`). Şifre argon2id; düz metin ne DB'de ne log'da.

Enumeration savunması:
  • register: allowlist-dışı → 403 (POLİTİKA — hesap değil). Duplicate email → jenerik
    başarı (varlık doğrulanmaz).
  • login: bilinmeyen email + yanlış şifre AYNI 401 nötr mesaj; dummy-verify ile timing düz.
    pending/disabled durumu ancak DOĞRU şifreyle görülebilir (hesabın sahibi bilir).
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException
from pydantic import BaseModel, Field

from ..database import user_repo
from ..observability.logging import get_logger
from . import passwords
from .auth import hash_token

_LOG = get_logger("api.user_auth")

# Nötr mesajlar (enumeration önleme) — bilinmeyen email ve yanlış şifre için AYNI.
_BAD_CREDENTIALS = "Email veya şifre hatalı."
_REGISTER_OK = ("Kaydınız alındı. Hesabınız bir yönetici tarafından onaylandıktan sonra "
                "giriş yapabilirsiniz.")


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)  # politika (min uzunluk) config'ten


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


def register_auth_routes(app, rt) -> None:
    """/api/register + /api/login (PUBLIC — admin guard YOK; fail-closed politika içeride)."""

    def _auth_cfg():
        return rt().cfg.group("auth")

    @app.post("/api/register")
    def register(req: RegisterRequest):
        # ŞİFRE ASLA LOGLANMAZ — req nesnesini log'a verme.
        cfg = _auth_cfg()
        email = passwords.normalize_email(req.email)
        if not passwords.valid_email_format(email):
            raise HTTPException(400, "Geçerli bir email adresi girin.")
        # Alan-adı allowlist (fail-closed: liste boşsa hiçbir kayıt kabul edilmez).
        if not passwords.domain_allowed(email, list(cfg.allowed_email_domains)):
            _LOG.info("register_rejected_domain", domain=passwords.email_domain(email))
            raise HTTPException(403, "Bu email alan-adı ile kayıt açık değil. "
                                     "Erişim için yöneticinizle iletişime geçin.")
        pol = passwords.password_policy_error(req.password, min_length=int(cfg.password_min_length))
        if pol:
            raise HTTPException(400, pol)

        with rt().db.connection() as conn:
            if not user_repo.email_auth_ready(conn):
                raise HTTPException(503, "Email/şifre kaydı henüz etkin değil (şema uygulanmadı).")
            # Enumeration nötr: email zaten kayıtlıysa YENİ kayıt oluşturmadan aynı jenerik
            # yanıtı döner (varlık sızmaz). user_id = normalize email (benzersiz).
            if not user_repo.email_exists(conn, email):
                pw_hash = passwords.hash_password(req.password)
                try:
                    user_repo.register_pending_user(
                        conn, user_id=email, email_lower=email,
                        password_hash=pw_hash, display_name=req.full_name.strip())
                except Exception:
                    # Yarış/nadir çakışma: yine jenerik başarı (varlık/iç hata sızdırma).
                    _LOG.warning("register_insert_conflict")
        _LOG.info("register_pending", email_domain=passwords.email_domain(email))
        return {"status": "pending", "message": _REGISTER_OK}

    @app.post("/api/login")
    def login(req: LoginRequest):
        # ŞİFRE ASLA LOGLANMAZ.
        email = passwords.normalize_email(req.email)
        with rt().db.connection() as conn:
            if not user_repo.email_auth_ready(conn):
                raise HTTPException(503, "Email/şifre girişi henüz etkin değil (şema uygulanmadı).")
            user = user_repo.get_auth_user_by_email(conn, email)
            # Şifre doğrulaması ÖNCE (bilinmeyen kullanıcıda dummy-verify → timing düz, nötr 401).
            if user is None or not passwords.verify_password(user.get("password_hash"), req.password):
                if user is None:
                    passwords.dummy_verify(req.password)
                _LOG.warning("login_failed")
                raise HTTPException(401, _BAD_CREDENTIALS, headers={"WWW-Authenticate": "Bearer"})
            # Şifre doğru — durum kapıları (yalnız hesabın sahibi bu mesajları görebilir).
            if user["status"] == "pending":
                raise HTTPException(403, "Hesabınız onay bekliyor. Yönetici onayından sonra "
                                         "giriş yapabilirsiniz.")
            if user["status"] != "active" or not user["active"]:
                raise HTTPException(403, "Hesabınız devre dışı. Yöneticinizle iletişime geçin.")
            # Oturum aç: opak token üret, HASH'ini api_token_hash'e yaz (mevcut Bearer yolu).
            raw = secrets.token_urlsafe(24)
            user_repo.set_session_token(conn, user["user_id"], hash_token(raw))
        # Token rotasyonu: bu kullanıcının ESKİ (artık geçersiz) token'ı cache'te kalmasın.
        rt().session_cache.invalidate_user(user["user_id"])
        _LOG.info("login_ok", user_id=user["user_id"])
        return {"status": "ok", "token": raw, "user_id": user["user_id"],
                "is_admin": bool(user.get("is_admin", False)),
                "note": "Bu token Authorization: Bearer olarak gönderilir; güvenli saklayın."}

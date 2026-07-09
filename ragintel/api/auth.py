"""FAZ 6 — AuthN: Bearer token → user_ctx (fail-closed).

Resolver ARAYÜZÜ (`UserResolver`) LDAP/AD (FAZ 9) altlığıdır: DB implementasyonu bir
seçenek; LDAP resolver aynı arayüzü sağlayıp runtime'a enjekte edilebilir (kod değişmez).

Güvenlik: token yalnızca HASH'iyle (sha256) çözülür; düz metin ne DB'de ne log'da tutulur.
Eksik/geçersiz/inaktif token → `Unauthorized` (API 401'e çevirir; eski X-User-Id yolu YOK).
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from ..database import user_repo
from ..observability.logging import get_logger

_LOG = get_logger("api.auth")


class Unauthorized(Exception):
    """Kimlik doğrulanamadı → 401 (fail-closed)."""


def hash_token(token: str) -> str:
    """Token → sha256 hex (DB'de saklanan/aranan biçim). Düz metin asla tutulmaz."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class UserResolver(Protocol):
    def resolve(self, token: str | None) -> dict:
        """Geçerli token → user_ctx dict; aksi halde `Unauthorized` yükseltir."""
        ...


class DbUserResolver:
    """`ragintel.users` üzerinden token→user_ctx. LDAP resolver bunun yerine geçebilir."""

    def __init__(self, db):
        self.db = db

    def resolve(self, token: str | None) -> dict:
        if not token or not token.strip():
            raise Unauthorized("Authorization Bearer token gerekli")
        with self.db.connection() as conn:
            user = user_repo.get_active_user_by_token_hash(conn, hash_token(token.strip()))
        if user is None:
            # Token değerini LOGLAMA (hash bile) — yalnızca reddi kaydet.
            _LOG.warning("auth_rejected")
            raise Unauthorized("Geçersiz veya pasif token")
        return {
            "user_id": user["user_id"],
            "tenant_id": user["tenant_id"],
            "roles": user["roles"],
            "allowed_doc_scopes": user["allowed_doc_scopes"],
            "is_admin": bool(user.get("is_admin", False)),  # FAZ 7
        }


class Forbidden(Exception):
    """Yetkisiz (admin değil) → 403 (fail-closed)."""


def require_admin(user_ctx: dict) -> dict:
    """Admin değilse Forbidden (403). Fail-closed: is_admin yoksa/False → reddet."""
    if not user_ctx.get("is_admin"):
        raise Forbidden("Bu işlem için admin yetkisi gerekli")
    return user_ctx


def bearer_token(authorization: str | None) -> str | None:
    """`Authorization: Bearer <token>` başlığından token'ı çıkarır (yoksa None)."""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
        return parts[1].strip()
    return None

"""M-12 Redis eki — login oturumlarının OTORİTER Redis deposu (session store).

M-10'daki 'DB çözümü cache'i'nden FARKLIDIR: burada login session Redis'te YAŞAR (kaynak),
DB'de değil. Güvenlik sözleşmesi (net kodlanır + testli):
  - Login (email+şifre) oturumu → Redis. Redis down = login çalışmaz (oturum çözülemez/kurulamaz).
  - Admin/servis Bearer token → DB `api_token_hash`. Redis'ten BAĞIMSIZ (Redis down olsa da çalışır).
  - Resolver önce Redis'e bakar; Redis'te yoksa/erişilemezse DB'ye düşer → 401 ya da DB token.
Yani Redis down: login token'ları 401 alır ama admin DB yolu SAĞLAM (fail-closed de fail-open da değil).

Güvenlik: anahtar token'ın HASH'idir (sha256); düz token TUTULMAZ. Kalıcılık YOK (RAM):
Redis restart = tüm oturumlar düşer → yeniden login. TTL SLIDING (her erişimde tazelenir →
hareketsizlik zaman aşımı). Tüm Redis çağrıları fail-safe: create False döner, get None döner.
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from ..observability.logging import get_logger

_LOG = get_logger("api.session_store")

_SESS = "ragintel:session:"    # session:<token_hash> -> user_ctx JSON (+exp)
_USER = "ragintel:usess:"      # usess:<user_id> -> {token_hash,...} (iptal indeksi)


class SessionStore(Protocol):
    def create(self, token_hash: str, ctx: dict[str, Any]) -> bool: ...
    def get(self, token_hash: str) -> dict[str, Any] | None: ...
    def delete(self, token_hash: str) -> None: ...
    def invalidate_user(self, user_id: str) -> None: ...
    def ping(self) -> bool: ...


class NullSessionStore:
    """Redis yapılandırılmamışken: oturum OLUŞTURULAMAZ (login 503), get miss. Admin DB
    token yolu resolver'da DB'ye düşerek çalışmaya devam eder (Redis'e bağımlı DEĞİL)."""

    enabled = False

    def create(self, token_hash: str, ctx: dict[str, Any]) -> bool:
        return False

    def get(self, token_hash: str) -> dict[str, Any] | None:
        return None

    def delete(self, token_hash: str) -> None:
        return None

    def invalidate_user(self, user_id: str) -> None:
        return None

    def ping(self) -> bool:
        return False


class RedisSessionStore:
    """Redis-destekli, FAIL-SAFE login oturum deposu. create() başarı/başarısızlık BİLDİRİR
    (login Redis down'da 503'e çevirsin diye); get()/delete()/invalidate() sessiz-güvenlidir."""

    enabled = True

    def __init__(self, client: Any, ttl_seconds: int = 28800) -> None:
        self._r = client
        self._ttl = max(60, int(ttl_seconds))

    def create(self, token_hash: str, ctx: dict[str, Any]) -> bool:
        """Login oturumu yaz. True=yazıldı; False=Redis down/hata → login 503 (spec: login çalışmaz)."""
        try:
            uid = ctx.get("user_id")
            payload = json.dumps({**ctx, "exp": int(time.time()) + self._ttl}, ensure_ascii=False)
            pipe = self._r.pipeline()
            pipe.setex(_SESS + token_hash, self._ttl, payload)
            if uid:
                pipe.sadd(_USER + uid, token_hash)
                pipe.expire(_USER + uid, self._ttl + 300)
            pipe.execute()
            return True
        except Exception as exc:
            _LOG.warning("session_create_failed", error=str(exc)[:120])
            return False

    def get(self, token_hash: str) -> dict[str, Any] | None:
        """Oturumu getir + SLIDING: bulununca TTL'i _ttl'e tazele (hareketsizlik zaman aşımı;
        mutlak değil). Fail-safe: Redis down/parse → None (resolver DB'ye düşer)."""
        try:
            raw = self._r.get(_SESS + token_hash)
            if raw is None:
                return None
            ctx = json.loads(raw)
            pipe = self._r.pipeline()
            pipe.expire(_SESS + token_hash, self._ttl)   # sliding: erişimde tazele
            uid = ctx.get("user_id")
            if uid:
                pipe.expire(_USER + uid, self._ttl + 300)
            pipe.execute()
            return ctx
        except Exception as exc:
            _LOG.warning("session_get_failed", error=str(exc)[:120])
            return None

    def delete(self, token_hash: str) -> None:
        """Tek oturumu sil (logout). usess indeksinden de düşür (best-effort)."""
        try:
            raw = self._r.get(_SESS + token_hash)
            pipe = self._r.pipeline()
            pipe.delete(_SESS + token_hash)
            if raw:
                try:
                    uid = json.loads(raw).get("user_id")
                    if uid:
                        pipe.srem(_USER + uid, token_hash)
                except Exception:
                    pass
            pipe.execute()
        except Exception as exc:
            _LOG.warning("session_delete_failed", error=str(exc)[:120])

    def invalidate_user(self, user_id: str) -> None:
        """Kullanıcının TÜM oturumlarını sil (scope/deaktive/ret → anında geçersiz)."""
        if not user_id:
            return
        try:
            key = _USER + user_id
            hashes = self._r.smembers(key) or []
            pipe = self._r.pipeline()
            for h in hashes:
                pipe.delete(_SESS + (h.decode() if isinstance(h, bytes) else h))
            pipe.delete(key)
            pipe.execute()
        except Exception as exc:
            _LOG.warning("session_invalidate_failed", user_id=user_id, error=str(exc)[:120])

    def ping(self) -> bool:
        try:
            return bool(self._r.ping())
        except Exception:
            return False


def build_session_store(url: str, ttl_seconds: int = 28800) -> SessionStore:
    """URL boşsa NullSessionStore (login Redis'siz çalışmaz; admin DB token yolu sürer).
    Doluysa Redis istemcisi; import/kurulum hatası → fail-open (Null)."""
    if not url or not url.strip():
        return NullSessionStore()
    try:
        import redis  # lazy: Redis yapılandırılmadıysa import gerekmesin
        client = redis.Redis.from_url(url.strip(), decode_responses=True,
                                      socket_connect_timeout=2, socket_timeout=2)
        _LOG.info("session_store_enabled")
        return RedisSessionStore(client, ttl_seconds=ttl_seconds)
    except Exception as exc:
        _LOG.warning("session_store_disabled_build_failed", error=str(exc)[:160])
        return NullSessionStore()

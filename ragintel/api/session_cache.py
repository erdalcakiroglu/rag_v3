"""M-10/0 EK — oturum-token çözümünün OPSİYONEL Redis cache'i.

Amaç: her istekte `users` tablosuna atılan token→user_ctx sorgusunu, kısa TTL'li bir
Redis lookup'ıyla hızlandırmak. **DB kaynak-otoriter; Redis yalnızca hızlandırıcı:**
- Redis yapılandırılmamışsa (URL boş) → `NullSessionCache`: her şey miss/no-op, resolver
  DB'ye gider (mevcut davranış — Bearer yolu regresyonsuz).
- Redis erişilemezse → istisna YUTULUR, miss gibi davranır → yine DB. Cache asla auth'u
  düşürmez (fail-safe: hızlandırıcının arızası, kimliğin arızası değildir — M-4 felsefesi).

Güvenlik: cache anahtarı token'ın HASH'idir (DB'dekiyle aynı sha256); düz token TUTULMAZ.
İptal (deaktive/ret/scope değişimi/çıkış-rotasyonu) `invalidate_user` ile ANINDA silinir;
TTL yalnızca doğrudan-SQL değişiklikleri için üst-sınır güvenlik ağıdır.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from ..observability.logging import get_logger

_LOG = get_logger("api.session_cache")

_SESS = "ragintel:sess:"      # sess:<token_hash> -> user_ctx JSON
_USER = "ragintel:usess:"     # usess:<user_id> -> {token_hash, ...} (invalidation indeksi)


class SessionCache(Protocol):
    def get(self, token_hash: str) -> dict[str, Any] | None: ...
    def put(self, token_hash: str, ctx: dict[str, Any]) -> None: ...
    def invalidate_user(self, user_id: str) -> None: ...
    def ping(self) -> bool: ...


class NullSessionCache:
    """Redis yokken: her şey miss/no-op. Resolver doğrudan DB kullanır (mevcut davranış)."""

    enabled = False

    def get(self, token_hash: str) -> dict[str, Any] | None:
        return None

    def put(self, token_hash: str, ctx: dict[str, Any]) -> None:
        return None

    def invalidate_user(self, user_id: str) -> None:
        return None

    def ping(self) -> bool:
        return False


class RedisSessionCache:
    """Redis-destekli, FAIL-SAFE oturum cache'i. Her redis çağrısı istisna-güvenlidir:
    hata → miss/no-op + tek satır log; asla resolver'a sızmaz."""

    enabled = True

    def __init__(self, client: Any, ttl_seconds: int = 60) -> None:
        self._r = client
        self._ttl = max(1, int(ttl_seconds))

    def get(self, token_hash: str) -> dict[str, Any] | None:
        try:
            raw = self._r.get(_SESS + token_hash)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:      # redis down / parse → miss (DB'ye düşülür)
            _LOG.warning("session_cache_get_failed", error=str(exc)[:120])
            return None

    def put(self, token_hash: str, ctx: dict[str, Any]) -> None:
        try:
            uid = ctx.get("user_id")
            payload = json.dumps(ctx, ensure_ascii=False)
            pipe = self._r.pipeline()
            pipe.setex(_SESS + token_hash, self._ttl, payload)
            if uid:
                # İptal indeksi: kullanıcının canlı token-hash'leri. TTL'i biraz uzun tut ki
                # cache girdisi dolmadan indeks kaybolmasın (invalidate hep hepsini bulsun).
                pipe.sadd(_USER + uid, token_hash)
                pipe.expire(_USER + uid, self._ttl + 300)
            pipe.execute()
        except Exception as exc:
            _LOG.warning("session_cache_put_failed", error=str(exc)[:120])

    def invalidate_user(self, user_id: str) -> None:
        """Kullanıcının TÜM cache'lenmiş oturumlarını siler (deaktive/ret/scope/çıkış)."""
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
            _LOG.warning("session_cache_invalidate_failed", user_id=user_id, error=str(exc)[:120])

    def ping(self) -> bool:
        try:
            return bool(self._r.ping())
        except Exception:
            return False


def build_session_cache(url: str, ttl_seconds: int = 60) -> SessionCache:
    """URL boşsa NullSessionCache (cache devre dışı). Doluysa Redis istemcisi kurar.
    redis import/kurulum hatası → fail-open (Null): auth çalışmaya devam eder."""
    if not url or not url.strip():
        return NullSessionCache()
    try:
        import redis  # lazy: Redis yapılandırılmadıysa import de gerekmesin
        client = redis.Redis.from_url(url.strip(), decode_responses=True,
                                      socket_connect_timeout=2, socket_timeout=2)
        _LOG.info("session_cache_enabled")
        return RedisSessionCache(client, ttl_seconds=ttl_seconds)
    except Exception as exc:
        _LOG.warning("session_cache_disabled_build_failed", error=str(exc)[:160])
        return NullSessionCache()

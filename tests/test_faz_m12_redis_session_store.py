"""M-12 Redis eki — login oturumlarının OTORİTER Redis deposu (session store).

Güvenlik sözleşmesi (net kodlanır + testli):
  - Login (email+şifre) oturumu Redis'te YAŞAR; resolver önce Redis'e bakar (+SLIDING TTL).
  - Redis DOWN → login token'ları 401 alır ama ADMIN/SERVİS DB token yolu SAĞLAM (fail-closed
    de fail-open da DEĞİL — ayrım kesin).
  - Login Redis'e yazamıyorsa (down/yok) → oturum kurulamaz (create False → uç 503).
  - Kalıcılık yok; TTL SLIDING (hareketsizlikte tazelenir).
  - Health: configured+down → degraded + açıklama; disabled (yapılandırılmamış) degrade etmez.
  - Kod-dışı sözleşme: compose redis servisi (port/param/parola-env).

Gerçek Redis GEREKMEZ — FakeRedis in-memory taklit.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from ragintel.api.auth import DbUserResolver, Unauthorized, hash_token
from ragintel.api.session_store import (
    NullSessionStore,
    RedisSessionStore,
    build_session_store,
)

KOK = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------- FakeRedis
class FakeRedis:
    def __init__(self):
        self._s: dict[str, str] = {}
        self._sets: dict[str, set] = {}
        self.ttls: dict[str, int] = {}

    def get(self, k): return self._s.get(k)
    def setex(self, k, ttl, v): self._s[k] = v; self.ttls[k] = ttl
    def sadd(self, k, *vals): self._sets.setdefault(k, set()).update(vals)
    def srem(self, k, *vals): self._sets.get(k, set()).difference_update(vals)
    def smembers(self, k): return set(self._sets.get(k, set()))
    def expire(self, k, ttl): self.ttls[k] = ttl
    def delete(self, *keys):
        for k in keys:
            self._s.pop(k, None); self._sets.pop(k, None)
    def ping(self): return True
    def pipeline(self): return self
    def execute(self): return []


class BrokenRedis:
    def __getattr__(self, _):
        def boom(*a, **k): raise RuntimeError("redis down")
        return boom


# --------------------------------------------------------------- Null store
def test_null_store_cannot_create_and_is_disabled():
    s = NullSessionStore()
    assert s.enabled is False
    assert s.create("h", {"user_id": "u"}) is False   # login → 503
    assert s.get("h") is None
    s.delete("h"); s.invalidate_user("u")             # atmamalı
    assert s.ping() is False


def test_build_session_store_empty_url_is_null():
    assert isinstance(build_session_store(""), NullSessionStore)
    assert isinstance(build_session_store("   "), NullSessionStore)


# --------------------------------------------------------------- Redis store
def test_create_get_roundtrip_with_ttl_and_exp():
    fr = FakeRedis()
    s = RedisSessionStore(fr, ttl_seconds=120)
    assert s.enabled is True
    ctx = {"user_id": "u1", "tenant_id": "default", "roles": ["user"],
           "allowed_doc_scopes": ["muhasebe"], "is_admin": False}
    assert s.create("h1", ctx) is True
    got = s.get("h1")
    assert got["allowed_doc_scopes"] == ["muhasebe"] and got["user_id"] == "u1"
    assert "exp" in got                                # spec: {..., exp}
    assert fr.ttls["ragintel:session:h1"] == 120


def test_get_slides_ttl_on_access():
    """SLIDING: her get TTL'i _ttl'e tazeler (hareketsizlik zaman aşımı, mutlak değil)."""
    fr = FakeRedis()
    s = RedisSessionStore(fr, ttl_seconds=100)
    s.create("h1", {"user_id": "u1"})
    fr.ttls["ragintel:session:h1"] = 5                 # zaman geçmiş gibi (TTL düşmüş)
    s.get("h1")
    assert fr.ttls["ragintel:session:h1"] == 100       # erişimde geri 100'e tazelendi


def test_create_returns_false_when_redis_down():
    """Redis down → create False → login 503 (spec: login çalışmaz)."""
    s = RedisSessionStore(BrokenRedis(), ttl_seconds=60)
    assert s.create("h", {"user_id": "u"}) is False
    assert s.get("h") is None                          # get de fail-safe (None)
    assert s.ping() is False


def test_delete_removes_session_and_usess_entry():
    fr = FakeRedis()
    s = RedisSessionStore(fr, ttl_seconds=60)
    s.create("h1", {"user_id": "u1"})
    s.delete("h1")
    assert s.get("h1") is None
    assert "h1" not in fr._sets.get("ragintel:usess:u1", set())   # indeksten de düştü


def test_invalidate_user_clears_all_sessions():
    fr = FakeRedis()
    s = RedisSessionStore(fr, ttl_seconds=60)
    s.create("hA", {"user_id": "u1"})
    s.create("hB", {"user_id": "u1"})
    s.create("hC", {"user_id": "u2"})
    s.invalidate_user("u1")
    assert s.get("hA") is None and s.get("hB") is None
    assert s.get("hC") is not None


# ---------------------------------------------------- Resolver: iki yol + Redis-down
class _FakeDb:
    @contextmanager
    def connection(self):
        yield None


@pytest.fixture
def db_admin(monkeypatch):
    """DB'de yalnız ADMINTOK çözen sahte user_repo (sayaçlı)."""
    from ragintel.database import user_repo
    calls = {"n": 0}
    admin_h = hash_token("ADMINTOK")

    def fake_get(conn, token_hash):
        calls["n"] += 1
        if token_hash == admin_h:
            return {"user_id": "admin", "display_name": "A", "allowed_doc_scopes": ["default"],
                    "tenant_id": "default", "roles": ["user"], "is_admin": True}
        return None

    monkeypatch.setattr(user_repo, "get_active_user_by_token_hash", fake_get)
    return calls


def _login_ctx(uid, scopes, admin=False):
    return {"user_id": uid, "tenant_id": "default", "roles": ["user"],
            "allowed_doc_scopes": scopes, "is_admin": admin}


def test_redis_login_session_resolves_without_db(db_admin):
    store = RedisSessionStore(FakeRedis())
    store.create(hash_token("LOGINTOK"), _login_ctx("ali@x.com", ["muhasebe"]))
    r = DbUserResolver(_FakeDb(), store=store)
    ctx = r.resolve("LOGINTOK")
    assert ctx["user_id"] == "ali@x.com" and ctx["allowed_doc_scopes"] == ["muhasebe"]
    assert db_admin["n"] == 0                           # Redis'te bulundu → DB'ye hiç gidilmedi


def test_admin_db_token_resolves_via_db(db_admin):
    r = DbUserResolver(_FakeDb(), store=RedisSessionStore(FakeRedis()))
    ctx = r.resolve("ADMINTOK")
    assert ctx["is_admin"] is True
    assert db_admin["n"] == 1                           # Redis miss → DB


def test_REDIS_DOWN_login_token_401_but_admin_db_ok(db_admin):
    """GÜVENLİK SÖZLEŞMESİ: Redis down iken login token 401, admin DB token SAĞLAM."""
    r = DbUserResolver(_FakeDb(), store=RedisSessionStore(BrokenRedis()))
    # login token: Redis'te (down) → miss → DB'de yok → 401
    with pytest.raises(Unauthorized):
        r.resolve("LOGINTOK")
    # admin DB token: Redis miss (down) → DB hit → çalışır
    assert r.resolve("ADMINTOK")["is_admin"] is True


def test_null_store_resolver_falls_to_db(db_admin):
    """Redis yapılandırılmamış (Null): login token yok sayılır (miss→DB→401), admin DB OK."""
    r = DbUserResolver(_FakeDb())                       # store verilmedi → Null
    with pytest.raises(Unauthorized):
        r.resolve("LOGINTOK")
    assert r.resolve("ADMINTOK")["is_admin"] is True


# ---------------------------------------------------- /api/health redis sinyali
class _SahteRuntime:
    from ragintel.api.runtime import RagRuntime
    health = RagRuntime.health

    def __init__(self, store):
        from types import SimpleNamespace
        self.cfg = SimpleNamespace(group=lambda n: SimpleNamespace(health_timeout=1.0))
        self.langfuse = SimpleNamespace(enabled=False)
        self.is_warm = True
        self.session_store = store

    def _check_db(self): return "ok"
    def _check_http(self, *a, **k): return "ok"


def test_health_redis_ok_when_enabled():
    assert _SahteRuntime(RedisSessionStore(FakeRedis())).health()["checks"]["redis"] == "ok"


def test_health_redis_disabled_when_null_does_not_degrade():
    h = _SahteRuntime(NullSessionStore()).health()
    assert h["checks"]["redis"] == "disabled"
    assert h["status"] == "healthy"                     # yapılandırılmamış → degrade YOK


def test_health_redis_down_is_degraded_with_note():
    h = _SahteRuntime(RedisSessionStore(BrokenRedis())).health()
    assert h["checks"]["redis"] == "down"
    assert h["status"] == "degraded"                    # login yolu çökük
    assert "login" in h["redis_note"] and "admin" in h["redis_note"]   # açıklama


def test_derive_health_status_redis_rules():
    from ragintel.api.runtime import derive_health_status
    ok = {"db": "ok", "ollama": "ok", "tei": "ok"}
    assert derive_health_status({**ok, "redis": "ok"}) == "healthy"
    assert derive_health_status({**ok, "redis": "disabled"}) == "healthy"     # opsiyonel
    assert derive_health_status({**ok, "redis": "down"}) == "degraded"        # configured+down
    assert derive_health_status({**ok, "redis": "down", "db": "down"}) == "unhealthy"  # db baskın


# ---------------------------------------------------- compose kod-dışı sözleşmesi
def _compose_code_lines():
    text = (KOK / "docker-compose.h200.yml").read_text(encoding="utf-8")
    return [ln for ln in text.splitlines() if not ln.strip().startswith("#")]


def test_compose_has_redis_service_with_cache_params():
    code = "\n".join(_compose_code_lines())
    assert "\n  redis:" in code and "image: redis:7" in code
    for token in ("network_mode: host", "--maxmemory 4gb", "--appendonly no",
                  "allkeys-lru", "requirepass"):
        assert token in code, f"compose redis servisinde eksik: {token!r}"


def test_compose_redis_password_is_not_hardcoded():
    import re
    code = "\n".join(_compose_code_lines())
    assert '--requirepass "$$RAGINTEL_REDIS_PASSWORD"' in code
    for m in re.finditer(r"requirepass\s+(\S+)", code):
        assert "$$RAGINTEL_REDIS_PASSWORD" in m.group(1), \
            f"requirepass literal parola içeriyor olabilir: {m.group(1)!r}"


def test_compose_server_and_healthcheck_ports_agree():
    import re
    code = "\n".join(_compose_code_lines())
    sp = re.search(r"--port\s+(\d+)", code)
    hp = re.search(r"redis-cli\s+-p\s+(\d+)", code)
    assert sp and hp and sp.group(1) == hp.group(1), \
        "redis sunucu portu healthcheck -p ile uyuşmuyor (yanlış-port yalanı)"


def test_redis_url_not_in_deploy_zorunlu_sirlar():
    import re
    m = re.search(r"ZORUNLU_SIRLAR=\(([^)]*)\)", (KOK / "deploy.sh").read_text(encoding="utf-8"))
    assert m and "RAGINTEL_REDIS" not in m.group(1)   # opsiyonel → dağıtımı bloklamaz


def test_api_not_hostage_to_redis():
    assert not any("depends_on" in ln for ln in _compose_code_lines())

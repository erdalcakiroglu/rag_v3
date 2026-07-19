"""M-10/0 EK — oturum-token Redis cache'i (OPSİYONEL hızlandırıcı, DB kaynak-otoriter).

Kanıtlanan sözleşmeler:
  1. Cache MISS/arıza → DB (Bearer yolu regresyonsuz; Redis auth'u ASLA düşürmez).
  2. Cache HIT → DB'ye GİDİLMEZ (hızlandırıcı gerçekten çalışıyor).
  3. Negatif (geçersiz token) cache'lenmez (iptal edilen token TTL boyunca yaşamasın;
     rastgele token cache'i şişirmesin).
  4. İptal (scope/deaktive/ret/login-rotasyon) → invalidate_user ANINDA temizler.
  5. Redis YOKKEN (Null) her şey miss/no-op — mevcut davranış birebir.
  6. Kod-dışı sözleşme: compose redis servisi (M-10 "deploy dosyaları da test edilir").

Gerçek Redis GEREKMEZ — FakeRedis in-memory taklit; redis-py/argon2 gibi ağ yok.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from ragintel.api.auth import DbUserResolver, Unauthorized, hash_token
from ragintel.api.session_cache import (
    NullSessionCache,
    RedisSessionCache,
    build_session_cache,
)

KOK = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------- FakeRedis
class FakeRedis:
    """RedisSessionCache'in kullandığı asgari yüzey (get/setex/sadd/smembers/delete/
    expire/ping/pipeline). decode_responses=True gibi str döndürür."""

    def __init__(self):
        self._s: dict[str, str] = {}
        self._sets: dict[str, set] = {}
        self.ttls: dict[str, int] = {}

    def get(self, k): return self._s.get(k)
    def setex(self, k, ttl, v): self._s[k] = v; self.ttls[k] = ttl
    def sadd(self, k, *vals): self._sets.setdefault(k, set()).update(vals)
    def smembers(self, k): return set(self._sets.get(k, set()))
    def expire(self, k, ttl): self.ttls[k] = ttl
    def delete(self, *keys):
        for k in keys:
            self._s.pop(k, None); self._sets.pop(k, None)
    def ping(self): return True
    # pipeline: sırayı koruyarak ANINDA uygular (test için tamponlamaya gerek yok)
    def pipeline(self): return self
    def execute(self): return []


class BrokenRedis:
    """Her çağrıda patlar → fail-safe kanıtı (cache arızası auth'u düşürmemeli)."""
    def __getattr__(self, _):
        def boom(*a, **k): raise RuntimeError("redis down")
        return boom


# --------------------------------------------------------------- Null cache
def test_null_cache_is_noop_and_disabled():
    c = NullSessionCache()
    assert c.enabled is False
    assert c.get("x") is None
    c.put("x", {"user_id": "u"}); c.invalidate_user("u")   # atmamalı
    assert c.ping() is False


def test_build_session_cache_empty_url_is_null():
    assert isinstance(build_session_cache(""), NullSessionCache)
    assert isinstance(build_session_cache("   "), NullSessionCache)


# --------------------------------------------------------------- Redis cache
def test_redis_cache_put_get_roundtrip_with_ttl():
    fr = FakeRedis()
    c = RedisSessionCache(fr, ttl_seconds=45)
    assert c.enabled is True
    c.put("h1", {"user_id": "u1", "allowed_doc_scopes": ["a"]})
    assert c.get("h1") == {"user_id": "u1", "allowed_doc_scopes": ["a"]}
    assert fr.ttls["ragintel:sess:h1"] == 45           # TTL uygulandı


def test_invalidate_user_clears_all_that_users_sessions():
    fr = FakeRedis()
    c = RedisSessionCache(fr, ttl_seconds=60)
    c.put("hA", {"user_id": "u1"})
    c.put("hB", {"user_id": "u1"})      # aynı kullanıcı, iki oturum
    c.put("hC", {"user_id": "u2"})
    c.invalidate_user("u1")
    assert c.get("hA") is None and c.get("hB") is None  # u1'in HEPSİ gitti
    assert c.get("hC") == {"user_id": "u2"}             # u2 etkilenmedi


def test_redis_cache_is_failsafe_on_broken_client():
    c = RedisSessionCache(BrokenRedis(), ttl_seconds=60)
    assert c.get("h") is None            # get patlamaz → miss
    c.put("h", {"user_id": "u"})         # put patlamaz → sessiz
    c.invalidate_user("u")               # patlamaz
    assert c.ping() is False


# --------------------------------------------------- Resolver + cache entegrasyon
class _FakeDb:
    @contextmanager
    def connection(self):
        yield None


@pytest.fixture
def db_counter(monkeypatch):
    """user_repo.get_active_user_by_token_hash'i sayan sahte ile değiştirir."""
    from ragintel.database import user_repo
    calls = {"n": 0}
    good = hash_token("GOODTOK")

    def fake_get(conn, token_hash):
        calls["n"] += 1
        if token_hash == good:
            return {"user_id": "u1", "display_name": "U1", "allowed_doc_scopes": ["muhasebe"],
                    "tenant_id": "default", "roles": ["user"], "is_admin": False}
        return None

    monkeypatch.setattr(user_repo, "get_active_user_by_token_hash", fake_get)
    return calls


def test_cache_hit_skips_db(db_counter):
    r = DbUserResolver(_FakeDb(), cache=RedisSessionCache(FakeRedis()))
    ctx1 = r.resolve("GOODTOK")
    ctx2 = r.resolve("GOODTOK")                 # ikinci → cache'ten
    assert ctx1 == ctx2 and ctx1["allowed_doc_scopes"] == ["muhasebe"]
    assert db_counter["n"] == 1                 # DB'ye YALNIZ BİR kez gidildi


def test_invalid_token_is_not_cached(db_counter):
    r = DbUserResolver(_FakeDb(), cache=RedisSessionCache(FakeRedis()))
    for _ in range(3):
        with pytest.raises(Unauthorized):
            r.resolve("BADTOK")
    assert db_counter["n"] == 3                 # her seferinde DB'de reddedildi (cache YOK)


def test_invalidate_forces_db_reload(db_counter):
    cache = RedisSessionCache(FakeRedis())
    r = DbUserResolver(_FakeDb(), cache=cache)
    r.resolve("GOODTOK")                        # DB + cache
    assert db_counter["n"] == 1
    cache.invalidate_user("u1")                 # yetki değişti → cache temizle
    r.resolve("GOODTOK")                        # tekrar DB
    assert db_counter["n"] == 2


def test_resolver_failsafe_when_redis_down(db_counter):
    """Redis çökse bile auth çalışır (DB kaynak-otoriter). Cache miss + put yutulur."""
    r = DbUserResolver(_FakeDb(), cache=RedisSessionCache(BrokenRedis()))
    ctx = r.resolve("GOODTOK")
    assert ctx["user_id"] == "u1"
    assert db_counter["n"] == 1                 # cache patlasa da DB'den çözüldü


def test_null_cache_resolver_matches_legacy_db_path(db_counter):
    """Redis yokken (Null) her çözüm DB'ye gider — eski davranış birebir."""
    r = DbUserResolver(_FakeDb())               # cache verilmedi → Null
    r.resolve("GOODTOK"); r.resolve("GOODTOK")
    assert db_counter["n"] == 2


# --------------------------------------------------- /api/health redis sinyali
class _SahteRuntime:
    from ragintel.api.runtime import RagRuntime
    health = RagRuntime.health

    def __init__(self, cache):
        from types import SimpleNamespace
        self.cfg = SimpleNamespace(group=lambda n: SimpleNamespace(health_timeout=1.0))
        self.langfuse = SimpleNamespace(enabled=False)
        self.is_warm = True
        self.session_cache = cache

    def _check_db(self): return "ok"
    def _check_http(self, *a, **k): return "ok"


def test_health_reports_redis_ok_when_enabled():
    h = _SahteRuntime(RedisSessionCache(FakeRedis())).health()
    assert h["checks"]["redis"] == "ok"


def test_health_reports_redis_disabled_when_null():
    h = _SahteRuntime(NullSessionCache()).health()
    assert h["checks"]["redis"] == "disabled"


def test_redis_down_does_not_make_health_unhealthy():
    """OPSİYONEL bileşen: Redis 'down' olsa da (configured ama erişilemez) genel
    status 'unhealthy' OLMAZ — derive_health_status redis'i dikkate almaz."""
    h = _SahteRuntime(RedisSessionCache(BrokenRedis())).health()
    assert h["checks"]["redis"] == "down"
    assert h["status"] != "unhealthy"           # db/ollama ok → akış sürer


# --------------------------------------------------- compose kod-dışı sözleşmesi
def _compose_text():
    return (KOK / "docker-compose.h200.yml").read_text(encoding="utf-8")


def _compose_code_lines():
    """Yorum satırları HARİÇ gerçek YAML satırları (iddialar açıklamaya takılmasın)."""
    return [ln for ln in _compose_text().splitlines() if not ln.strip().startswith("#")]


def test_compose_has_redis_service_with_cache_params():
    t = _compose_text()
    assert "\n  redis:" in t and "image: redis:7" in t
    for token in ("network_mode: host", "--maxmemory 4gb", "--appendonly no",
                  "allkeys-lru", "requirepass"):
        assert token in t, f"compose redis servisinde eksik: {token!r}"


def test_compose_redis_password_is_not_hardcoded():
    """Parola compose'a LİTERAL yazılmaz — konteyner env'inden shell-expansion ile
    ($$RAGINTEL_REDIS_PASSWORD; compose $$ → literal $)."""
    code = "\n".join(_compose_code_lines())
    assert '--requirepass "$$RAGINTEL_REDIS_PASSWORD"' in code
    # requirepass'in KOD satırlarında ardından yalnız env-değişkeni gelsin (literal parola yok).
    import re
    for m in re.finditer(r"requirepass\s+(\S+)", code):
        assert "$$RAGINTEL_REDIS_PASSWORD" in m.group(1), \
            f"requirepass literal parola içeriyor olabilir: {m.group(1)!r}"


def test_compose_healthcheck_uses_authenticated_ping():
    code = "\n".join(_compose_code_lines())
    assert "redis-cli" in code and "ping" in code and "$$RAGINTEL_REDIS_PASSWORD" in code


def test_compose_redis_server_and_healthcheck_ports_agree():
    """Sunucu `--port N` ile healthcheck `-p N` AYNI olmalı; aksi halde healthcheck
    yanlış porta ping atıp 'yalan' söyler. (H200'de 6379 Langfuse'da → 6380 kullanılıyor.)"""
    import re
    code = "\n".join(_compose_code_lines())
    server_port = re.search(r"--port\s+(\d+)", code)
    hc_port = re.search(r"redis-cli\s+-p\s+(\d+)", code)
    assert server_port and hc_port, "compose'da redis --port veya healthcheck -p bulunamadı"
    assert server_port.group(1) == hc_port.group(1), (
        f"redis sunucu portu ({server_port.group(1)}) healthcheck portuyla "
        f"({hc_port.group(1)}) uyuşmuyor — healthcheck yanlış porta ping atar.")


def test_redis_url_is_NOT_in_deploy_zorunlu_sirlar():
    """Redis OPSİYONEL → ZORUNLU_SIRLAR'a girmez (yoksa dağıtımı boşuna bloklardı)."""
    import re
    m = re.search(r"ZORUNLU_SIRLAR=\(([^)]*)\)", (KOK / "deploy.sh").read_text(encoding="utf-8"))
    assert m and "RAGINTEL_REDIS" not in m.group(1)


def test_api_service_not_hostage_to_redis():
    """ragintel-api `depends_on: redis` İLE bağlanmaz — Redis çökse app yine kalkar
    (fail-safe felsefesi). depends_on hiç yoksa da bu sağlanır. (Yorumlar hariç.)"""
    assert not any("depends_on" in ln for ln in _compose_code_lines())

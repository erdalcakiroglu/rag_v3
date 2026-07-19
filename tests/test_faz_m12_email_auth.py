"""M-12 — Email+şifre kimlik: self-kayıt (admin-onaylı) + giriş.

İki katman:
  1) BİRİM: şifre hash'i (argon2id, düz metin YOK), politika, email/allowlist (fail-closed),
     enumeration-nötr yardımcılar, log scrub (şifre/token loglanmaz).
  2) ENTEGRASYON (fake-DB + TestClient): register→pending→admin approve→login→oturum token'ı
     mevcut resolver'la çözülür; ÇİFT-YÖNLÜ scope izolasyonu bu YENİ yolla da (onaylı-scope
     kullanıcı başka scope'u taşımaz). Canlı DB gerektirmez — user_repo dict-store ile taklit
     edilir; argon2/politika/uçlar/resolver GERÇEK çalışır.

DDL (docs/FAZ6_Sema_Ek1_EmailAuth.sql) Erdal tarafından uygulanır; bu testler onu beklemez.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from ragintel.api import passwords
from ragintel.api.auth import DbUserResolver, hash_token
from ragintel.config.settings import AuthConfig


# =============================================================================
# 1) BİRİM
# =============================================================================
def test_password_hash_is_argon2id_not_plaintext():
    h = passwords.hash_password("dogru-sifre-123")
    assert h.startswith("$argon2id$")               # ⚠ token'ın sha256'sı DEĞİL
    assert "dogru-sifre-123" not in h               # düz metin hash'te YOK
    assert h != "dogru-sifre-123"
    assert passwords.verify_password(h, "dogru-sifre-123") is True
    assert passwords.verify_password(h, "yanlis") is False


def test_hash_is_salted_two_hashes_differ():
    a = passwords.hash_password("aynısifre123")
    b = passwords.hash_password("aynısifre123")
    assert a != b                                   # rastgele salt → aynı şifre farklı hash
    assert passwords.verify_password(a, "aynısifre123")
    assert passwords.verify_password(b, "aynısifre123")


def test_verify_none_hash_is_false_and_does_not_raise():
    # pending/None hash → dummy-verify ile timing eşit + False (enumeration nötr).
    assert passwords.verify_password(None, "herhangi") is False
    passwords.dummy_verify("herhangi")              # atmamalı


def test_password_policy_min_length():
    assert passwords.password_policy_error("kısa", min_length=12) is not None
    assert "12" in passwords.password_policy_error("kısa", min_length=12)
    assert passwords.password_policy_error("yeterince-uzun-sifre", min_length=12) is None


def test_email_normalize_and_format():
    assert passwords.normalize_email("  Ali@Firma.COM ") == "ali@firma.com"
    assert passwords.valid_email_format("a@b.com")
    assert not passwords.valid_email_format("a@b")
    assert not passwords.valid_email_format("düz-metin")


def test_domain_allowlist_is_fail_closed():
    # Boş allowlist → HİÇBİR email kabul edilmez (fail-closed).
    assert passwords.domain_allowed("a@firma.com", []) is False
    # İçindeyse kabul; dışındaysa red (büyük/küçük harf duyarsız).
    assert passwords.domain_allowed("a@Firma.com", ["firma.com"]) is True
    assert passwords.domain_allowed("a@baska.com", ["firma.com"]) is False


def test_log_scrub_redacts_secret_keys_but_keeps_others():
    from ragintel.observability.logging import _pii_scrub_processor
    d = _pii_scrub_processor(None, None, {
        "password": "cok-gizli", "api_token": "abc.def", "authorization": "Bearer x",
        "event": "login_ok", "user_id": "ali@firma.com"})
    assert d["password"] == "***"                   # şifre log'a SIZMAZ
    assert d["api_token"] == "***" and d["authorization"] == "***"
    assert d["event"] == "login_ok"                 # sır olmayan alan korunur
    assert d["user_id"] == "ali@firma.com"


# =============================================================================
# 2) ENTEGRASYON — fake user_repo (dict-store) + gerçek uçlar/argon2/resolver
# =============================================================================
STORE: dict[str, dict] = {}


def _row(uid, **kw):
    base = {"user_id": uid, "email": None, "password_hash": None, "display_name": None,
            "status": "active", "active": True, "allowed_doc_scopes": [], "is_admin": False,
            "api_token_hash": None, "tenant_id": "default", "roles": ["user"]}
    base.update(kw)
    return base


@pytest.fixture
def store(monkeypatch):
    STORE.clear()
    # önceden onaylı bir admin (Bearer token'lı) — approve/reject uçlarını çağırır.
    STORE["admin"] = _row("admin", is_admin=True, api_token_hash=hash_token("ADMINTOK"))
    from ragintel.database import user_repo as ur

    def email_auth_ready(conn): return True
    def email_exists(conn, email_lower): return any(r["email"] == email_lower for r in STORE.values())

    def register_pending_user(conn, *, user_id, email_lower, password_hash, display_name):
        if user_id in STORE:
            raise RuntimeError("dup")
        STORE[user_id] = _row(user_id, email=email_lower, password_hash=password_hash,
                              display_name=display_name, status="pending", active=False,
                              allowed_doc_scopes=[])

    def get_auth_user_by_email(conn, email_lower):
        for r in STORE.values():
            if r["email"] == email_lower:
                return {"user_id": r["user_id"], "password_hash": r["password_hash"],
                        "status": r["status"], "active": r["active"],
                        "allowed_doc_scopes": list(r["allowed_doc_scopes"]), "is_admin": r["is_admin"]}
        return None

    def set_session_token(conn, user_id, token_hash):
        if user_id in STORE:
            STORE[user_id]["api_token_hash"] = token_hash
            return 1
        return 0

    def approve_user(conn, user_id, scopes):
        r = STORE.get(user_id)
        if r and r["status"] == "pending":
            r.update(status="active", active=True, allowed_doc_scopes=list(scopes))
            return 1
        return 0

    def reject_user(conn, user_id):
        r = STORE.get(user_id)
        if r:
            r.update(status="disabled", active=False)
            return 1
        return 0

    def get_active_user_by_token_hash(conn, token_hash):
        for r in STORE.values():
            if r["api_token_hash"] == token_hash and r["active"]:
                return {"user_id": r["user_id"], "display_name": r["display_name"],
                        "allowed_doc_scopes": list(r["allowed_doc_scopes"]),
                        "tenant_id": r["tenant_id"], "roles": r["roles"], "is_admin": r["is_admin"]}
        return None

    def list_users(conn):
        return [{"user_id": r["user_id"], "display_name": r["display_name"],
                 "allowed_doc_scopes": list(r["allowed_doc_scopes"]), "active": r["active"],
                 "is_admin": r["is_admin"], "created_at": "2026-01-01", "email": r["email"],
                 "status": r["status"]} for r in STORE.values()]

    for name, fn in dict(
        email_auth_ready=email_auth_ready, email_exists=email_exists,
        register_pending_user=register_pending_user, get_auth_user_by_email=get_auth_user_by_email,
        set_session_token=set_session_token, approve_user=approve_user, reject_user=reject_user,
        get_active_user_by_token_hash=get_active_user_by_token_hash, list_users=list_users,
    ).items():
        monkeypatch.setattr(ur, name, fn)
    return STORE


class _FakeDb:
    @contextmanager
    def connection(self):
        yield None


class _FakeCfg:
    def __init__(self, domains, minlen=12):
        self._auth = AuthConfig(allowed_email_domains=domains, password_min_length=minlen)

    def group(self, name):
        assert name == "auth"
        return self._auth


class _FakeRuntime:
    def __init__(self, domains, minlen=12):
        from ragintel.api.session_cache import NullSessionCache
        self.db = _FakeDb()
        self.cfg = _FakeCfg(domains, minlen)
        self.session_cache = NullSessionCache()   # gerçek RagRuntime hep sağlar (M-10/0)
        self.resolver = DbUserResolver(self.db)   # GERÇEK resolver → fake user_repo

    def warm_up_async(self):
        pass


def _client(domains=("firma.com",), minlen=12):
    from ragintel.api.app import create_app
    return TestClient(create_app(runtime=_FakeRuntime(list(domains), minlen)))


# --- kayıt: allowlist + fail-closed + pending + scope=[] ----------------------
def test_register_rejected_outside_allowlist(store):
    with _client(domains=("firma.com",)) as c:
        r = c.post("/api/register", json={"full_name": "Dış Kişi", "email": "x@baska.com", "password": "uzun-sifre-123"})
    assert r.status_code == 403
    assert not any(row["email"] == "x@baska.com" for row in store.values())   # DB'ye yazılmadı


def test_register_inside_allowlist_creates_pending_empty_scope(store):
    with _client(domains=("firma.com",)) as c:
        r = c.post("/api/register", json={"full_name": "Ali Veli", "email": "Ali@Firma.com", "password": "uzun-sifre-123"})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    row = store["ali@firma.com"]
    assert row["status"] == "pending" and row["active"] is False
    assert row["allowed_doc_scopes"] == []                 # BOŞ = fail-closed
    assert row["api_token_hash"] is None                   # token yok (login'e dek)
    assert row["password_hash"].startswith("$argon2id$")   # argon2, düz metin yok
    assert "uzun-sifre-123" not in row["password_hash"]


def test_register_short_password_rejected_by_policy(store):
    with _client(domains=("firma.com",), minlen=12) as c:
        r = c.post("/api/register", json={"full_name": "Ali", "email": "a@firma.com", "password": "kısa"})
    assert r.status_code == 400 and "12" in r.json()["detail"]


def test_register_empty_allowlist_is_fail_closed(store):
    with _client(domains=()) as c:      # allowlist boş → hiçbir kayıt
        r = c.post("/api/register", json={"full_name": "Ali", "email": "a@firma.com", "password": "uzun-sifre-123"})
    assert r.status_code == 403


def test_register_duplicate_email_is_enumeration_neutral(store):
    with _client(domains=("firma.com",)) as c:
        first = c.post("/api/register", json={"full_name": "Ali", "email": "a@firma.com", "password": "uzun-sifre-123"})
        second = c.post("/api/register", json={"full_name": "Başka", "email": "a@firma.com", "password": "uzun-sifre-999"})
    # İkisi de aynı jenerik başarı — varlık sızmaz; ikinci kayıt oluşturulmaz.
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert sum(1 for r in store.values() if r["email"] == "a@firma.com") == 1


# --- giriş: pending 403, yanlış/bilinmeyen nötr 401 --------------------------
def test_login_pending_is_forbidden_no_session(store):
    with _client() as c:
        c.post("/api/register", json={"full_name": "Ali", "email": "a@firma.com", "password": "uzun-sifre-123"})
        r = c.post("/api/login", json={"email": "a@firma.com", "password": "uzun-sifre-123"})
    assert r.status_code == 403 and "onay bekliyor" in r.json()["detail"]
    assert store["a@firma.com"]["api_token_hash"] is None      # oturum açılmadı


def test_login_wrong_password_and_unknown_email_are_identical_neutral(store):
    with _client() as c:
        c.post("/api/register", json={"full_name": "Ali", "email": "a@firma.com", "password": "uzun-sifre-123"})
        wrong = c.post("/api/login", json={"email": "a@firma.com", "password": "YANLIS-sifre-000"})
        unknown = c.post("/api/login", json={"email": "yok@firma.com", "password": "uzun-sifre-123"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]   # enumeration nötr (aynı mesaj)


# --- onay → giriş → oturum token'ı resolver'la çözülür -----------------------
def _approve(c, user_id, scopes):
    return c.post(f"/api/admin/users/{user_id}/approve",
                  json={"scopes": scopes}, headers={"Authorization": "Bearer ADMINTOK"})


def test_approve_then_login_issues_working_session_token(store):
    with _client() as c:
        c.post("/api/register", json={"full_name": "Ali", "email": "a@firma.com", "password": "uzun-sifre-123"})
        assert _approve(c, "a@firma.com", ["muhasebe"]).status_code == 200
        r = c.post("/api/login", json={"email": "a@firma.com", "password": "uzun-sifre-123"})
    assert r.status_code == 200
    tok = r.json()["token"]
    assert store["a@firma.com"]["status"] == "active"
    assert store["a@firma.com"]["api_token_hash"] == hash_token(tok)   # sha256 saklandı (düz değil)


def test_bidirectional_scope_isolation_via_login_path(store):
    """Kabul: onaylı-scope kullanıcı SADECE kendi scope'unu taşır; başkasınınkini GÖRMEZ.
    Oturum token'ı mevcut resolver'la çözülür → user_ctx.allowed_doc_scopes doğru olmalı."""
    resolver = DbUserResolver(_FakeDb())
    with _client() as c:
        # iki kullanıcı, iki farklı scope
        c.post("/api/register", json={"full_name": "A", "email": "a@firma.com", "password": "uzun-sifre-aaa"})
        c.post("/api/register", json={"full_name": "B", "email": "b@firma.com", "password": "uzun-sifre-bbb"})
        _approve(c, "a@firma.com", ["muhasebe"])
        _approve(c, "b@firma.com", ["insan-kaynaklari"])
        tok_a = c.post("/api/login", json={"email": "a@firma.com", "password": "uzun-sifre-aaa"}).json()["token"]
        tok_b = c.post("/api/login", json={"email": "b@firma.com", "password": "uzun-sifre-bbb"}).json()["token"]

    ctx_a = resolver.resolve(tok_a)
    ctx_b = resolver.resolve(tok_b)
    assert ctx_a["allowed_doc_scopes"] == ["muhasebe"]
    assert ctx_b["allowed_doc_scopes"] == ["insan-kaynaklari"]
    # ÇİFT-YÖNLÜ: her kullanıcı ötekinin scope'unu taşımaz (fail-closed izolasyon).
    assert "insan-kaynaklari" not in ctx_a["allowed_doc_scopes"]
    assert "muhasebe" not in ctx_b["allowed_doc_scopes"]


def test_rejected_pending_cannot_login(store):
    with _client() as c:
        c.post("/api/register", json={"full_name": "Ali", "email": "a@firma.com", "password": "uzun-sifre-123"})
        r = c.post("/api/admin/users/a@firma.com/reject", headers={"Authorization": "Bearer ADMINTOK"})
        assert r.status_code == 200
        login = c.post("/api/login", json={"email": "a@firma.com", "password": "uzun-sifre-123"})
    assert login.status_code == 403 and "devre dışı" in login.json()["detail"]


def test_admin_endpoints_require_admin(store):
    with _client() as c:
        # token yok → 401
        assert c.post("/api/admin/users/x/approve", json={"scopes": []}).status_code == 401

"""FAZ 6 P1 — AuthN testleri: hash/bearer/resolver + fail-closed 401 + scope sızıntısı."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from ragintel.api.app import create_app
from ragintel.api.auth import DbUserResolver, Unauthorized, bearer_token, hash_token
from ragintel.api.runtime import RagRuntime
from ragintel.config.loader import load_config


# --- birim: hash + bearer -----------------------------------------------------
def test_hash_token_deterministic_hex():
    h = hash_token("secret-abc")
    assert h == hash_token("secret-abc") and len(h) == 64 and h != "secret-abc"


def test_bearer_token_parsing():
    assert bearer_token("Bearer tok123") == "tok123"
    assert bearer_token("bearer tok123") == "tok123"   # case-insensitive scheme
    assert bearer_token("Bearer   spaced ") == "spaced"
    assert bearer_token(None) is None
    assert bearer_token("Basic xyz") is None
    assert bearer_token("Bearer ") is None
    assert bearer_token("tok-without-scheme") is None


# --- birim: DbUserResolver (sahte db, ağ yok) --------------------------------
class _FakeCursorResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row):
        self._row = row

    def execute(self, sql, params=None):
        # is_admin sütun-kontrolü (FAZ7) → yok say (has_admin=False); diğer SELECT → user satırı.
        if "information_schema" in sql.lower():
            return _FakeCursorResult(None)
        return _FakeCursorResult(self._row)


class _FakeDb:
    def __init__(self, row):
        self._row = row

    @contextmanager
    def connection(self):
        yield _FakeConn(self._row)


def test_resolver_valid_token_builds_ctx():
    row = ("erdal", "Erdal", ["inventory"], "t1", ["admin"])
    ctx = DbUserResolver(_FakeDb(row)).resolve("any-token")
    assert ctx["user_id"] == "erdal" and ctx["allowed_doc_scopes"] == ["inventory"]
    assert ctx["tenant_id"] == "t1" and ctx["roles"] == ["admin"]


def test_resolver_unknown_token_unauthorized():
    with pytest.raises(Unauthorized):
        DbUserResolver(_FakeDb(None)).resolve("bad-token")


def test_resolver_missing_token_unauthorized():
    with pytest.raises(Unauthorized):
        DbUserResolver(_FakeDb(("x", "X", ["default"], "t", ["user"]))).resolve(None)
    with pytest.raises(Unauthorized):
        DbUserResolver(_FakeDb(None)).resolve("   ")


# --- API fail-closed 401 (mock runtime, DB yok) ------------------------------
class _MockGateway:
    def complete(self, **kw):  # 401 yolunda çağrılmaz
        raise AssertionError("auth başarısızken gateway çağrılmamalı")


class _MockService:
    def search_hybrid(self, *a, **k):
        return []


class _MockCB:
    def build(self, retrieved):
        return {"blocks": [], "citations": [], "dropped_chunk_ids": []}


def _mock_runtime(resolver):
    from langgraph.checkpoint.memory import InMemorySaver
    from ragintel.config.settings import LangfuseSettings
    return RagRuntime(
        db=object(), config=load_config(), gateway=_MockGateway(), checkpointer=InMemorySaver(),
        service=_MockService(), context_builder=_MockCB(),
        langfuse=LangfuseSettings(host="", public_key="", secret_key=""), resolver=resolver)


def _reject_resolver():
    return DbUserResolver(_FakeDb(None))  # her token reddedilir


def test_api_no_token_401():
    client = TestClient(create_app(_mock_runtime(_reject_resolver())))
    r = client.post("/api/ask", json={"question": "Karbon vergisi nedir?"})
    assert r.status_code == 401 and r.headers.get("WWW-Authenticate") == "Bearer"


def test_api_bad_token_401():
    client = TestClient(create_app(_mock_runtime(_reject_resolver())))
    r = client.post("/api/ask", headers={"Authorization": "Bearer wrong"},
                    json={"question": "Karbon vergisi nedir?"})
    assert r.status_code == 401


def test_api_old_x_user_id_ignored_401():
    # Eski X-User-Id yolu KALDIRILDI → yalnızca X-User-Id ile 401.
    client = TestClient(create_app(_mock_runtime(_reject_resolver())))
    r = client.post("/api/ask", headers={"X-User-Id": "erdal"},
                    json={"question": "Karbon vergisi nedir?"})
    assert r.status_code == 401


# --- @db: çift-yönlü scope sızıntısı (tablo yoksa skip) ----------------------
@pytest.mark.db
def test_scope_isolation_bidirectional(live_db):
    from ragintel.database import user_repo
    from ragintel.retrieval import RetrievalService
    with live_db.connection() as conn:
        if not user_repo.users_table_ready(conn):
            pytest.skip("ragintel.users henüz uygulanmadı (FAZ6_Sema.sql)")

    cfg = load_config(db_reader=None)
    svc = RetrievalService(db=live_db, config=cfg)
    q = "Karbon vergisi nedir?"
    default_ctx = {"user_id": "u_def", "tenant_id": "t", "roles": ["user"], "allowed_doc_scopes": ["default"]}
    inv_ctx = {"user_id": "u_inv", "tenant_id": "t", "roles": ["user"], "allowed_doc_scopes": ["inventory"]}

    default_hits = svc.search_hybrid(q, top_k=5, user_ctx=default_ctx)
    inv_hits = svc.search_hybrid(q, top_k=5, user_ctx=inv_ctx)
    # default-scope kullanıcı karbon içeriğini görür; inventory-scope kullanıcı GÖRMEZ (sızıntı yok).
    assert len(default_hits) > 0
    assert all(h["source"].get("file_name") not in {s["source"]["file_name"] for s in default_hits}
               for h in inv_hits) or len(inv_hits) == 0

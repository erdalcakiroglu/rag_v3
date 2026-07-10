"""FAZ 7 — Admin panel testleri: 403/401 fail-closed, bozuk config red (DB bozulmaz),
token tek-gösterim, self-deaktive koruması, config valid save."""

from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient

from ragintel.api.app import create_app
from ragintel.api.runtime import RagRuntime
from ragintel.config.loader import load_config
from ragintel.config.settings import LangfuseSettings


# --- sahte altyapı (DB/ağ yok) -----------------------------------------------
class _Cur:
    def __init__(self, rows=None, rowcount=1):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


def _handler(sql, params=None):
    s = sql.lower()
    if "information_schema.columns" in s:
        return _Cur([(1,)])          # is_admin kolonu var
    if "select 1 from ragintel.users" in s:
        return _Cur([])              # user_exists → yok
    if "insert into" in s or "update" in s:
        return _Cur(rowcount=1)
    return _Cur([])                  # list_* → boş


class _Conn:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return _handler(sql, params)


class _FakeDb:
    def __init__(self):
        self.conn = _Conn()

    @contextmanager
    def connection(self):
        yield self.conn


class _Resolver:
    def __init__(self, is_admin, user_id="u"):
        self._admin = is_admin
        self._uid = user_id

    def resolve(self, token):
        from ragintel.api.auth import Unauthorized
        if token != "tok":
            raise Unauthorized("geçersiz")
        return {"user_id": self._uid, "tenant_id": "t", "roles": ["user"],
                "allowed_doc_scopes": ["default"], "is_admin": self._admin}


class _MockGW:
    def complete(self, **k):
        raise AssertionError("çağrılmamalı")


class _MockSvc:
    def search_hybrid(self, *a, **k):
        return []


class _MockCB:
    def build(self, r):
        return {"blocks": [], "citations": [], "dropped_chunk_ids": []}


def _client(is_admin, user_id="u", db=None):
    from langgraph.checkpoint.memory import InMemorySaver
    rt = RagRuntime(db=db or _FakeDb(), config=load_config(), gateway=_MockGW(),
                    checkpointer=InMemorySaver(), service=_MockSvc(), context_builder=_MockCB(),
                    langfuse=LangfuseSettings(host="", public_key="", secret_key=""),
                    resolver=_Resolver(is_admin, user_id))
    return TestClient(create_app(rt)), rt


_A = {"Authorization": "Bearer tok"}


# --- fail-closed 401/403 -----------------------------------------------------
def test_admin_no_token_401():
    client, _ = _client(is_admin=True)
    assert client.get("/api/admin/config").status_code == 401


def test_admin_non_admin_403():
    client, _ = _client(is_admin=False)
    for path in ["/api/admin/config", "/api/admin/users", "/api/admin/files", "/api/admin/qc"]:
        assert client.get(path, headers=_A).status_code == 403


def test_admin_page_served():
    client, _ = _client(is_admin=True)
    r = client.get("/admin")
    assert r.status_code == 200 and "Admin" in r.text


# --- config: bozuk reddedilir (DB'ye YAZILMAZ) -------------------------------
def test_config_invalid_rejected_db_untouched():
    db = _FakeDb()
    client, _ = _client(is_admin=True, db=db)
    # max_iterations negatif → AgentConfig doğrulaması patlar (ge=... yok ama int/tip);
    # kesin bozuk: model tipine uymayan alan.
    r = client.post("/api/admin/config/agent", headers=_A, json={"max_iterations": "çok"})
    assert r.status_code == 400
    # DB'ye HİÇBİR yazım yapılmadı (doğrulama önce).
    assert all("insert into app_config" not in s.lower() for s, _ in db.conn.executed)

    # POZİTİF KONTROL: yukarıdaki iddia ancak _FakeDb GERÇEKTEN insert kaydediyorsa
    # kanıt değeri taşır. Aynı fake'e geçerli bir yazım gönderilince insert görünmeli;
    # görünmezse "yazım yapılmadı" iddiası kör kayıt yüzünden vacuously geçiyordu.
    ok = client.post("/api/admin/config/agent", headers=_A, json={"max_iterations": 3})
    assert ok.status_code == 200
    assert any("insert into app_config" in s.lower() for s, _ in db.conn.executed), \
        "fake DB insert kaydetmiyor → 'DB'ye yazılmadı' iddiası kanıtlanamaz"


def test_config_unknown_group_400():
    client, _ = _client(is_admin=True)
    assert client.post("/api/admin/config/bilinmeyen", headers=_A, json={}).status_code == 400


def test_config_valid_saved():
    db = _FakeDb()
    client, _ = _client(is_admin=True, user_id="admin1", db=db)
    r = client.post("/api/admin/config/agent", headers=_A, json={"max_iterations": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["updated_by"] == "admin1" and body["value"]["max_iterations"] == 3
    assert any("insert into app_config" in s.lower() for s, _ in db.conn.executed)


# --- users: token tek-gösterim + self-deaktive koruması ----------------------
def test_create_user_returns_token_once():
    client, _ = _client(is_admin=True)
    r = client.post("/api/admin/users", headers=_A,
                    json={"user_id": "yeni", "allowed_doc_scopes": ["default"]})
    assert r.status_code == 200
    body = r.json()
    assert body["token"] and len(body["token"]) > 20 and "yalnızca" in body["note"].lower()


def test_cannot_deactivate_self():
    client, _ = _client(is_admin=True, user_id="admin1")
    r = client.post("/api/admin/users/admin1/active", headers=_A, json={"active": False})
    assert r.status_code == 400 and "kendi" in r.json()["detail"].lower()

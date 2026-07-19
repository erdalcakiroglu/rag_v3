"""M-13 — konuşma geçmişi ("sohbetlerim").

GERÇEK conversation_repo + gerçek uçlar, in-memory FakeConn'a (conversations +
conversation_messages modeli) karşı koşulur. Kanıtlar:
  - SAHİPLİK izolasyonu (vacuous-guard: iki kullanıcı, her biri kendini görür, çapraz 0/404).
  - record_turn: yeni sohbet (title=ilk soru, scope SNAPSHOT, user_id); owned append (seq artar);
    BAŞKA kullanıcının sohbetine yazım YOK (fail-closed).
  - soft-delete: liste'den düşer, mesaj/veri DURUR, sonra 404.
  - Geçmiş Postgres'te → Redis'ten BAĞIMSIZ (session_store olmadan da erişilir).
  - scope snapshot SALT BİLGİ (panel okuma scope'a değil sahipliğe bakar).
"""

from __future__ import annotations

import itertools
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from ragintel.database import conversation_repo as cr


# --------------------------------------------------------------- FakeConn
class _Res:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []; self.rowcount = rowcount
    def fetchone(self): return self._rows[0] if self._rows else None
    def fetchall(self): return self._rows


class FakeConn:
    """conversations + conversation_messages'in in-memory modeli; repo'nun tam SQL'lerini
    ayırt edici alt-dizeyle eşler. (Şema H200'de ELLE uygulanır; bu test onu beklemez.)"""
    def __init__(self, ready=True):
        self.convs = {}   # cid -> row
        self.msgs = []    # msg rows
        self.ready = ready
        self._clk = itertools.count(1)

    def execute(self, sql, params=None):
        s = " ".join(sql.split()); p = params or ()
        if "pg_tables" in s:
            return _Res([(2 if self.ready else 0,)])
        if s.startswith("SELECT user_id FROM ragintel.conversations WHERE conversation_id"):
            r = self.convs.get(p[0]); return _Res([(r["user_id"],)] if r else [])
        if s.startswith("INSERT INTO ragintel.conversations"):
            cid, uid, title, scopes = p; t = next(self._clk)
            self.convs[cid] = {"user_id": uid, "title": title, "scopes": list(scopes),
                               "created_at": t, "last_at": t, "deleted_at": None}
            return _Res(rowcount=1)
        if s.startswith("UPDATE ragintel.conversations SET last_at"):
            self.convs[p[0]]["last_at"] = next(self._clk); return _Res(rowcount=1)
        if "COALESCE(MAX(seq)" in s:
            cid = p[0]; mx = max([m["seq"] for m in self.msgs if m["cid"] == cid] or [0]); return _Res([(mx,)])
        if s.startswith("INSERT INTO ragintel.conversation_messages"):
            c1, s1, q, t1, c2, s2, a, t2 = p
            self.msgs.append({"cid": c1, "seq": s1, "role": "user", "content": q, "trace_id": t1, "created_at": next(self._clk)})
            self.msgs.append({"cid": c2, "seq": s2, "role": "assistant", "content": a, "trace_id": t2, "created_at": next(self._clk)})
            return _Res(rowcount=2)
        if s.startswith("SELECT conversation_id, title, allowed_doc_scopes"):
            uid = p[0]
            rows = [(c, r["title"], r["scopes"], r["created_at"], r["last_at"])
                    for c, r in self.convs.items() if r["user_id"] == uid and r["deleted_at"] is None]
            rows.sort(key=lambda x: x[4], reverse=True); return _Res(rows)
        if s.startswith("SELECT 1 FROM ragintel.conversations WHERE conversation_id"):
            cid, uid = p; r = self.convs.get(cid)
            return _Res([(1,)] if r and r["user_id"] == uid and r["deleted_at"] is None else [])
        if s.startswith("SELECT seq, role, content"):
            cid = p[0]
            rows = sorted((m["seq"], m["role"], m["content"], m["trace_id"], m["created_at"])
                          for m in self.msgs if m["cid"] == cid)
            return _Res(list(rows))
        if s.startswith("UPDATE ragintel.conversations SET deleted_at"):
            cid, uid = p; r = self.convs.get(cid)
            if r and r["user_id"] == uid and r["deleted_at"] is None:
                r["deleted_at"] = next(self._clk); return _Res(rowcount=1)
            return _Res(rowcount=0)
        raise AssertionError("beklenmeyen SQL: " + s[:90])


def _rec(conn, cid, uid, q, a, scopes=("default",)):
    return cr.record_turn(conn, conversation_id=cid, user_id=uid, question=q, answer=a,
                          scopes=list(scopes), trace_id="tr-x")


# =============================================================================
# 1) GERÇEK repo — FakeConn'a karşı
# =============================================================================
def test_record_turn_new_creates_conversation_with_title_scope_owner():
    c = FakeConn()
    assert _rec(c, "sess-1", "ali", "Karbon vergisi nedir ve nasıl uygulanır detaylı anlat lütfen", "cevap", ["muhasebe"]) is True
    row = c.convs["sess-1"]
    assert row["user_id"] == "ali"
    assert row["title"].startswith("Karbon vergisi") and len(row["title"]) <= 51    # ~50 + …
    assert row["scopes"] == ["muhasebe"]                                            # scope SNAPSHOT
    msgs = [m for m in c.msgs if m["cid"] == "sess-1"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"].startswith("Karbon") and msgs[1]["content"] == "cevap"


def test_record_turn_owned_append_increments_seq():
    c = FakeConn()
    _rec(c, "s", "ali", "soru1", "cevap1")
    _rec(c, "s", "ali", "soru2", "cevap2")
    seqs = sorted(m["seq"] for m in c.msgs if m["cid"] == "s")
    assert seqs == [1, 2, 3, 4]                                                     # iki tur, sıralı


def test_record_turn_other_owner_is_failclosed_no_write():
    c = FakeConn()
    _rec(c, "s", "ali", "soru", "cevap")               # sess 'ali'ye ait
    before = len(c.msgs)
    assert _rec(c, "s", "veli", "gizli soru", "cevap") is False   # veli ali'nin sohbetine yazamaz
    assert len(c.msgs) == before                       # HİÇBİR mesaj yazılmadı (çapraz-yazım yok)
    assert c.convs["s"]["user_id"] == "ali"            # sahip değişmedi


def test_list_only_own_sorted_and_soft_delete():
    c = FakeConn()
    _rec(c, "a1", "ali", "s", "c"); _rec(c, "a2", "ali", "s", "c"); _rec(c, "v1", "veli", "s", "c")
    ali = cr.list_conversations(c, "ali")
    assert {x["conversation_id"] for x in ali} == {"a1", "a2"}     # veli'ninki YOK
    assert ali[0]["conversation_id"] == "a2"                        # en son üstte (a2 sonra yazıldı)
    # soft-delete a1 → liste'den düşer, mesaj DURUR
    assert cr.soft_delete(c, "a1", "ali") == 1
    assert {x["conversation_id"] for x in cr.list_conversations(c, "ali")} == {"a2"}
    assert any(m["cid"] == "a1" for m in c.msgs)                    # veri/audit durdu (silinmedi)
    # başkasının silinmez
    assert cr.soft_delete(c, "v1", "ali") == 0


def test_get_messages_ownership_failclosed():
    c = FakeConn()
    _rec(c, "a1", "ali", "soru", "cevap")
    assert cr.get_conversation_messages(c, "a1", "ali") is not None        # sahibi görür
    assert cr.get_conversation_messages(c, "a1", "veli") is None           # başkası → None (uç 404)
    assert cr.get_conversation_messages(c, "yok", "ali") is None           # olmayan → None
    cr.soft_delete(c, "a1", "ali")
    assert cr.get_conversation_messages(c, "a1", "ali") is None            # silinmiş → None


# =============================================================================
# 2) UÇLAR — TestClient (gerçek repo + FakeConn), sahiplik izolasyonu
# =============================================================================
class _FakeDb:
    def __init__(self, conn): self._c = conn
    @contextmanager
    def connection(self): yield self._c


class _Resolver:
    """token → ctx (Redis'e/DB'ye gitmez → geçmiş yolu Redis'ten BAĞIMSIZ kanıtı)."""
    _M = {"TOKA": "ali", "TOKB": "veli"}
    def resolve(self, token):
        from ragintel.api.auth import Unauthorized
        uid = self._M.get(token)
        if not uid:
            raise Unauthorized("geçersiz")
        return {"user_id": uid, "tenant_id": "default", "roles": ["user"],
                "allowed_doc_scopes": ["default"], "is_admin": False}


class _RT:
    def __init__(self, conn):
        self.db = _FakeDb(conn); self.resolver = _Resolver()
    def warm_up_async(self): pass


def _client(conn):
    from ragintel.api.app import create_app
    return TestClient(create_app(runtime=_RT(conn)))


@pytest.fixture
def seeded():
    c = FakeConn()
    _rec(c, "a1", "ali", "Ali sorusu bir", "cevap"); _rec(c, "a2", "ali", "Ali sorusu iki", "cevap")
    _rec(c, "v1", "veli", "Veli sorusu", "cevap")
    return c


def test_endpoint_list_is_own_only_bidirectional(seeded):
    with _client(seeded) as cl:
        a = cl.get("/api/conversations", headers={"Authorization": "Bearer TOKA"}).json()["conversations"]
        v = cl.get("/api/conversations", headers={"Authorization": "Bearer TOKB"}).json()["conversations"]
    a_ids = {x["conversation_id"] for x in a}; v_ids = {x["conversation_id"] for x in v}
    assert a_ids == {"a1", "a2"} and v_ids == {"v1"}          # her biri KENDİNİ görür (vacuous değil)
    assert a_ids.isdisjoint(v_ids)                            # ÇAPRAZ 0


def test_endpoint_get_ownership_404(seeded):
    with _client(seeded) as cl:
        own = cl.get("/api/conversations/a1", headers={"Authorization": "Bearer TOKA"})
        cross = cl.get("/api/conversations/a1", headers={"Authorization": "Bearer TOKB"})  # veli, ali'nin
        missing = cl.get("/api/conversations/yok", headers={"Authorization": "Bearer TOKA"})
    assert own.status_code == 200 and len(own.json()["messages"]) == 2
    assert cross.status_code == 404 and missing.status_code == 404   # sahiplik: varlık sızmaz


def test_endpoint_delete_ownership(seeded):
    with _client(seeded) as cl:
        H = {"Authorization": "Bearer TOKA"}
        assert cl.delete("/api/conversations/v1", headers=H).status_code == 404   # başkasınınki
        assert cl.delete("/api/conversations/a1", headers=H).status_code == 200
        left = {x["conversation_id"] for x in cl.get("/api/conversations", headers=H).json()["conversations"]}
    assert left == {"a2"}                                     # silinen liste'de yok


def test_endpoint_requires_auth(seeded):
    with _client(seeded) as cl:
        assert cl.get("/api/conversations").status_code == 401


def test_history_accessible_without_redis(seeded):
    """Geçmiş Postgres'te — resolver Redis'e gitmiyor; liste/transcript Redis olmadan çalışır."""
    with _client(seeded) as cl:
        r = cl.get("/api/conversations/a1", headers={"Authorization": "Bearer TOKA"})
    assert r.status_code == 200 and [m["role"] for m in r.json()["messages"]] == ["user", "assistant"]


def test_ready_guard_empty_when_schema_missing():
    c = FakeConn(ready=False)
    with _client(c) as cl:
        lst = cl.get("/api/conversations", headers={"Authorization": "Bearer TOKA"})
        got = cl.get("/api/conversations/x", headers={"Authorization": "Bearer TOKA"})
    assert lst.status_code == 200 and lst.json()["conversations"] == []   # şema yok → boş (bozulmaz)
    assert got.status_code == 404


# =============================================================================
# 3) ask → geçmiş kaydı wiring'i (runtime._record_history)
# =============================================================================
def test_runtime_record_history_extracts_answer_and_scope_snapshot():
    from types import SimpleNamespace
    from ragintel.api.runtime import RagRuntime
    c = FakeConn()
    fake = SimpleNamespace(db=_FakeDb(c), log=SimpleNamespace(warning=lambda *a, **k: None))
    RagRuntime._record_history(fake, "sess-9",
                               {"user_id": "ali", "allowed_doc_scopes": ["muhasebe"]},
                               "Soru metni nedir", {"answer": "Cevap metni"}, "tr-1")
    assert c.convs["sess-9"]["user_id"] == "ali"
    assert c.convs["sess-9"]["scopes"] == ["muhasebe"]        # scope SNAPSHOT ctx'ten
    msgs = [m for m in c.msgs if m["cid"] == "sess-9"]
    assert msgs[0]["content"] == "Soru metni nedir" and msgs[1]["content"] == "Cevap metni"


def test_runtime_record_history_is_best_effort_when_not_ready():
    """Şema yoksa geçmiş yazımı sessizce atlanır — ask'ı ÇÖKERTMEZ."""
    from types import SimpleNamespace
    from ragintel.api.runtime import RagRuntime
    c = FakeConn(ready=False)
    fake = SimpleNamespace(db=_FakeDb(c), log=SimpleNamespace(warning=lambda *a, **k: None))
    RagRuntime._record_history(fake, "s", {"user_id": "ali", "allowed_doc_scopes": []},
                               "q", {"answer": "a"}, "t")      # atmamalı
    assert "s" not in c.convs

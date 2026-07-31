"""M-14 — agent-pull çok-tur hafıza (memory_search) sözleşmesi.

Tasarım (kullanıcı onaylı): SADECE wiring, son 3 tur. Mekanizma memory_search
tool'u (agent-pull) — geçmiş, tail'deki tool-result'tan gelir; prepare'in her tur
`messages=None` reset'ini VE kol-2 prefix (KV-cache) disiplinini BOZMAZ (head donuk
kalır). Bu dosya kilitlediği kontrat:

  - memory_search bu oturumun ÖNCEKİ turlarının Q/A'sını reader üzerinden döndürür.
  - conversation_id (=session_id) + user_id RUNTIME enjekte; LLM ARGÜMANINDA YOK
    (şema yalnız `query` içerir — model oturum kimliği/kullanıcı üretemez/atlatamaz).
  - memory_reader yok / conversation_id yok / user_id yok → boş (fail-safe; stateless
    graph-flow testleriyle ve [[test_multi_turn_same_session_starts_clean_scratchpad]] ile uyum).
  - SAHİPLİK fail-closed reader'da: get_conversation_messages None/boş → boş memory.
  - reader son 3 turu (=6 mesaj) döndürür ve yalnız {role,content}'e sanitize eder.
  - tools_node state['session_id']'i execute'a conversation_id olarak thread'ler.
"""
from __future__ import annotations

from ragintel.agents.tools import (
    MEMORY_SEARCH_SCHEMA,
    ToolRegistry,
    _MEMORY_RECALL_TURNS,
)
from ragintel.agents.nodes.tools_node import tools_node


def _reader_stub(calls):
    def reader(conversation_id, user_id, limit_turns):
        calls.append((conversation_id, user_id, limit_turns))
        return [
            {"role": "user", "content": "önceki soru"},
            {"role": "assistant", "content": "önceki cevap"},
        ]
    return reader


# -- Registry sözleşmesi -------------------------------------------------------
def test_memory_search_reader_uzerinden_onceki_QA_doner():
    calls = []
    reg = ToolRegistry(service=None, memory_reader=_reader_stub(calls))
    out = reg.execute(
        "memory_search", {"query": "onu tekrar açıkla"},
        user_ctx={"user_id": "u1"}, conversation_id="sess-1",
    )
    assert out == {"memory": [
        {"role": "user", "content": "önceki soru"},
        {"role": "assistant", "content": "önceki cevap"},
    ]}
    # conversation_id + user_id RUNTIME enjekte edildi; recall penceresi sabit (son 3 tur).
    assert calls == [("sess-1", "u1", _MEMORY_RECALL_TURNS)]
    assert _MEMORY_RECALL_TURNS == 3


def test_memory_search_reader_YOKSA_bos():
    # Graph/flow testleri: memory_reader enjekte edilmez → boş (DB'ye gitmez).
    reg = ToolRegistry(service=None)
    out = reg.execute("memory_search", {}, user_ctx={"user_id": "u1"}, conversation_id="sess-1")
    assert out == {"memory": []}


def test_memory_search_conversation_id_YOKSA_bos():
    calls = []
    reg = ToolRegistry(service=None, memory_reader=_reader_stub(calls))
    out = reg.execute("memory_search", {}, user_ctx={"user_id": "u1"}, conversation_id=None)
    assert out == {"memory": []}
    assert calls == []  # oturum kimliği yoksa reader hiç çağrılmaz


def test_memory_search_user_id_YOKSA_bos():
    calls = []
    reg = ToolRegistry(service=None, memory_reader=_reader_stub(calls))
    out = reg.execute("memory_search", {}, user_ctx={}, conversation_id="sess-1")
    assert out == {"memory": []}
    assert calls == []  # user_id yoksa DB'ye gidilmez (fail-safe)


def test_memory_search_sahiplik_fail_closed_bos_liste_bos_doner():
    # get_conversation_messages sahip değil/silinmiş → None → reader [] → memory boş.
    reg = ToolRegistry(service=None, memory_reader=lambda c, u, n: [])
    out = reg.execute("memory_search", {}, user_ctx={"user_id": "u1"}, conversation_id="sess-1")
    assert out == {"memory": []}


def test_sema_yalniz_query_icerir_user_ctx_ve_id_YOK():
    props = MEMORY_SEARCH_SCHEMA["function"]["parameters"]["properties"]
    assert set(props) == {"query"}  # user_ctx / user_id / conversation_id LLM'e AÇILMAZ


# -- tools_node thread'lemesi ---------------------------------------------------
def test_tools_node_session_id_i_conversation_id_olarak_gecirir():
    captured = {}

    class _Reg:
        def execute(self, name, arguments, *, user_ctx, conversation_id=None):
            captured.update(name=name, conversation_id=conversation_id, user_ctx=user_ctx)
            return {"memory": []}

    state = {
        "pending_tool_calls": [{"name": "memory_search", "id": "c1", "arguments": {"query": "x"}}],
        "user_ctx": {"user_id": "u1"},
        "session_id": "sess-42",
        "retrieved": [],
    }
    out = tools_node(state, registry=_Reg())
    assert captured["conversation_id"] == "sess-42"   # session_id → conversation_id thread'lendi
    assert captured["name"] == "memory_search"
    tool_msg = out["messages"][0]
    assert tool_msg["role"] == "tool" and tool_msg["name"] == "memory_search"
    # memory çıktısı chunk değil → makbuza indirgenmeden LLM'e olduğu gibi geçer.
    assert '"memory"' in tool_msg["content"]
    assert out["retrieved"] == []  # memory retrieved'e (context'e) karışmaz


# -- Runtime reader: son-N slice + sanitize + fail-closed ----------------------
class _FakeConnCtx:
    def __enter__(self):
        return "CONN"

    def __exit__(self, *a):
        return False


class _FakeDb:
    def connection(self):
        return _FakeConnCtx()


class _NoopLog:
    def warning(self, *a, **k):
        pass


def _bind_reader():
    """_read_conversation_memory'yi ağır bootstrap olmadan sahte `self` üzerinde çağır."""
    from ragintel.api.runtime import RagRuntime
    import types
    return RagRuntime._read_conversation_memory, types.SimpleNamespace(db=_FakeDb(), log=_NoopLog())


def test_reader_son_3_turu_dondurur_ve_sanitize_eder(monkeypatch):
    from ragintel.database import conversation_repo
    # 4 tur = 8 mesaj (seq 1..8); son 3 tur = son 6 mesaj beklenir.
    full = [
        {"seq": i, "role": ("user" if i % 2 else "assistant"),
         "content": f"m{i}", "trace_id": "tr", "created_at": "t"}
        for i in range(1, 9)
    ]
    monkeypatch.setattr(conversation_repo, "get_conversation_messages",
                        lambda conn, cid, uid: full)
    fn, fake_self = _bind_reader()
    out = fn(fake_self, "sess-1", "u1", 3)
    assert [m["content"] for m in out] == ["m3", "m4", "m5", "m6", "m7", "m8"]  # son 6 (=3 tur)
    assert all(set(m) == {"role", "content"} for m in out)  # trace_id/created_at düşürüldü


def test_reader_sahiplik_None_ise_bos(monkeypatch):
    from ragintel.database import conversation_repo
    monkeypatch.setattr(conversation_repo, "get_conversation_messages",
                        lambda conn, cid, uid: None)  # sahip değil/silinmiş
    fn, fake_self = _bind_reader()
    assert fn(fake_self, "sess-1", "u1", 3) == []


def test_reader_bos_conversation_id_veya_user_id_ise_bos():
    fn, fake_self = _bind_reader()
    assert fn(fake_self, "", "u1", 3) == []
    assert fn(fake_self, "sess-1", "", 3) == []


def test_reader_db_hatasi_yutulur_bos_doner(monkeypatch):
    from ragintel.database import conversation_repo

    def _boom(conn, cid, uid):
        raise RuntimeError("db down")

    monkeypatch.setattr(conversation_repo, "get_conversation_messages", _boom)
    fn, fake_self = _bind_reader()
    assert fn(fake_self, "sess-1", "u1", 3) == []  # crash yerine boş (fail-safe)

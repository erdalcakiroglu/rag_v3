"""M-15 aşama streaming'i (/api/ask/stream) — sözleşme testleri.

Üç şeyi kilitler:
1. AKAN YOL, MÜHÜRLÜ YOLDAN SAPMAZ. `run_agent` (M-16 karnesinin ölçtüğü yol)
   bilerek DEĞİŞTİRİLMEDİ; `run_agent_stream` ayrı bir fonksiyondur. Bedeli ikisinin
   sapabilmesi — `test_stream_and_invoke_agree` o bedeli öder.
2. OLAY YÜKÜ ALLOWLIST'TİR. `user_ctx` / `allowed_doc_scopes` / chunk metni hiçbir
   olayda görünmez. Bu, projenin en eski güvenlik kuralının (LLM'e ve istemciye scope
   SIZMAZ) yeni bir yüzeye taşınmasıdır.
3. AKIŞ, /api/ask ile AYNI FinalResponse'u taşır ve auth fail-closed kalır.
"""

from __future__ import annotations

import json
from operator import add
from typing import Annotated, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from ragintel.agents.graph import run_agent, run_agent_stream
from ragintel.api.runtime import _EVENT_QUERY_MAX, node_event, tool_event


# --- 1. Akan yol ile mühürlü yol aynı sonucu vermeli --------------------------
class _ToyState(TypedDict, total=False):
    # NOT: `from __future__ import annotations` bu ek açıklamaları STRING yapar ve
    # LangGraph şemayı MODÜL global'inden çözer → Annotated/add modül seviyesinde
    # olmak zorunda (fonksiyon içi import NameError verir).
    steps: Annotated[list, add]
    retrieved: list
    pending_tool_calls: list
    final_response: dict


def _toy_graph():
    """Gerçek grafın topolojisini taklit eden minik graf (LLM/DB yok):
    prepare → agent → tools → agent → compose."""

    def prepare(s):
        return {"steps": ["prepare"]}

    def agent(s):
        if s.get("retrieved"):
            return {"steps": ["agent"], "pending_tool_calls": []}
        return {"steps": ["agent"],
                "pending_tool_calls": [{"id": "1", "name": "search_hybrid",
                                        "arguments": {"query": "karbon vergisi"}}]}

    def tools(s):
        return {"steps": ["tools"], "retrieved": [{"chunk_id": 1}, {"chunk_id": 2}],
                "pending_tool_calls": []}

    def compose(s):
        return {"steps": ["compose"], "final_response": {"answer": "ok", "meta": {}}}

    g = StateGraph(_ToyState)
    for name, fn in (("prepare", prepare), ("agent", agent), ("tools", tools), ("compose", compose)):
        g.add_node(name, fn)
    g.add_edge(START, "prepare")
    g.add_edge("prepare", "agent")
    g.add_conditional_edges("agent", lambda s: "tools" if s.get("pending_tool_calls") else "compose",
                            {"tools": "tools", "compose": "compose"})
    g.add_edge("tools", "agent")
    g.add_edge("compose", END)
    return g.compile()


def _drain(gen):
    """Generator'ı tüketip (yield'ler, dönüş değeri) çiftini verir."""
    items = []
    while True:
        try:
            items.append(next(gen))
        except StopIteration as stop:
            return items, stop.value


def test_stream_and_invoke_agree():
    """İki yol AYNI final state'i üretmeli — akış eklemek ölçülen davranışı kaydırmaz."""
    app = _toy_graph()
    blocking = run_agent(app, {"retrieved": []})
    _, streamed = _drain(run_agent_stream(app, {"retrieved": []}))

    # trace_id koşuma özgüdür (her span yeni id üretir) → eşitlik dışında tutulur,
    # ama İKİ yolda da DOLU olmalı: feedback/korelasyon sözleşmesi ikisinde de sürer.
    a, b = dict(streamed["final_response"]), dict(blocking["final_response"])
    assert a.pop("meta")["trace_id"] and b.pop("meta")["trace_id"]
    assert a == b
    assert streamed["steps"] == blocking["steps"] == ["prepare", "agent", "tools", "agent", "compose"]


def test_stream_yields_nodes_in_execution_order():
    events, _ = _drain(run_agent_stream(_toy_graph(), {"retrieved": []}))
    assert [node for node, _ in events] == ["prepare", "agent", "tools", "agent", "compose"]
    # Tool NİYETİ, tool çalışmadan ÖNCEKİ agent güncellemesinde olmalı (algılanan
    # gecikmenin tüm kazancı buna dayanır: kullanıcı beklerken görsün, sonra değil).
    first_agent = next(u for n, u in events if n == "agent")
    assert first_agent["pending_tool_calls"][0]["name"] == "search_hybrid"


# --- 2. Olay yükü: allowlist -------------------------------------------------
def test_tool_event_drops_everything_but_query_and_counts():
    call = {
        "id": "x", "name": "search_hybrid",
        "arguments": {
            "query": "karbon vergisi",
            # Bunların HİÇBİRİ olaya çıkmamalı:
            "user_ctx": {"user_id": "u1", "allowed_doc_scopes": ["gizli"]},
            "allowed_doc_scopes": ["gizli"], "tenant_id": "t1",
            "filters": {"file_name": "gizli-rapor.pdf"},
            "yeni_alan_2027": "ileride eklenen bir şey",
        },
    }
    ev = tool_event(call)
    assert ev == {"name": "search_hybrid", "query": "karbon vergisi"}
    blob = json.dumps(ev, ensure_ascii=False)
    for secret in ("allowed_doc_scopes", "gizli", "u1", "t1", "gizli-rapor.pdf", "yeni_alan_2027"):
        assert secret not in blob


def test_tool_event_truncates_model_generated_query():
    ev = tool_event({"name": "search_hybrid", "arguments": {"query": "A" * 5000}})
    assert len(ev["query"]) == _EVENT_QUERY_MAX


def test_tool_event_counts_not_ids():
    ev = tool_event({"name": "rerank", "arguments": {"query": "q", "chunk_ids": [7, 8, 9]}})
    assert ev["chunk_count"] == 3 and "chunk_ids" not in ev


def test_node_event_emits_counts_not_content():
    ev = node_event("tools", {"retrieved": [{"chunk_id": 1, "text": "GİZLİ METİN"}]}, 100)
    assert ev == {"event": "retrieved", "data": {"total": 1, "t_ms": 100}}
    assert "GİZLİ" not in json.dumps(ev, ensure_ascii=False)


def test_node_event_unknown_node_is_silent():
    """Grafa yeni düğüm eklenirse içeriği KENDİLİĞİNDEN akmaz (fail-closed)."""
    assert node_event("yeni_dugum", {"user_ctx": {"allowed_doc_scopes": ["gizli"]}}, 1) is None


def test_node_event_never_forwards_state_wholesale():
    """Bilinen düğümlerde bile update sözlüğü olduğu gibi geçirilmemeli."""
    poisoned = {"user_ctx": {"allowed_doc_scopes": ["gizli"]}, "messages": [{"content": "GİZLİ"}]}
    for node in ("prepare", "agent", "validate", "compose", "fallback", "tools"):
        ev = node_event(node, dict(poisoned), 5)
        assert ev is not None
        assert "gizli" not in json.dumps(ev, ensure_ascii=False).lower()


# --- 3. SSE kare biçimi ------------------------------------------------------
def test_sse_frame_cannot_be_broken_by_newlines_in_model_text():
    """Model üretimi metindeki \\n\\n, kare sınırını KIRAMAZ (json kaçışı yapar)."""
    from ragintel.api.app import _sse_frame

    frame = _sse_frame("tool", {"query": "satır1\n\nsatır2"})
    assert frame.count("\n\n") == 1 and frame.endswith("\n\n")
    assert frame.startswith("event: tool\ndata: {")


# --- 4. Uçtan uca (canlı DB gerektirir; yoksa atlanır) ------------------------
def _sse_parse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        name, data = "message", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        events.append((name, json.loads(data)))
    return events


@pytest.fixture()
def stream_client(live_db):
    from fastapi.testclient import TestClient
    from ragintel.api.app import create_app
    from tests.test_faz7_api import _runtime

    return TestClient(create_app(_runtime(live_db)))


def test_stream_requires_auth_before_body_opens(stream_client):
    """AuthN hatası SSE gövdesine gömülmez — gerçek 401 olur (istemci girişe yönlensin)."""
    r = stream_client.post("/api/ask/stream", json={"question": "karbon vergisi nedir?"})
    assert r.status_code == 401
    assert "text/event-stream" not in r.headers.get("content-type", "")


def test_stream_rejects_empty_question_with_400(stream_client):
    r = stream_client.post("/api/ask/stream", json={"question": "   "},
                           headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 400


def test_stream_delivers_progress_then_same_final_as_blocking(stream_client):
    auth = {"Authorization": "Bearer test-token"}
    body = {"question": "karbon vergisi nedir?"}
    r = stream_client.post("/api/ask/stream", json=body, headers=auth)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")

    events = _sse_parse(r.text)
    names = [n for n, _ in events]
    assert names[0] == "open" and names[-1] == "final"
    assert "step" in names, "ilerleme olayı hiç gitmemiş — algılanan gecikme kazancı yok"

    # Akan final, bloklayan ucun final'i ile AYNI şekilde doğrulanabilir olmalı.
    final = dict(events[-1][1])
    blocking = stream_client.post("/api/ask", json=body, headers=auth).json()
    assert set(final) == set(blocking)
    assert final["confidence"] in ("low", "medium", "high")

    # Hiçbir olayda scope/user_ctx görünmemeli.
    blob = json.dumps(events, ensure_ascii=False)
    assert "allowed_doc_scopes" not in blob and "user_ctx" not in blob


def test_client_abort_releases_the_runtime_lock(live_db):
    """İSTEMCİ 'Durdur'a basınca sunucu tarafı ne oluyor? (önyüz revizyonu, gözden geçirme #2)

    Önyüzdeki `AbortController` bağlantıyı keser; ASGI katmanı da yanıt
    generator'ını KAPATIR. Kapanış, `ask_stream` içinde `with self._lock:`
    bloğunun İÇİNDEKİ `yield` noktasında `GeneratorExit` olarak gelir. Bu test
    kritik sonucu kilitler: **kilit bırakılır**. Bırakılmasaydı tek bir iptal
    tüm servisi süresiz kilitlerdi (`_lock` graf çağrılarını serileştiriyor).

    NOT — bilinçli davranış: iptal turu YARIDA bırakır (bir sonraki düğüm
    sınırında durur), `_ask_epilogue` koşmaz → o tur geçmişe yazılmaz. Yarım
    checkpoint bir sonraki turu zehirlemez, çünkü `prepare_state` her turda
    `messages=None` ile scratchpad'i sıfırlar.
    """
    from tests.test_faz7_api import _runtime

    rt = _runtime(live_db)
    gen = rt.ask_stream("karbon vergisi nedir?", None, "test-token")

    assert next(gen)["event"] == "open"
    assert not rt._lock.locked(), "ilk olay kilit BEKLENMEDEN gitmeliydi (TTFB)"

    next(gen)                       # ilk ilerleme olayı → artık kilidin İÇİNDEYİZ
    assert rt._lock.locked()

    gen.close()                     # istemci koptu (RuntimeError atarsa: GeneratorExit yutulmuş)
    assert not rt._lock.locked(), "iptal sonrası kilit sızdı — sonraki istek süresiz beklerdi"

    # Kilit gerçekten yeniden alınabilir olmalı: yarım-durumda takılma yok.
    assert rt._lock.acquire(timeout=1)
    rt._lock.release()


def test_open_event_carries_session_and_precedes_work(stream_client):
    r = stream_client.post("/api/ask/stream", json={"question": "karbon vergisi nedir?"},
                           headers={"Authorization": "Bearer test-token"})
    name, data = _sse_parse(r.text)[0]
    assert name == "open"
    assert data["session_id"] and r.headers["X-Session-Id"] == data["session_id"]
    # TTFB iddiasının testi: ilk olay LLM turlarından ÖNCE üretilir.
    assert data["t_ms"] < 1000

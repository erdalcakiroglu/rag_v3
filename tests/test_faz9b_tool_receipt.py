"""Tasarım §9b/8 — tool-result MAKBUZU: tam chunk metni yalnızca
`state.retrieved` → context yolunda taşınır; tool-result mesajına kompakt
makbuz ({found_chunk_ids, count, note}) konur (çift-taşıma / prompt şişmesi
önlenir). Makbuzdaki chunk_id'ler GERÇEK id'lerdir → lookup_document/rerank
onlarla çalışır."""

from __future__ import annotations

import json

from ragintel.agents.nodes.tools_node import _tool_receipt, tools_node
from ragintel.agents.tools import ToolRegistry


def _chunk(chunk_id, text):
    return {
        "chunk_id": chunk_id,
        "text": text,
        "score": 0.9,
        "source": {"file_id": 1, "file_name": "a.pdf", "page": 1, "section": "Vergi", "version": 1},
        "retrieval_method": "hybrid",
    }


def _user_ctx():
    return {"user_id": "u1", "tenant_id": "t1", "roles": ["user"], "allowed_doc_scopes": ["s1"]}


class RecordingService:
    """Aldığı chunk_id'leri kaydeder — makbuz id'lerinin lookup/rerank'e
    doğru aktığını kanıtlamak için."""

    def __init__(self, chunks):
        self._by_id = {c["chunk_id"]: c for c in chunks}
        self.lookup_ids: list[int] = []
        self.rerank_ids: list[int] = []

    def search_hybrid(self, query, top_k=10, filters=None, *, user_ctx):
        return list(self._by_id.values())

    def lookup_document(self, chunk_id, window=2, *, user_ctx):
        self.lookup_ids.append(chunk_id)
        c = self._by_id.get(chunk_id)
        return [c] if c else []

    def rerank(self, query, chunk_ids, *, user_ctx):
        self.rerank_ids = list(chunk_ids)
        return [{"chunk_id": cid, "rerank_score": 1.0} for cid in chunk_ids if cid in self._by_id]


# --- _tool_receipt birim ------------------------------------------------------
def test_receipt_compacts_chunk_output():
    out = _tool_receipt({"chunks": [_chunk(10, "metin bir"), _chunk(11, "metin iki")]})
    assert out["found_chunk_ids"] == [10, 11]
    assert out["count"] == 2
    assert "note" in out
    # Tam metin makbuzda YOK (çift-taşıma önlenir).
    assert "text" not in json.dumps(out, ensure_ascii=False)


def test_receipt_passes_through_non_chunk_output():
    # rerank ranking'i (kontrol-akışı sinyali) olduğu gibi kalır.
    ranking = {"ranking": [{"chunk_id": 10, "rerank_score": 1.0}]}
    assert _tool_receipt(ranking) == ranking
    # error / memory de dokunulmaz.
    assert _tool_receipt({"error": "tool_error: x"}) == {"error": "tool_error: x"}
    assert _tool_receipt({"memory": []}) == {"memory": []}


def test_receipt_empty_chunks_is_zero_count():
    out = _tool_receipt({"chunks": []})
    assert out["found_chunk_ids"] == [] and out["count"] == 0


# --- tools_node entegrasyonu --------------------------------------------------
def test_search_puts_receipt_in_message_but_full_chunks_in_retrieved():
    svc = RecordingService([_chunk(10, "karbon vergisi emisyonu"), _chunk(11, "ikinci parça")])
    reg = ToolRegistry(svc, include_memory=False)
    state = {
        "pending_tool_calls": [{"id": "c1", "name": "search_hybrid", "arguments": {"query": "x"}}],
        "user_ctx": _user_ctx(),
        "retrieved": [],
    }
    out = tools_node(state, registry=reg)

    # Tool-result mesajı = MAKBUZ (tam metin yok).
    content = out["messages"][0]["content"]
    receipt = json.loads(content)
    assert receipt["found_chunk_ids"] == [10, 11]
    assert receipt["count"] == 2
    assert "karbon vergisi" not in content  # chunk metni makbuzda taşınmıyor

    # retrieved (→ context yolu) tam chunk'ları taşır.
    assert [c["chunk_id"] for c in out["retrieved"]] == [10, 11]
    assert out["retrieved"][0]["text"] == "karbon vergisi emisyonu"


def test_receipt_chunk_ids_drive_lookup_and_rerank():
    svc = RecordingService([_chunk(10, "birinci"), _chunk(11, "ikinci")])
    reg = ToolRegistry(svc, include_memory=False)

    # 1) search → makbuz.
    out1 = tools_node(
        {
            "pending_tool_calls": [{"id": "c1", "name": "search_hybrid", "arguments": {"query": "x"}}],
            "user_ctx": _user_ctx(),
            "retrieved": [],
        },
        registry=reg,
    )
    ids = json.loads(out1["messages"][0]["content"])["found_chunk_ids"]
    assert ids == [10, 11]

    # 2) Makbuzdaki id'lerle lookup_document + rerank → servis o id'leri almalı.
    out2 = tools_node(
        {
            "pending_tool_calls": [
                {"id": "c2", "name": "lookup_document", "arguments": {"chunk_id": ids[0]}},
                {"id": "c3", "name": "rerank", "arguments": {"query": "x", "chunk_ids": ids}},
            ],
            "user_ctx": _user_ctx(),
            "retrieved": out1["retrieved"],
        },
        registry=reg,
    )
    assert svc.lookup_ids == [10]        # makbuz id'si lookup_document'e ulaştı
    assert svc.rerank_ids == [10, 11]    # makbuz id'leri rerank'e ulaştı

    # rerank çıktısı (ranking) makbuza İNMEZ — kontrol sinyali korunur.
    rerank_msg = json.loads(out2["messages"][1]["content"])
    assert "ranking" in rerank_msg and rerank_msg["ranking"][0]["chunk_id"] == 10

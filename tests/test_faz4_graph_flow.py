"""FAZ 4 graf akış testleri (mock LLM). Gerçek graph + gerçek deterministik
node'lar; yalnızca LLM gateway ve context builder taklit edilir.

Senaryolar: happy path, validation-fail→retry→pass, retry→fail→fallback,
bütçe aşımı→zorla yanıt (yalnızca submit_answer sunulur).
"""

from __future__ import annotations

import json

from ragintel.agents.graph import build_agent_graph
from ragintel.agents.tools import ToolRegistry
from ragintel.config.loader import load_config
from ragintel.llm.gateway import LLMResponse, ToolCall


# --- Mock altyapı -------------------------------------------------------------
class ScriptedGateway:
    """Önceden belirlenmiş aksiyon dizisi döndürür. Aksiyon:
    ("tool", name, args) | ("submit", answer, citations)."""

    def __init__(self, script, *, tokens=(10, 5)):
        self.script = list(script)
        self.calls = []
        self.tokens = tokens

    def complete(self, *, messages, tools, max_retries=None, timeout=None):
        self.calls.append({"messages": messages, "tools": tools})
        action = self.script.pop(0)
        pt, ct = self.tokens
        name = "submit_answer" if action[0] == "submit" else action[1]
        args = {"answer": action[1], "citations": action[2]} if action[0] == "submit" else action[2]
        tc = ToolCall(id=f"call{len(self.calls)}", name=name, arguments=args)
        raw = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": tc.id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}],
        }
        return LLMResponse(content=None, tool_calls=[tc], prompt_tokens=pt, completion_tokens=ct, raw_message=raw)


class FakeContextBuilder:
    """retrieved → her chunk bir blok (İP-3.4 kontratı biçiminde).

    kol-2(b) append-only: `prior` verilirse gösterilmiş bloklar NUMARASIYLA BİREBİR
    yeniden yayılır (yeniden-numaralama yok), yalnız YENİ chunk'lar daha yüksek
    numarayla sona eklenir. `prior=None` → ilk tur, eski stateless davranışla birebir.
    """

    def build(self, retrieved, prior=None):
        shown_blocks = [dict(b) for b in (prior or {}).get("blocks", [])]
        shown_citations = [dict(c) for c in (prior or {}).get("citations", [])]
        shown_ids = {cid for b in shown_blocks for cid in b["chunk_ids"]}
        blocks, citations = list(shown_blocks), list(shown_citations)
        n = max((int(b["n"]) for b in shown_blocks), default=0) + 1
        for ch in retrieved:
            if ch["chunk_id"] in shown_ids:      # gösterilmiş → dondurulmuş blokta zaten var
                continue
            blocks.append(
                {
                    "n": n,
                    "label": f"[{n}] {ch['source']['file_name']}",
                    "text": ch["text"],
                    "chunk_ids": [ch["chunk_id"]],
                    "token_count": len(ch["text"].split()),
                    "low_quality": False,
                }
            )
            citations.append(
                {
                    "n": n,
                    "file_name": ch["source"]["file_name"],
                    "page": ch["source"].get("page"),
                    "sheet": None,
                    "section": ch["source"].get("section"),
                    "chunk_id": ch["chunk_id"],
                }
            )
            n += 1
        return {"blocks": blocks, "citations": citations, "dropped_chunk_ids": []}


class FakeService:
    def __init__(self, chunks):
        self._chunks = chunks

    def search_hybrid(self, query, top_k=10, filters=None, *, user_ctx):
        return list(self._chunks)

    def lookup_document(self, chunk_id, window=2, *, user_ctx):
        return []

    def rerank(self, query, chunk_ids, *, user_ctx):
        return []


def _chunk(chunk_id, text, *, file_id=1, file_name="a.pdf", page=1, section="Vergi"):
    return {
        "chunk_id": chunk_id,
        "text": text,
        "score": 0.9,
        "source": {"file_id": file_id, "file_name": file_name, "page": page, "section": section, "version": 1},
        "retrieval_method": "hybrid",
    }


def _cfg(**agent):
    base = {"validation_coverage_threshold": 0.7, "confidence_high_coverage_threshold": 0.9}
    base.update(agent)
    return load_config(db_reader=lambda: {"agent": base})


def _user_ctx():
    return {"user_id": "u1", "tenant_id": "t1", "roles": ["user"], "allowed_doc_scopes": ["s1"]}


def _initial(retrieved=None):
    return {"query": "karbon vergisi", "user_ctx": _user_ctx(), "session_id": "sess-1", "retrieved": retrieved or []}


_GOOD_ANSWER = "Karbon vergisi emisyonu fiyatlar."
_GOOD_CITATION = [{"claim": _GOOD_ANSWER, "chunk_id": 10, "quote": "emisyonu fiyatlar"}]
_BAD_CITATION = [{"claim": "Yanlış.", "chunk_id": 10, "quote": "hicbir yerde olmayan ifade"}]
_CHUNK = _chunk(10, "karbon vergisi emisyonu fiyatlar ve davranisi degistirir")


# --- Testler ------------------------------------------------------------------
def test_happy_path_search_then_answer_passes_and_composes():
    gw = ScriptedGateway([("tool", "search_hybrid", {"query": "karbon"}), ("submit", _GOOD_ANSWER, _GOOD_CITATION)])
    app = build_agent_graph(
        gateway=gw,
        context_builder=FakeContextBuilder(),
        registry=ToolRegistry(FakeService([_CHUNK])),
        config=_cfg(),
    )
    out = app.invoke(_initial())
    final = out["final_response"]
    assert final["answer"] == _GOOD_ANSWER
    assert final["confidence"] == "high"
    assert final["sources"][0]["chunk_id"] == 10
    assert out["validation"]["passed"] is True


def test_validation_fail_then_retry_passes_medium_confidence():
    gw = ScriptedGateway([("submit", "Yanlış.", _BAD_CITATION), ("submit", _GOOD_ANSWER, _GOOD_CITATION)])
    app = build_agent_graph(
        gateway=gw, context_builder=FakeContextBuilder(), registry=ToolRegistry(FakeService([_CHUNK])), config=_cfg()
    )
    out = app.invoke(_initial(retrieved=[_CHUNK]))
    assert len(gw.calls) == 2               # bir retry
    assert out["retry_count"] == 1
    assert out["final_response"]["confidence"] == "medium"
    assert out["validation"]["passed"] is True


def test_retry_fail_goes_to_fallback():
    gw = ScriptedGateway([("submit", "Yanlış.", _BAD_CITATION), ("submit", "Yine yanlış.", _BAD_CITATION)])
    app = build_agent_graph(
        gateway=gw, context_builder=FakeContextBuilder(), registry=ToolRegistry(FakeService([_CHUNK])), config=_cfg()
    )
    out = app.invoke(_initial(retrieved=[_CHUNK]))
    assert len(gw.calls) == 2               # retry sonrası ikinci fail → fallback (üçüncü çağrı yok)
    final = out["final_response"]
    assert final["confidence"] == "low"
    assert "Cevap bulunamadı" in final["answer"]
    # FAZ 5: fallback → sources BOŞ; incelenen chunk meta.reviewed_sources'a.
    assert final["sources"] == []
    assert final["meta"]["reviewed_sources"]


def test_budget_exhaustion_forces_answer_with_submit_only():
    gw = ScriptedGateway([("tool", "search_hybrid", {"query": "x"}), ("submit", _GOOD_ANSWER, _GOOD_CITATION)])
    app = build_agent_graph(
        gateway=gw,
        context_builder=FakeContextBuilder(),
        registry=ToolRegistry(FakeService([_CHUNK])),
        config=_cfg(max_iterations=1),
    )
    out = app.invoke(_initial())
    # İkinci agent turunda bütçe bitmiş → yalnızca submit_answer sunulmuş olmalı.
    offered_second = [s["function"]["name"] for s in gw.calls[1]["tools"]]
    assert offered_second == ["submit_answer"]
    assert out["final_response"] is not None

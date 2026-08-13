"""kol-2 (b) — APPEND-ONLY bağlam (prefix/KV-cache disiplini).

Bu test ÖLÇÜT/LATENCY değil; (b)'nin davranış sözleşmesini kilitler (Brief_kol2 §2):
  - Gösterilmiş bloklar NUMARASIYLA ve BYTES'IYLA turlar arası KORUNUR (asla yeniden-numaralama).
  - Yeni chunk'lar yalnız SONA, daha yüksek numarayla eklenir → mesaj-2 önceki bytes'ı değişmez.
  - Gösterilmiş bloklar bütçeyi aşsa bile DÜŞMEZ; tahliye yalnız yeni adaylardan.
  - Gösterilmiş bloğa bitişik YENİ chunk o bloğa MERGE edilmez (shown mutasyona uğramaz).
  - `prior=None` (ilk tur) → eski stateless davranışla BİREBİR aynı (regresyon yok).

Ayrıca agent_node'un önceki turun `context`'ini `prior` olarak geçirdiğini (istek-yerel ledger)
doğrular. Durum builder singleton'ında DEĞİL `context` state kanalında taşınır.
"""
from __future__ import annotations

from dataclasses import dataclass

from ragintel.agents.nodes.agent import agent_node
from ragintel.config.loader import load_config
from ragintel.retrieval import ContextBuilder


# --- fakes (DB'siz, deterministik; test_ip34_context_builder deseniyle aynı) -----------------
@dataclass
class _Meta:
    chunk_id: int
    file_id: int
    chunk_index: int
    token_count: int
    page_number: int | None
    sheet_name: str | None
    section_title: str | None
    quality_score: float | None


class _Store:
    def __init__(self, mapping):
        self.mapping = mapping

    def fetch(self, chunk_ids: list[int]):
        return {cid: self.mapping[cid] for cid in chunk_ids if cid in self.mapping}


class _Counter:
    def count(self, text: str) -> int:
        return len([w for w in text.split() if w])

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        return []


def _cfg(**retrieval_cfg):
    defaults = {
        "context_token_budget": 100,
        "context_token_safety_margin": 1.0,
        "context_low_quality_threshold": 70.0,
    }
    defaults.update(retrieval_cfg)
    return load_config(db_reader=lambda: {"retrieval": defaults})


def _chunk(chunk_id, text, score, *, file_id, file_name, page=1, section="Genel"):
    return {
        "chunk_id": chunk_id,
        "text": text,
        "score": score,
        "source": {"file_id": file_id, "file_name": file_name, "page": page, "section": section, "version": 1},
        "retrieval_method": "hybrid",
    }


# --- (b) çekirdeği: numara + bytes korunur, yeni sona eklenir --------------------------------
def test_gosterilmis_bloklar_numara_ve_bytes_KORUNUR_yeni_SONA_eklenir():
    store = _Store({
        10: _Meta(10, 1, 0, 5, 2, None, "Vergi", 85.0),
        11: _Meta(11, 1, 1, 6, 2, None, "Vergi", 85.0),
        20: _Meta(20, 2, 0, 4, None, "Sayfa1", "Tablo", 90.0),
        30: _Meta(30, 3, 0, 4, 7, None, "Yeni", 90.0),
    })
    builder = ContextBuilder(config=_cfg(), token_counter=_Counter(), metadata_store=store)
    base = [
        _chunk(10, "ilk parca", 0.8, file_id=1, file_name="a.pdf", page=2, section="Vergi"),
        _chunk(11, "ikinci parca", 0.7, file_id=1, file_name="a.pdf", page=2, section="Vergi"),
        _chunk(20, "tablo satiri", 0.6, file_id=2, file_name="b.xlsx", page=None, section="Tablo"),
    ]
    t1 = builder.build(list(base))
    assert [b["chunk_ids"] for b in t1["blocks"]] == [[10, 11], [20]]  # [1]={10,11}, [2]={20}

    t2 = builder.build(base + [_chunk(30, "yeni parca", 0.95, file_id=3, file_name="c.pdf", page=7, section="Yeni")], prior=t1)
    # gösterilmiş bloklar BİREBİR (dict eşitliği: n/label/text/chunk_ids/token_count/low_quality)
    assert t2["blocks"][0] == t1["blocks"][0]
    assert t2["blocks"][1] == t1["blocks"][1]
    # yeni chunk yalnız SONA, yeni numarayla
    assert t2["blocks"][2]["n"] == 3
    assert t2["blocks"][2]["chunk_ids"] == [30]
    assert t2["blocks"][2]["label"].startswith("[3] c.pdf")
    assert [b["n"] for b in t2["blocks"]] == [1, 2, 3]  # monoton + stabil
    # citation'lar: gösterilmiş + yeni, sırayla
    assert [c["chunk_id"] for c in t2["citations"]] == [10, 11, 20, 30]


def test_uc_tur_boyunca_bloklar_ONCEKININ_PREFIX_UZANTISI():
    """Prefix byte-stabilitesi: her tur öncekinin bloklarıyla BAŞLAR, yalnız sona ekler."""
    store = _Store({i: _Meta(i, i, 0, 3, 1, None, f"S{i}", 90.0) for i in (1, 2, 3)})
    builder = ContextBuilder(config=_cfg(), token_counter=_Counter(), metadata_store=store)
    r = [_chunk(1, "a", 0.9, file_id=1, file_name="1.pdf")]
    t1 = builder.build(list(r))
    r.append(_chunk(2, "b", 0.8, file_id=2, file_name="2.pdf"))
    t2 = builder.build(list(r), prior=t1)
    r.append(_chunk(3, "c", 0.7, file_id=3, file_name="3.pdf"))
    t3 = builder.build(list(r), prior=t2)
    assert t2["blocks"][: len(t1["blocks"])] == t1["blocks"]   # t2, t1 ile başlar
    assert t3["blocks"][: len(t2["blocks"])] == t2["blocks"]   # t3, t2 ile başlar
    assert [b["n"] for b in t3["blocks"]] == [1, 2, 3]


def test_gosterilmis_bloklar_butce_asilsa_da_DUSMEZ_yalniz_yeni_aday_tahliye():
    """Append-only bütçe sözleşmesi: shown ASLA düşmez; yalnız yeni aday (score,-order) ile tahliye."""
    store = _Store({
        1: _Meta(1, 1, 0, 8, 1, None, "A", 90.0),
        2: _Meta(2, 2, 0, 8, 1, None, "B", 90.0),
    })
    builder = ContextBuilder(config=_cfg(context_token_budget=10, context_token_safety_margin=1.0),
                             token_counter=_Counter(), metadata_store=store)
    t1 = builder.build([_chunk(1, "bir iki", 0.9, file_id=1, file_name="a.pdf")])  # label(4)+text(2)=6 ≤ 10
    assert [b["chunk_ids"] for b in t1["blocks"]] == [[1]]

    # t2: yeni aday 2 eklenince toplam bütçeyi aşar → shown [1] korunur, [2] düşer
    t2 = builder.build(
        [_chunk(1, "bir iki", 0.9, file_id=1, file_name="a.pdf"),
         _chunk(2, "uc dort bes alti bes", 0.2, file_id=2, file_name="b.pdf")],
        prior=t1,
    )
    assert [b["chunk_ids"] for b in t2["blocks"]] == [[1]]   # gösterilmiş korundu, yeni düştü
    assert t2["blocks"][0] == t1["blocks"][0]                # bytes değişmedi
    assert t2["dropped_chunk_ids"] == [2]


def test_gosterilmis_bloga_bitisik_YENI_chunk_AYRI_blok_shown_mutasyona_ugramaz():
    """10'a bitişik (aynı file, chunk_index+1) yeni 11, shown bloğa MERGE EDİLMEZ → ayrı [2]."""
    store = _Store({
        10: _Meta(10, 1, 0, 3, 1, None, "S", 90.0),
        11: _Meta(11, 1, 1, 3, 1, None, "S", 90.0),
    })
    builder = ContextBuilder(config=_cfg(), token_counter=_Counter(), metadata_store=store)
    t1 = builder.build([_chunk(10, "onlu", 0.9, file_id=1, file_name="a.pdf")])
    assert [b["chunk_ids"] for b in t1["blocks"]] == [[10]]

    t2 = builder.build(
        [_chunk(10, "onlu", 0.9, file_id=1, file_name="a.pdf"),
         _chunk(11, "onbirli", 0.8, file_id=1, file_name="a.pdf")],
        prior=t1,
    )
    assert [b["chunk_ids"] for b in t2["blocks"]] == [[10], [11]]  # bitişik olsa da AYRI blok
    assert t2["blocks"][0] == t1["blocks"][0]                       # shown [10] bytes DEĞİŞMEDİ
    assert t2["blocks"][1]["n"] == 2


def test_prior_none_ilk_tur_stateless_ile_BIREBIR_ayni():
    """Regresyon çiti: prior verilmezse / açıkça None ise eski davranış birebir korunur."""
    store = _Store({1: _Meta(1, 1, 0, 3, 1, None, "A", 90.0), 2: _Meta(2, 2, 0, 3, 1, None, "B", 90.0)})
    builder = ContextBuilder(config=_cfg(), token_counter=_Counter(), metadata_store=store)
    chunks = [_chunk(1, "a b", 0.9, file_id=1, file_name="a.pdf"),
              _chunk(2, "c d", 0.8, file_id=2, file_name="b.pdf")]
    without = builder.build(chunks)
    with_none = builder.build(chunks, prior=None)
    assert without == with_none
    assert [b["n"] for b in without["blocks"]] == [1, 2]


# --- agent wiring: prior = önceki turun context'i (istek-yerel ledger) -----------------------
class _Grp:
    system_prompt = ""


class _AgentCfg:
    def group(self, name):
        if name == "prompts":
            raise KeyError(name)
        return _Grp()


def test_agent_node_ONCEKI_context_i_prior_olarak_gecirir():
    """(b) entegrasyon: agent_node build()'e state['context']'i prior verir → append-only tetiklenir.
    Durum builder'da tutulmaz; prepare context=None sıfırladığı için ledger istek-yereldir."""
    captured = {}

    class _CB:
        def build(self, retrieved, prior=None):
            captured["prior"] = prior
            return {"blocks": [], "citations": [], "dropped_chunk_ids": []}

    class _Resp:
        tool_calls: list = []
        total_tokens = 1
        content = ""
        raw_message: dict = {}

    class _GW:
        def complete(self, *, messages, tools, max_retries=None):
            return _Resp()

    class _Reg:
        def llm_tool_schemas(self):
            return []

        def final_only_schemas(self):
            return []

    prior_sentinel = {
        "blocks": [{"n": 1, "label": "[1] x", "text": "t", "chunk_ids": [9], "token_count": 1, "low_quality": False}],
        "citations": [],
        "dropped_chunk_ids": [],
    }
    state = {
        "query": "soru",
        "retrieved": [{"chunk_id": 9}],
        "context": prior_sentinel,
        "budget": {"iteration": 0, "max_iterations": 3, "tokens_used": 0, "max_tokens": 1000, "deadline_ts": 0},
        "messages": [],
        "validation": None,
    }
    agent_node(state, gateway=_GW(), registry=_Reg(), context_builder=_CB(), config=_AgentCfg())
    assert captured["prior"] is prior_sentinel

"""İP-3.4 ↔ FAZ 4 köprüsü: retrieval → context builder → validate zinciri.

Bu test, tasarım kararının koştuğu köprüyü doğrular: validate node'un girdisi
İP-3.4'ün ÇIKTISIDIR. Gerçek `ContextBuilder` bloklar + citation haritası üretir;
mock bir LLM YALNIZCA bu blokları görerek yanıt + `Citation[]` üretir; gerçek
`validate_grounding` bu citation'ları context builder'ın SAKLADIĞI chunk'lara
(LLM'in gördüğü evren) karşı denetler. Bütçeyle elenen chunk'lar evrene girmez.

İzole birim testleri `test_ip34_context_builder.py` / `test_faz4_grounding.py`'de;
burada iki modülün gerçek çıktı/girdi sözleşmesinin uçtan uca oturduğunu ve
tasarım §4'teki netleştirilmiş kuralların (evren = saklanan bağlam, coverage =
benzersiz cümle) zincir boyunca tuttuğunu kanıtlıyoruz.
"""

from __future__ import annotations

from dataclasses import dataclass

from ragintel.agents.nodes import validate_state
from ragintel.agents.state import Citation
from ragintel.config.loader import load_config
from ragintel.guardrails import validate_grounding
from ragintel.retrieval import ContextBuilder
from ragintel.retrieval.types import ContextBuildResult, RetrievedChunk


# --- Test altyapısı: gerçek ContextBuilder'ı besleyen hafif stub'lar -----------
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
        return {chunk_id: self.mapping[chunk_id] for chunk_id in chunk_ids}


class _WordCounter:
    """BGE yerine deterministik kelime sayacı (ağır tokenizer yüklenmez)."""

    def count(self, text: str) -> int:
        return len([w for w in text.split() if w])

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        return []


def _retrieval_cfg(**overrides):
    defaults = {
        "context_token_budget": 25,
        "context_token_safety_margin": 1.0,
        "context_low_quality_threshold": 70.0,
    }
    defaults.update(overrides)
    return load_config(db_reader=lambda: {"retrieval": defaults})


def _agent_cfg(**overrides):
    defaults = {"validation_coverage_threshold": 0.7, "confidence_high_coverage_threshold": 0.9}
    defaults.update(overrides)
    return load_config(db_reader=lambda: {"agent": defaults})


def _chunk(chunk_id, text, score, *, file_id, file_name, page=1, section="Genel"):
    return {
        "chunk_id": chunk_id,
        "text": text,
        "score": score,
        "source": {"file_id": file_id, "file_name": file_name, "page": page, "section": section, "version": 1},
        "retrieval_method": "hybrid",
    }


def _kept_chunks(retrieved: list[RetrievedChunk], context: ContextBuildResult) -> list[RetrievedChunk]:
    """LLM'in gördüğü evren: context builder'ın sakladığı chunk'lar."""
    kept = {cid for block in context["blocks"] for cid in block["chunk_ids"]}
    return [c for c in retrieved if c["chunk_id"] in kept]


def _grounded_mock_llm(context: ContextBuildResult) -> tuple[str, list[Citation]]:
    """İyi davranan LLM: yalnızca üretilen bloklardan alıntı yapar.

    Her blok için bir iddia cümlesi + o bloğun metninden birebir çekilmiş bir
    quote üretir. Gerçek grounded modelin davranışını taklit eder.
    """
    sentences: list[str] = []
    citations: list[Citation] = []
    for block in context["blocks"]:
        words = block["text"].split()
        quote = " ".join(words[:3])  # blok metninden birebir alıntı
        claim = f"{block['label'].split(']')[0]}] iddiasi."
        sentences.append(claim)
        citations.append({"claim": claim, "chunk_id": block["chunk_ids"][0], "quote": quote})
    return " ".join(sentences), citations


# --- Köprü senaryosu ----------------------------------------------------------
def _scenario():
    """3 dosya: dosya1 komşu iki chunk (merge), dosya2 tek chunk, dosya3 düşük
    skorlu (bütçeyle elenmeli)."""
    store = _Store(
        {
            100: _Meta(100, 1, 0, 6, 3, None, "Vergi", 88.0),
            101: _Meta(101, 1, 1, 6, 3, None, "Vergi", 88.0),
            200: _Meta(200, 2, 0, 6, None, "Sayfa1", "Tablo", 90.0),
            300: _Meta(300, 3, 0, 6, 9, None, "Ek", 90.0),
        }
    )
    retrieved = [
        _chunk(100, "karbon vergisi emisyonu fiyatlar", 0.90, file_id=1, file_name="a.pdf", page=3, section="Vergi"),
        _chunk(101, "ve davranisi degistirir boylece", 0.85, file_id=1, file_name="a.pdf", page=3, section="Vergi"),
        _chunk(200, "tablo enerji tuketimini ozetler", 0.80, file_id=2, file_name="b.xlsx", page=None, section="Tablo"),
        _chunk(300, "alakasiz dusuk skorlu icerik", 0.10, file_id=3, file_name="c.pdf", page=9, section="Ek"),
    ]
    builder = ContextBuilder(config=_retrieval_cfg(), token_counter=_WordCounter(), metadata_store=store)
    return retrieved, builder


def test_bridge_grounded_answer_passes_end_to_end():
    retrieved, builder = _scenario()
    context = builder.build(retrieved)

    # Mock LLM yalnızca üretilen bağlamı görür ve ondan alıntı yapar.
    draft_answer, citations = _grounded_mock_llm(context)

    # validate_state evreni context'ten türetir (retrieved tam havuz olsa da).
    state = {"draft_answer": draft_answer, "citations": citations, "retrieved": retrieved, "context": context}
    out = validate_state(state, config=_agent_cfg())

    assert out["validation"]["passed"] is True
    assert out["validation"]["issues"] == []
    assert out["validation"]["coverage"] >= 0.7


def test_bridge_citation_universe_matches_builder_map_and_excludes_dropped():
    """LLM'in yasal citation evreni = context builder'ın citation haritası;
    bütçeyle elenen chunk bu evrende YOKTUR."""
    retrieved, builder = _scenario()
    context = builder.build(retrieved)

    assert 300 in context["dropped_chunk_ids"]  # düşük skorlu chunk elenmeli
    map_chunk_ids = {c["chunk_id"] for c in context["citations"]}
    block_chunk_ids = {cid for block in context["blocks"] for cid in block["chunk_ids"]}
    assert map_chunk_ids == block_chunk_ids  # citation haritası bloklarla kayıpsız örtüşür
    assert 300 not in map_chunk_ids           # elenen chunk alıntılanamaz

    draft_answer, citations = _grounded_mock_llm(context)
    assert {c["chunk_id"] for c in citations} <= map_chunk_ids
    res = validate_grounding(
        draft_answer=draft_answer,
        citations=citations,
        context_chunks=_kept_chunks(retrieved, context),
        coverage_threshold=0.7,
    )
    assert res["passed"] is True


def test_bridge_citation_to_budget_dropped_chunk_fails():
    """(a) Elenen chunk'a citation → citation_not_in_context, gerçek quote olsa bile."""
    retrieved, builder = _scenario()
    context = builder.build(retrieved)
    assert 300 in context["dropped_chunk_ids"]

    # chunk 300'ün GERÇEK metninden alıntı — ama LLM onu görmedi (elendi).
    citations: list[Citation] = [{"claim": "Elenen iddia.", "chunk_id": 300, "quote": "alakasiz dusuk skorlu"}]
    state = {"draft_answer": "Elenen iddia.", "citations": citations, "retrieved": retrieved, "context": context}
    out = validate_state(state, config=_agent_cfg())

    assert out["validation"]["passed"] is False
    assert "citation_not_in_context:300" in out["validation"]["issues"]


def test_bridge_multiple_citations_on_one_sentence_do_not_inflate_coverage():
    """(b) Tek cümleye 3 citation coverage'ı şişirmez (benzersiz cümle sayılır)."""
    retrieved, builder = _scenario()
    context = builder.build(retrieved)
    kept = _kept_chunks(retrieved, context)  # 100, 101, 200

    # İki cümle; 3 geçerli citation'ın hepsi YALNIZCA birinci cümleye bağlı.
    citations: list[Citation] = [
        {"claim": "Bir cumle.", "chunk_id": 100, "quote": "karbon vergisi"},
        {"claim": "Bir cumle.", "chunk_id": 101, "quote": "ve davranisi"},
        {"claim": "Bir cumle.", "chunk_id": 200, "quote": "tablo enerji"},
    ]
    res = validate_grounding(
        draft_answer="Bir cumle. Iki cumle.",
        citations=citations,
        context_chunks=kept,
        coverage_threshold=0.7,
    )
    # 2 cümleden yalnızca 1'i kapsanmış → 0.5, şişme yok (1.5 veya 1.0 değil).
    assert res["coverage"] == 0.5


def test_bridge_invalid_citation_does_not_contribute_to_coverage():
    """(c) Geçersiz citation (elenen chunk) coverage payına girmez."""
    retrieved, builder = _scenario()
    context = builder.build(retrieved)
    kept = _kept_chunks(retrieved, context)

    citations: list[Citation] = [
        {"claim": "Bir cumle.", "chunk_id": 100, "quote": "karbon vergisi"},  # geçerli
        {"claim": "Iki cumle.", "chunk_id": 300, "quote": "alakasiz dusuk"},   # geçersiz (elendi)
    ]
    res = validate_grounding(
        draft_answer="Bir cumle. Iki cumle.",
        citations=citations,
        context_chunks=kept,
        coverage_threshold=0.7,
    )
    # İkinci cümle yalnızca geçersiz citation'a bağlı → kapsanmaz. coverage = 1/2.
    assert res["coverage"] == 0.5
    assert "citation_not_in_context:300" in res["issues"]


def test_bridge_fabricated_quote_is_flagged_through_real_context():
    """Uydurma alıntı gerçek zincir boyunca yakalanır."""
    retrieved, builder = _scenario()
    context = builder.build(retrieved)

    good_chunk_id = context["blocks"][0]["chunk_ids"][0]
    citations: list[Citation] = [
        {"claim": "Uydurma.", "chunk_id": good_chunk_id, "quote": "bu ifade hicbir blokta yok"},
    ]
    res = validate_grounding(
        draft_answer="Uydurma iddia.",
        citations=citations,
        context_chunks=_kept_chunks(retrieved, context),
        coverage_threshold=0.7,
    )
    assert res["passed"] is False
    assert f"fabricated_quote:{good_chunk_id}" in res["issues"]

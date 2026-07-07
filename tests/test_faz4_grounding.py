"""FAZ 4 grounding/validate/compose/fallback deterministik çekirdeği."""

from __future__ import annotations

from ragintel.agents.nodes import compose_response, fallback_response, validate_state
from ragintel.config.loader import load_config
from ragintel.guardrails import validate_grounding


def _cfg(**agent_overrides):
    return load_config(db_reader=lambda: {"agent": {"validation_coverage_threshold": 0.7, "confidence_high_coverage_threshold": 0.9, **agent_overrides}})


def _chunk(chunk_id: int, text: str, *, file_name="a.pdf", page=1, section="Vergi"):
    return {
        "chunk_id": chunk_id,
        "text": text,
        "score": 0.9,
        "source": {"file_id": 1, "file_name": file_name, "page": page, "section": section, "version": 1},
        "retrieval_method": "hybrid",
    }


def _ctx(*chunk_ids: int):
    """Validate node'un beklediği minimal context build result (verilen chunk'lar saklandı)."""
    return {
        "blocks": [
            {"n": 1, "label": "", "text": "", "chunk_ids": list(chunk_ids), "token_count": 0, "low_quality": False}
        ],
        "citations": [{"n": 1, "file_name": "a.pdf", "page": 1, "sheet": None, "section": "Vergi", "chunk_id": cid} for cid in chunk_ids],
        "dropped_chunk_ids": [],
    }


def test_validate_grounding_passes_on_valid_quote_and_coverage():
    res = validate_grounding(
        draft_answer="Karbon vergisi emisyonu fiyatlar.",
        citations=[{"claim": "Karbon vergisi emisyonu fiyatlar.", "chunk_id": 10, "quote": "emisyonu fiyatlar"}],
        context_chunks=[_chunk(10, "Karbon vergisi emisyonu fiyatlar ve davranışı değiştirir.")],
        coverage_threshold=0.7,
    )
    assert res["passed"] is True
    assert res["coverage"] == 1.0


def test_validate_grounding_flags_missing_chunk_and_unsupported_quote():
    # Halüsinasyon: quote'un içeriği bağlamda YOK → düşük örtüşme → unsupported_quote.
    res = validate_grounding(
        draft_answer="Bir. İki.",
        citations=[
            {"claim": "Bir.", "chunk_id": 10, "quote": "olmayan uydurma ifade"},
            {"claim": "İki.", "chunk_id": 99, "quote": "x"},
        ],
        context_chunks=[_chunk(10, "Karbon vergisi emisyonu fiyatlar.")],
        coverage_threshold=0.9,
    )
    assert res["passed"] is False
    assert "unsupported_quote:10" in res["issues"]
    assert "citation_not_in_context:99" in res["issues"]


def test_validate_grounding_accepts_faithful_paraphrase():
    # §4 gevşetme: quote birebir değil ama içeriği bağlamda geçiyor (parafraz) →
    # kabul; answer cümlesi de claim'le token-örtüşmesiyle kapsanır → coverage 1.0.
    res = validate_grounding(
        draft_answer="Karbon vergisini ilk uygulayan ülke Finlandiya olmuştur.",
        citations=[{"claim": "İlk uygulayan ülke Finlandiya.", "chunk_id": 10,
                    "quote": "ilk uygulayan ülke 1990 yılında Finlandiya olmuştur"}],
        context_chunks=[_chunk(10, "Karbon vergisini ilk uygulayan ülke 1990 yılında Finlandiya olmuştur.")],
        coverage_threshold=0.7,
    )
    assert res["passed"] is True
    assert res["coverage"] == 1.0


def test_validate_grounding_tolerates_malformed_citations():
    # Gerçek (küçük) modeller str/eksik-alan citation üretebilir → crash YOK.
    res = validate_grounding(
        draft_answer="Bir cümle.",
        citations=["ham string", {"claim": "Bir cümle."}, {"chunk_id": "abc"}, {"claim": "x", "chunk_id": 10, "quote": "yok"}],
        context_chunks=[_chunk(10, "Karbon vergisi emisyonu fiyatlar.")],
        coverage_threshold=0.7,
    )
    assert res["passed"] is False
    assert "malformed_citation" in res["issues"]


def test_validate_state_writes_validation_result():
    state = {
        "draft_answer": "Karbon vergisi emisyonu fiyatlar.",
        "citations": [{"claim": "Karbon vergisi emisyonu fiyatlar.", "chunk_id": 10, "quote": "emisyonu fiyatlar"}],
        "retrieved": [_chunk(10, "Karbon vergisi emisyonu fiyatlar ve davranışı değiştirir.")],
        "context": _ctx(10),
    }
    out = validate_state(state, config=_cfg())
    assert out["validation"]["passed"] is True


def test_compose_response_builds_final_contract():
    state = {
        "draft_answer": "Yanıt [1]",
        "citations": [{"claim": "Yanıt", "chunk_id": 10, "quote": "emisyonu fiyatlar"}],
        "retrieved": [_chunk(10, "Karbon vergisi emisyonu fiyatlar ve davranışı değiştirir.")],
        "validation": {"passed": True, "coverage": 1.0, "issues": []},
        "retry_count": 0,
        "budget": {"iteration": 2, "tokens_used": 123, "max_iterations": 4, "max_tokens": 16000, "deadline_ts": 0.0},
    }
    res = compose_response(state, config=_cfg(), trace_id="abc123")
    assert res["answer"] == "Yanıt [1]"
    assert res["confidence"] == "high"
    assert res["sources"][0]["chunk_id"] == 10
    assert res["meta"]["trace_id"] == "abc123"


def test_fallback_response_is_low_confidence():
    state = {
        "citations": [{"claim": "Yanıt", "chunk_id": 10, "quote": "emisyonu fiyatlar"}],
        "retrieved": [_chunk(10, "Karbon vergisi emisyonu fiyatlar ve davranışı değiştirir.")],
        "validation": {"passed": False, "coverage": 0.1, "issues": ["low_coverage:0.1"]},
        "retry_count": 1,
        "budget": {"iteration": 4, "tokens_used": 200, "max_iterations": 4, "max_tokens": 16000, "deadline_ts": 0.0},
    }
    res = fallback_response(state, config=_cfg())
    assert res["confidence"] == "low"
    assert "Güvenilir yanıt üretilemedi" in res["answer"]

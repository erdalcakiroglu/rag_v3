"""FAZ 5 — validate v2 (toplu entailment) birim testleri (mock judge, ağ YOK).

Kapsam: entailment PASS; unsupported_claim; overconfident_hypothetical (gs-v0-034
senaryosu); fail-OPEN skip (judge erişilemez → v1 korunur); OFF iken çağrı yok;
v1 FAIL iken entailment koşmaz; feedback yeni issue tipleri.
"""

from __future__ import annotations

from ragintel.agents.nodes.agent import _feedback_message
from ragintel.agents.nodes.validate import validate_state
from ragintel.config.loader import load_config
from ragintel.guardrails import check_entailment


# --- mock judge (ağ yok) ------------------------------------------------------
class MockJudge:
    def __init__(self, verdicts=None, raise_exc=None, label="mock/dev-mode"):
        self.verdicts = verdicts
        self.raise_exc = raise_exc
        self.label = label
        self.calls = 0

    def ask_json(self, user):
        self.calls += 1
        if self.raise_exc:
            raise self.raise_exc
        return {"verdicts": self.verdicts or []}


_CHUNK_TEXT = "Karbon vergisini ilk uygulayan ülke 1990 yılında Finlandiya olmuştur."


def _state(draft, citations, chunk_text=_CHUNK_TEXT, chunk_id=10):
    chunk = {
        "chunk_id": chunk_id, "text": chunk_text, "score": 0.9,
        "source": {"file_id": 1, "file_name": "a.pdf", "page": 1, "section": "S", "version": 1},
        "retrieval_method": "hybrid",
    }
    context = {
        "blocks": [{"n": 1, "label": "[1] a.pdf", "text": chunk_text, "chunk_ids": [chunk_id],
                    "token_count": 10, "low_quality": False}],
        "citations": [], "dropped_chunk_ids": [],
    }
    return {"query": "Karbon vergisini ilk uygulayan ülke hangisidir?", "draft_answer": draft,
            "citations": citations, "retrieved": [chunk], "context": context, "session_id": "t"}


def _cfg(entailment=True):
    agent = {"validate_coverage_threshold": 0.7, "validate_entailment": entailment}
    # not: alan adı validation_coverage_threshold; kısa tutmak için default'lar da geçerli.
    return load_config(db_reader=lambda: {"agent": {"validate_entailment": entailment}})


_GOOD_CIT = [{"claim": "Finlandiya 1990'da ilk uyguladı.", "chunk_id": 10, "quote": "1990 yılında Finlandiya"}]
_GOOD_DRAFT = "Finlandiya 1990'da ilk uyguladı."


# --- check_entailment birimi --------------------------------------------------
def test_entailment_pass_no_issue():
    j = MockJudge(verdicts=[{"i": 0, "supported": 1, "hypothetical_as_fact": 0}])
    r = check_entailment(question="q", answer="a", citations=_GOOD_CIT, judge=j)
    assert r.issues == [] and not r.skipped and j.calls == 1


def test_entailment_unsupported_claim():
    j = MockJudge(verdicts=[{"i": 0, "supported": 0, "hypothetical_as_fact": 0}])
    r = check_entailment(question="q", answer="a", citations=_GOOD_CIT, judge=j)
    assert r.issues == ["unsupported_claim:10"] and not r.skipped


def test_entailment_overconfident_hypothetical_gs034():
    # gs-v0-034: gerçek ama hipotetik alıntı, kesin olgu gibi sunulmuş.
    j = MockJudge(verdicts=[{"i": 0, "supported": 1, "hypothetical_as_fact": 1}])
    r = check_entailment(question="q", answer="a", citations=_GOOD_CIT, judge=j)
    assert "overconfident_hypothetical:10" in r.issues


def test_entailment_string_verdicts_coerced():
    j = MockJudge(verdicts=[{"i": 0, "supported": "no", "hypothetical_as_fact": "yes"}])
    r = check_entailment(question="q", answer="a", citations=_GOOD_CIT, judge=j)
    assert "unsupported_claim:10" in r.issues and "overconfident_hypothetical:10" in r.issues


def test_entailment_fail_open_on_judge_error():
    j = MockJudge(raise_exc=RuntimeError("judge unreachable"))
    r = check_entailment(question="q", answer="a", citations=_GOOD_CIT, judge=j)
    assert r.skipped is True and r.issues == []  # FAIL-OPEN


def test_entailment_no_citations_no_call():
    j = MockJudge(verdicts=[])
    r = check_entailment(question="q", answer="a", citations=[], judge=j)
    assert r.issues == [] and j.calls == 0  # pair yok → judge çağrılmaz


# --- validate_state entegrasyonu ---------------------------------------------
def test_validate_v2_flags_unsupported_flips_passed():
    j = MockJudge(verdicts=[{"i": 0, "supported": 0, "hypothetical_as_fact": 0}])
    out = validate_state(_state(_GOOD_DRAFT, _GOOD_CIT), config=_cfg(True), judge=j)
    v = out["validation"]
    assert v["passed"] is False and any(i.startswith("unsupported_claim") for i in v["issues"])
    assert j.calls == 1


def test_validate_v2_pass_keeps_passed():
    j = MockJudge(verdicts=[{"i": 0, "supported": 1, "hypothetical_as_fact": 0}])
    out = validate_state(_state(_GOOD_DRAFT, _GOOD_CIT), config=_cfg(True), judge=j)
    assert out["validation"]["passed"] is True


def test_validate_v2_fail_open_keeps_v1_pass():
    j = MockJudge(raise_exc=RuntimeError("down"))
    out = validate_state(_state(_GOOD_DRAFT, _GOOD_CIT), config=_cfg(True), judge=j)
    # judge erişilemez → v1 PASS korunur (sorgu ölmez)
    assert out["validation"]["passed"] is True


def test_validate_v2_off_no_judge_call():
    j = MockJudge(verdicts=[{"i": 0, "supported": 0}])
    out = validate_state(_state(_GOOD_DRAFT, _GOOD_CIT), config=_cfg(False), judge=j)
    assert j.calls == 0 and out["validation"]["passed"] is True


def test_validate_v2_skipped_when_v1_fails():
    # v1 FAIL (uydurma quote, bağlamda yok) → entailment koşmaz.
    bad_cit = [{"claim": "Uydurma.", "chunk_id": 10, "quote": "bağlamda kesinlikle olmayan ifade xyzq"}]
    j = MockJudge(verdicts=[{"i": 0, "supported": 1}])
    out = validate_state(_state("Uydurma.", bad_cit), config=_cfg(True), judge=j)
    assert out["validation"]["passed"] is False and j.calls == 0


# --- feedback yeni issue tipleri ---------------------------------------------
def test_feedback_includes_entailment_hints():
    state = {"validation": {"passed": False, "coverage": 0.9,
                            "issues": ["unsupported_claim:10", "overconfident_hypothetical:10"]}}
    msg = _feedback_message(state)[0]["content"]
    assert "unsupported_claim:10" in msg
    assert "DOĞRUDAN kanıtlıyorsa" in msg          # unsupported_claim yönlendirmesi
    assert "tahmin/projeksiyon" in msg             # overconfident_hypothetical yönlendirmesi

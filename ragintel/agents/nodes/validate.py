"""FAZ 4/5 validate node — v1 deterministik + v2 toplu entailment (opt-in)."""

from __future__ import annotations

from ...config.loader import EffectiveConfig, load_config
from ...guardrails import EntailmentJudge, check_entailment, validate_grounding
from ...observability.logging import get_logger
from ...observability.tracing import set_span_attributes
from ...retrieval.types import RetrievedChunk

_LOG = get_logger("agent.validate")


def _context_chunks(state: dict) -> list[RetrievedChunk]:
    """Citation evreni = context builder'ın sakladığı chunk'lar (LLM'in gördüğü).

    Bütçeyle elenen chunk'lar `retrieved` havuzunda kalır ama evrene girmez.
    `context` yoksa (henüz build edilmemiş) evren boştur — bu durumda tüm
    citation'lar `citation_not_in_context` olur; graph her zaman context sağlar.
    """
    retrieved = state.get("retrieved", [])
    context = state.get("context")
    if not context:
        return []
    kept = {int(cid) for block in context["blocks"] for cid in block["chunk_ids"]}
    return [chunk for chunk in retrieved if int(chunk["chunk_id"]) in kept]


def _run_entailment(state: dict, agent_cfg, judge) -> "object":
    j = judge or EntailmentJudge(model=(str(getattr(agent_cfg, "validate_entailment_model", "")) or None))
    return check_entailment(
        question=str(state.get("query", "")),
        answer=str(state.get("draft_answer") or ""),
        citations=state.get("citations", []) or [],
        judge=j,
    )


def validate_state(state: dict, *, config: EffectiveConfig | None = None, judge=None) -> dict:
    """v1 (deterministik) → PASS ise ve entailment açıksa v2 (toplu LLM entailment).

    v2 yalnızca v1 GEÇTİKTEN sonra ve citation varken koşar (gereksiz judge çağrısı yok).
    Judge erişilemezse FAIL-OPEN: v1 sonucu korunur + span.entailment_skipped=true + WARNING
    (güvenlik katmanı eksilmesi görünür). `judge` enjekte edilebilir (birim testleri)."""
    cfg = config or load_config()
    agent_cfg = cfg.group("agent")
    validation = validate_grounding(
        draft_answer=state.get("draft_answer"),
        citations=state.get("citations", []),
        context_chunks=_context_chunks(state),
        coverage_threshold=float(agent_cfg.validation_coverage_threshold),
        quote_overlap_threshold=float(getattr(agent_cfg, "validation_quote_overlap_threshold", 0.7)),
    )

    entailment_on = bool(getattr(agent_cfg, "validate_entailment", False))
    if validation["passed"] and entailment_on and (state.get("citations") or []):
        ent = _run_entailment(state, agent_cfg, judge)
        set_span_attributes(
            entailment_ran=True,
            entailment_judge=ent.label,
            entailment_skipped=ent.skipped,
            entailment_issue_count=len(ent.issues),
        )
        if ent.skipped:
            # Güvenlik katmanı atlandı — sorgu ölmez ama GÖRÜNÜR (metrik/uyarı).
            _LOG.warning("entailment_guardrail_skipped", judge=ent.label,
                         session_id=state.get("session_id"))
        elif ent.issues:
            _LOG.info("entailment_failed", issues=ent.issues, judge=ent.label)
            validation = {
                **validation,
                "passed": False,
                "issues": list(validation["issues"]) + ent.issues,
            }

    return {**state, "validation": validation}

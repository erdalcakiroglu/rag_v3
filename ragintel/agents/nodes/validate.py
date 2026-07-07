"""FAZ 4 validate node."""

from __future__ import annotations

from ...config.loader import EffectiveConfig, load_config
from ...guardrails import validate_grounding
from ...retrieval.types import RetrievedChunk


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


def validate_state(state: dict, *, config: EffectiveConfig | None = None) -> dict:
    cfg = config or load_config()
    agent_cfg = cfg.group("agent")
    validation = validate_grounding(
        draft_answer=state.get("draft_answer"),
        citations=state.get("citations", []),
        context_chunks=_context_chunks(state),
        coverage_threshold=float(agent_cfg.validation_coverage_threshold),
        quote_overlap_threshold=float(getattr(agent_cfg, "validation_quote_overlap_threshold", 0.7)),
    )
    return {**state, "validation": validation}

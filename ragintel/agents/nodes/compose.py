"""FAZ 4 compose node."""

from __future__ import annotations

import time

from ...agents.state import Citation
from ...config.loader import EffectiveConfig, load_config
from ...guardrails.pii import mask_pii, policy_from_config


def _confidence(state: dict, *, high_threshold: float) -> str:
    validation = state.get("validation") or {"coverage": 0.0}
    coverage = float(validation.get("coverage", 0.0))
    retry_count = int(state.get("retry_count", 0))
    if coverage >= high_threshold and retry_count == 0:
        return "high"
    if coverage > 0.0:
        return "medium"
    return "low"


def _sources(citations: list[Citation], retrieved: list[dict]) -> list[dict]:
    chunk_map = {int(chunk["chunk_id"]): chunk for chunk in retrieved}
    sources = []
    for n, citation in enumerate(citations, start=1):
        chunk = chunk_map.get(int(citation["chunk_id"]))
        if chunk is None:
            continue
        source = chunk["source"]
        sources.append(
            {
                "n": n,
                "file_name": source["file_name"],
                "page": source.get("page"),
                "section": source.get("section"),
                "chunk_id": citation["chunk_id"],
                "quote": citation["quote"],
            }
        )
    return sources


def compose_response(
    state: dict,
    *,
    config: EffectiveConfig | None = None,
    trace_id: str | None = None,
    declined: bool | None = None,
) -> dict:
    """FAZ 5 §5-v2: `sources` YALNIZCA cevabı destekleyen kanıttır. Reddedilen/fallback
    yolunda (`declined`) sources=[] olur ve incelenen-ama-yetersiz chunk'lar
    `meta.reviewed_sources`'a taşınır (citation DEĞİL). `declined=None` ise confidence=low
    reddetme sayılır; `declined=True` (fallback) confidence'ı da low'a zorlar."""
    cfg = config or load_config()
    agent_cfg = cfg.group("agent")
    citations = state.get("citations", [])
    retrieved = state.get("retrieved", [])
    examined = _sources(citations, retrieved)

    confidence = _confidence(state, high_threshold=float(agent_cfg.confidence_high_coverage_threshold))
    is_declined = declined if declined is not None else (confidence == "low")
    if is_declined:
        confidence = "low"

    sources = [] if is_declined else examined
    reviewed = examined if is_declined else []

    # FAZ 6 P2: output PII maskeleme — yanıt VE citation/reviewed quote'ları. İzlenebilir sayaç.
    policy = policy_from_config(cfg)
    answer, pii_count = mask_pii(state.get("draft_answer") or "", policy)
    for src in sources + reviewed:
        if src.get("quote"):
            src["quote"], c = mask_pii(src["quote"], policy)
            pii_count += c

    return {
        "answer": answer,
        "sources": sources,
        "confidence": confidence,
        "followups": [],
        "meta": {
            "iterations": int(state.get("budget", {}).get("iteration", 0)),
            "tokens": int(state.get("budget", {}).get("tokens_used", 0)),
            "latency_ms": 0,
            "model": "",
            "trace_id": trace_id or "",
            "generated_at": int(time.time()),
            "reviewed_sources": reviewed,
            "pii_masked_count": pii_count,
        },
    }

"""FAZ 4 compose node."""

from __future__ import annotations

import time

from ...agents.state import Citation
from ...config.loader import EffectiveConfig, load_config


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


def compose_response(state: dict, *, config: EffectiveConfig | None = None, trace_id: str | None = None) -> dict:
    cfg = config or load_config()
    agent_cfg = cfg.group("agent")
    citations = state.get("citations", [])
    retrieved = state.get("retrieved", [])
    return {
        "answer": state.get("draft_answer") or "",
        "sources": _sources(citations, retrieved),
        "confidence": _confidence(
            state,
            high_threshold=float(agent_cfg.confidence_high_coverage_threshold),
        ),
        "followups": [],
        "meta": {
            "iterations": int(state.get("budget", {}).get("iteration", 0)),
            "tokens": int(state.get("budget", {}).get("tokens_used", 0)),
            "latency_ms": 0,
            "model": "",
            "trace_id": trace_id or "",
            "generated_at": int(time.time()),
        },
    }

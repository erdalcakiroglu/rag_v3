"""FAZ 4 tools node: bekleyen tool çağrılarını yürütür, sonuçları state'e yazar.

- `user_ctx` RUNTIME enjekte edilir (state'ten) — LLM'in verdiği argümanlara EKLENİR.
- Tool exception'ları sarılır: crash yerine LLM'e "tool_error" mesajı döner.
- Chunk döndüren tool'ların çıktısı `retrieved`'e birikimli eklenir (dedup chunk_id).
"""

from __future__ import annotations

import json

from ...observability.tracing import set_span_attributes, start_span
from ..tools import ToolRegistry, accumulated_chunks


def _dedup(chunks: list) -> list:
    best: dict[int, dict] = {}
    order: list[int] = []
    for chunk in chunks:
        cid = int(chunk["chunk_id"])
        if cid not in best:
            order.append(cid)
            best[cid] = chunk
        elif float(chunk.get("score", 0)) > float(best[cid].get("score", 0)):
            best[cid] = chunk
    return [best[cid] for cid in order]


def tools_node(state: dict, *, registry: ToolRegistry) -> dict:
    calls = state.get("pending_tool_calls") or []
    user_ctx = state.get("user_ctx") or {}
    messages: list[dict] = []
    accumulated = list(state.get("retrieved") or [])

    with start_span("agent.tools", tool_calls=",".join(c["name"] for c in calls) or "none"):
        for call in calls:
            name = call["name"]
            try:
                output = registry.execute(name, call.get("arguments") or {}, user_ctx=user_ctx)
            except Exception as exc:  # tool exception → LLM'e mesaj, crash değil
                output = {"error": f"tool_error: {exc}"}
            accumulated.extend(accumulated_chunks(output))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "name": name,
                    "content": json.dumps(output, ensure_ascii=False, default=str),
                }
            )
        deduped = _dedup(accumulated)
        set_span_attributes(retrieved_total=len(deduped))

    return {
        "messages": messages,
        "pending_tool_calls": None,
        "retrieved": deduped,
    }

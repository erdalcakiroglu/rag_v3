"""FAZ 4 tools node: bekleyen tool çağrılarını yürütür, sonuçları state'e yazar.

- `user_ctx` RUNTIME enjekte edilir (state'ten) — LLM'in verdiği argümanlara EKLENİR.
- Tool exception'ları sarılır: crash yerine LLM'e "tool_error" mesajı döner.
- Chunk döndüren tool'ların çıktısı `retrieved`'e birikimli eklenir (dedup chunk_id).
"""

from __future__ import annotations

import json
import time

from ...observability.logging import get_logger
from ...observability.tracing import set_span_attributes, start_span
from ..tools import ToolRegistry, accumulated_chunks

_LOG = get_logger("agent.tools")


def _tool_receipt(output) -> dict | list:
    """Chunk döndüren tool çıktısını kompakt MAKBUZA indir (Tasarım §9b/8).

    Tam chunk metni yalnızca `state.retrieved` → context bloğu yolunda taşınır;
    tool-result mesajına KOYULMAZ (çift-taşıma → prompt şişmesi önlenir). LLM
    içeriği context'te `[n]` etiketleriyle görür. Makbuzdaki `found_chunk_ids`
    GERÇEK chunk_id'lerdir → lookup_document/rerank bunlarla çalışır. Chunk
    döndürmeyen çıktılar (ranking/memory/error) olduğu gibi kalır."""
    if isinstance(output, dict) and isinstance(output.get("chunks"), list):
        ids = [int(c["chunk_id"]) for c in output["chunks"] if "chunk_id" in c]
        return {
            "found_chunk_ids": ids,
            "count": len(ids),
            "note": "İçerik bağlam bloklarında [n] olarak sunuldu; "
                    "lookup_document/rerank için bu chunk_id'leri kullan.",
        }
    return output


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
    conversation_id = state.get("session_id")   # M-14: memory_search'e runtime enjekte (LLM argümanı değil)
    messages: list[dict] = []
    accumulated = list(state.get("retrieved") or [])

    with start_span("agent.tools", tool_calls=",".join(c["name"] for c in calls) or "none"):
        for call in calls:
            name = call["name"]
            t0 = time.perf_counter()
            try:
                output = registry.execute(name, call.get("arguments") or {},
                                          user_ctx=user_ctx, conversation_id=conversation_id)
            except Exception as exc:  # tool exception → LLM'e mesaj, crash değil
                output = {"error": f"tool_error: {exc}"}
            tool_ms = int((time.perf_counter() - t0) * 1000)
            _LOG.info("tool_timing", tool=name, tool_ms=tool_ms)
            # Tam chunk'lar retrieved'e (→ context) gider; tool-result'a MAKBUZ konur.
            accumulated.extend(accumulated_chunks(output))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "name": name,
                    "content": json.dumps(_tool_receipt(output), ensure_ascii=False, default=str),
                }
            )
        deduped = _dedup(accumulated)
        set_span_attributes(retrieved_total=len(deduped))

    return {
        "messages": messages,
        "pending_tool_calls": None,
        "retrieved": deduped,
    }

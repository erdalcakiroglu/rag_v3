"""FAZ 4 agent node: tek LLM karar noktası (tool çağır VEYA yanıt teslim et).

- Her turda `retrieved`'den context builder ile bağlam blokları üretilir ve
  prompt'a konur (validate'in evreni = bu saklanan bağlam — İP-3.4 köprüsü).
- `user_ctx` LLM'e VERİLMEZ (tool şemalarında yok, runtime enjekte edilir).
- Bütçe bitince (iterasyon/token) yalnızca `submit_answer` sunulur → yanıt zorlanır.
- Zaman aşımı: LLM çağrılmaz; router fallback'e yönlendirir.
"""

from __future__ import annotations

import json
import time

from ...config.loader import EffectiveConfig, load_config
from ...observability.tracing import set_span_attributes, start_span
from ...retrieval.types import ContextBuildResult
from ..prompts import load_system_prompt
from ..tools import SUBMIT_ANSWER, ToolRegistry, accumulated_chunks  # noqa: F401 (accumulated_chunks tools_node'da)

_FEEDBACK_HEADER = "VALIDATION_FAILED:"


def _normalize_citations(raw) -> list[dict]:
    """LLM'in ürettiği citation listesini kanonik biçime indirger; bozukları atar.
    Gerçek (küçük) modeller str/eksik-alan üretebilir — pipeline crash etmemeli."""
    out: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict) or "chunk_id" not in item:
            continue
        try:
            cid = int(item["chunk_id"])
        except (TypeError, ValueError):
            continue
        out.append({"claim": str(item.get("claim", "")), "chunk_id": cid, "quote": str(item.get("quote", ""))})
    return out


def _render_context(context: ContextBuildResult | None) -> str:
    if not context or not context["blocks"]:
        return "(henüz bağlam yok — önce arama yapın)"
    lines = []
    for block in context["blocks"]:
        flag = " [DÜŞÜK KALİTE]" if block.get("low_quality") else ""
        lines.append(f"{block['label']}{flag}\n{block['text']}")
    return "\n\n".join(lines)


def _feedback_message(state: dict) -> list[dict]:
    validation = state.get("validation")
    if not validation or validation.get("passed"):
        return []
    issues = "\n".join(f"- {issue}" for issue in validation.get("issues", []))
    content = (
        f"{_FEEDBACK_HEADER}\n{issues}\n"
        "Talimat: Yalnızca sağlanan bağlamdaki bilgiyle yanıtla; desteklenmeyen "
        "iddiaları çıkar veya ek arama yap."
    )
    return [{"role": "user", "content": content}]


def _assemble_messages(state: dict, cfg: EffectiveConfig, context: ContextBuildResult) -> list[dict]:
    budget = state["budget"]
    remaining = int(budget["max_iterations"]) - int(budget["iteration"])
    system = load_system_prompt(cfg) + f"\n\n[Kalan iterasyon: {remaining}]"
    head = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Soru: {state['query']}\n\nBağlam blokları:\n{_render_context(context)}"},
    ]
    tail = list(state.get("messages") or [])
    return head + tail + _feedback_message(state)


def agent_node(
    state: dict,
    *,
    gateway,
    registry: ToolRegistry,
    context_builder,
    config: EffectiveConfig | None = None,
) -> dict:
    cfg = config or load_config()
    budget = dict(state["budget"])

    with start_span("agent.step", iteration=int(budget["iteration"])) as span:
        # Zaman aşımı: yeni LLM çağrısı yapma; router deadline'ı görüp fallback'e gider.
        if budget["deadline_ts"] and time.time() > float(budget["deadline_ts"]):
            set_span_attributes(decision="timeout")
            return {"pending_tool_calls": None}

        # Bu tur bir retry mi? (başarısız validation state'te duruyorsa)
        validation = state.get("validation")
        is_retry = bool(validation) and not validation.get("passed", False)

        context = context_builder.build(state.get("retrieved") or [])
        messages = _assemble_messages(state, cfg, context)

        exhausted = (
            int(budget["iteration"]) >= int(budget["max_iterations"])
            or int(budget["tokens_used"]) >= int(budget["max_tokens"])
        )
        tools = registry.final_only_schemas() if exhausted else registry.llm_tool_schemas()

        resp = gateway.complete(messages=messages, tools=tools)
        budget["iteration"] = int(budget["iteration"]) + 1
        budget["tokens_used"] = int(budget["tokens_used"]) + int(resp.total_tokens)
        set_span_attributes(
            is_retry=is_retry,
            forced_final=exhausted,
            tokens_used=int(budget["tokens_used"]),
            tool_calls=",".join(tc.name for tc in resp.tool_calls) or "none",
        )

        update: dict = {
            "budget": budget,
            "context": context,
            "retry_count": int(state.get("retry_count", 0)) + (1 if is_retry else 0),
        }
        # Retry turuna girildi: eski başarısız validation temizlenir ki bu turdaki
        # ek tool çağrıları retry_count'u tekrar artırmasın (retry başına 1 artış).
        if is_retry:
            update["validation"] = None

        submit = next((tc for tc in resp.tool_calls if tc.name == SUBMIT_ANSWER), None)
        if submit is not None:
            set_span_attributes(decision="submit_answer")
            update["draft_answer"] = str(submit.arguments.get("answer") or "")
            update["citations"] = _normalize_citations(submit.arguments.get("citations"))
            update["pending_tool_calls"] = None
            return update

        if resp.tool_calls:
            set_span_attributes(decision="tool_call")
            update["messages"] = [resp.raw_message]
            update["draft_answer"] = None  # stale taslağı temizle → router tools'a yönlensin
            update["pending_tool_calls"] = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in resp.tool_calls
            ]
            return update

        # Ne tool ne submit: içeriği citation'sız taslak say (validate düşük coverage'la eler).
        set_span_attributes(decision="content_no_tool")
        update["draft_answer"] = str(resp.content or "")
        update["citations"] = []
        update["pending_tool_calls"] = None
        return update

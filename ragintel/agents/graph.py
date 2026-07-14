"""FAZ 4 agentic loop grafı (Tasarim_FAZ4 §1).

Topoloji:  START → prepare → agent ⇄ tools → validate → compose/fallback → END
- validate PASS → compose; FAIL + retry hakkı → agent (feedback ile, retry=1);
  FAIL + hak yok/bütçe yok → fallback.
- Checkpointing: PostgresSaver, thread_id = session_id (config'te DDL elle uygulanır,
  otomatik .setup() ÇAĞRILMAZ — bkz. docs/FAZ4_Sema.sql).
"""

from __future__ import annotations

import time
import uuid
from functools import partial

from langgraph.graph import END, START, StateGraph

from ..config.loader import EffectiveConfig, load_config
from ..llm.gateway import LLMGateway
from ..observability.tracing import set_span_attributes, start_span
from .nodes.agent import agent_node
from .nodes.compose import compose_response
from .nodes.fallback import fallback_response
from .nodes.prepare import prepare_state
from .nodes.tools_node import tools_node
from .nodes.validate import validate_state
from .state import AgentState
from .tools import ToolRegistry


# --- Node wrapper'ları (partial-update döndürür) ------------------------------
def _validate_node(state: dict, *, config: EffectiveConfig) -> dict:
    with start_span("agent.validate"):
        validation = validate_state(state, config=config)["validation"]
        set_span_attributes(passed=validation["passed"], coverage=float(validation["coverage"]))
    return {"validation": validation}


def _compose_node(state: dict, *, config: EffectiveConfig) -> dict:
    with start_span("agent.compose"):
        response = compose_response(state, config=config)
        set_span_attributes(
            confidence=response["confidence"], source_count=len(response["sources"]),
            pii_masked_count=int(response["meta"].get("pii_masked_count", 0)),
        )
    return {"final_response": response}


def _fallback_node(state: dict, *, config: EffectiveConfig) -> dict:
    with start_span("agent.fallback"):
        response = fallback_response(state, config=config)
    return {"final_response": response}


# --- Yönlendirme (conditional edges) ------------------------------------------
def route_after_agent(state: dict) -> str:
    budget = state.get("budget") or {}
    deadline = budget.get("deadline_ts")
    if deadline and time.time() > float(deadline):
        return "fallback"
    if state.get("draft_answer") is not None:
        return "validate"
    if state.get("pending_tool_calls"):
        return "tools"
    return "validate"


def route_after_validate(state: dict) -> str:
    """M-9 (Tasarim §9b, implementasyon-revizyonu): doğrulama-retry'si TOOL bütçesinden
    AYRIDIR — tam 1 hak.

    Eskiden `iteration >= max_iterations` da "tükenmiş" sayılıyordu. Tool turları (arama +
    lookup + submit) bütçeyi zaten bitirdiği için validate FAIL ettiğinde retry hakkı
    pratikte HİÇ ateşlenmiyordu: tasarımın vaat ettiği düzeltme turu erişilemezdi
    (ölçüldü: fallback'lerin 4/6'sı retry'yi hiç görmeden düştü). Kalite mekanizmasının
    tool bütçesiyle aynı kasadan yemesi bir sözleşme ihlaliydi.

    SERT durdurucular yerinde: token bütçesi ve deadline. Retry sayısını `retry_count < 1`
    sınırlar (sonsuz döngü imkânsız). Retry turunda agent_node zaten `final_only_schemas`
    verir (iteration tükenmiş) — yani model yalnızca submit_answer'a gidebilir: yeni arama
    yapamaz, sadece cevabını ALINTILAYARAK düzeltir. İstenen davranış tam olarak budur.
    """
    validation = state.get("validation") or {}
    if validation.get("passed"):
        return "compose"
    budget = state.get("budget") or {}
    deadline = budget.get("deadline_ts")
    hard_stop = (
        int(budget.get("tokens_used", 0)) >= int(budget.get("max_tokens", 1 << 30))
        or bool(deadline and time.time() > float(deadline))
    )
    if int(state.get("retry_count", 0)) < 1 and not hard_stop:
        return "agent"
    return "fallback"


def build_agent_graph(
    *,
    gateway: LLMGateway,
    context_builder,
    service=None,
    registry: ToolRegistry | None = None,
    config: EffectiveConfig | None = None,
    checkpointer=None,
    interrupt_before: list[str] | None = None,
):
    """Grafı kurar ve derler.

    - `gateway`: LLM gateway (LiteLLMGateway veya test mock'u).
    - `context_builder`: `.build(retrieved) -> ContextBuildResult` (İP-3.4 ContextBuilder).
    - `service`/`registry`: retrieval tool'ları; `registry` verilmezse `service`'ten kurulur.
    - `checkpointer`: PostgresSaver/InMemorySaver; None → checkpoint yok.
    """
    cfg = config or load_config()
    registry = registry or ToolRegistry(service)

    graph = StateGraph(AgentState)
    graph.add_node("prepare", partial(prepare_state, config=cfg))
    graph.add_node(
        "agent",
        partial(agent_node, gateway=gateway, registry=registry, context_builder=context_builder, config=cfg),
    )
    graph.add_node("tools", partial(tools_node, registry=registry))
    graph.add_node("validate", partial(_validate_node, config=cfg))
    graph.add_node("compose", partial(_compose_node, config=cfg))
    graph.add_node("fallback", partial(_fallback_node, config=cfg))

    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "agent")
    graph.add_conditional_edges(
        "agent", route_after_agent, {"tools": "tools", "validate": "validate", "fallback": "fallback"}
    )
    graph.add_edge("tools", "agent")
    graph.add_conditional_edges(
        "validate", route_after_validate, {"compose": "compose", "agent": "agent", "fallback": "fallback"}
    )
    graph.add_edge("compose", END)
    graph.add_edge("fallback", END)

    return graph.compile(checkpointer=checkpointer, interrupt_before=interrupt_before or None)


def run_agent(app, initial: dict, *, config: dict | None = None) -> dict:
    """Grafı tek bir kök span ('agent.run') altında koşturur.

    Node span'leri (prepare/agent.step/tools/validate/compose/fallback) bu kökün
    altında nest eder → Langfuse'ta tam trace ağacı. `config` LangGraph thread
    config'idir (checkpoint için {"configurable": {"thread_id": ...}}).
    """
    with start_span("agent.run", session_id=str(initial.get("session_id", ""))) as span:
        ctx = span.get_span_context()
        # OTel provider yoksa (Langfuse kapalı) span context geçersiz olur; yine de
        # korelasyon/feedback için boş olmayan bir trace_id garanti et.
        trace_id = f"{ctx.trace_id:032x}" if ctx.is_valid else uuid.uuid4().hex
        set_span_attributes(query=str(initial.get("query", "")))
        out = app.invoke(initial, config) if config else app.invoke(initial)
        final = out.get("final_response") or {}
        set_span_attributes(
            confidence=final.get("confidence", ""),
            validation_passed=bool((out.get("validation") or {}).get("passed", False)),
        )
        if isinstance(final.get("meta"), dict) and not final["meta"].get("trace_id"):
            final["meta"]["trace_id"] = trace_id
        return out

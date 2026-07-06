"""FAZ 4 state şeması kontratı."""

from __future__ import annotations

import operator
import typing

from ragintel.agents.state import AgentState, Budget, Citation, ValidationResult


def test_state_symbols_exist():
    assert AgentState
    assert Budget
    assert Citation
    assert ValidationResult


def test_agent_state_has_bridge_and_control_fields():
    hints = AgentState.__annotations__
    for field in ("query", "user_ctx", "messages", "retrieved", "context", "pending_tool_calls",
                  "draft_answer", "citations", "validation", "budget", "retry_count", "final_response"):
        assert field in hints, f"eksik alan: {field}"


def test_messages_reducer_is_list_concatenation():
    # messages kanalı LiteLLM biçimli dict tutar; reducer = operator.add (coercion yok).
    meta = typing.get_type_hints(AgentState, include_extras=True)["messages"]
    reducers = getattr(meta, "__metadata__", ())
    assert operator.add in reducers

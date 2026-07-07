"""FAZ 4 state şeması kontratı."""

from __future__ import annotations

import typing

from ragintel.agents.state import AgentState, Budget, Citation, ValidationResult, merge_messages


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


def test_messages_reducer_concatenates_and_resets():
    # messages kanalı LiteLLM biçimli dict tutar; reducer = merge_messages (coercion yok).
    meta = typing.get_type_hints(AgentState, include_extras=True)["messages"]
    reducers = getattr(meta, "__metadata__", ())
    assert merge_messages in reducers
    # birleştirme (operator.add gibi) + tur sıfırlama (right=None → []).
    assert merge_messages([{"role": "user"}], [{"role": "assistant"}]) == [{"role": "user"}, {"role": "assistant"}]
    assert merge_messages([{"role": "user"}], None) == []          # yeni tur: scratchpad reset
    assert merge_messages(None, [{"role": "system"}]) == [{"role": "system"}]

"""FAZ 4 tool şema kontratı: user_ctx LLM'e AÇILMAZ, runtime enjekte edilir."""

from __future__ import annotations

import json

from ragintel.agents.tools import SUBMIT_ANSWER, ToolRegistry


def _all_schema_text(schemas) -> str:
    return json.dumps(schemas, ensure_ascii=False).lower()


def test_user_ctx_and_scopes_absent_from_llm_schemas():
    reg = ToolRegistry(service=None)
    text = _all_schema_text(reg.llm_tool_schemas())
    assert "user_ctx" not in text
    assert "allowed_doc_scopes" not in text
    assert "tenant_id" not in text


def test_final_only_schemas_expose_just_submit_answer():
    reg = ToolRegistry(service=None)
    finals = reg.final_only_schemas()
    assert [s["function"]["name"] for s in finals] == [SUBMIT_ANSWER]


def test_registry_offers_retrieval_tools_plus_submit():
    reg = ToolRegistry(service=None)
    names = [s["function"]["name"] for s in reg.llm_tool_schemas()]
    assert "search_hybrid" in names
    assert "lookup_document" in names
    assert SUBMIT_ANSWER in names


def test_submit_answer_is_terminal_not_executable():
    reg = ToolRegistry(service=None)
    assert SUBMIT_ANSWER not in reg.executable_names()
    try:
        reg.execute(SUBMIT_ANSWER, {}, user_ctx={})
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_unknown_tool_returns_tool_error_not_crash():
    reg = ToolRegistry(service=None)
    out = reg.execute("nonexistent", {}, user_ctx={})
    assert "tool_error" in out["error"]

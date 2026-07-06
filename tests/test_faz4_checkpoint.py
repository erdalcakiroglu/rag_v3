"""FAZ 4 checkpoint kesinti/devam testleri.

- InMemorySaver: mekanizmayı DB'siz, deterministik doğrular (varsayılan koşu).
- PostgresSaver: gerçek serileştirme/devam (@db; DB yoksa atlanır). thread_id=session_id.
"""

from __future__ import annotations

import pytest

from ragintel.agents.graph import build_agent_graph
from ragintel.agents.tools import ToolRegistry

from tests.test_faz4_graph_flow import (
    _CHUNK,
    _GOOD_ANSWER,
    _GOOD_CITATION,
    FakeContextBuilder,
    FakeService,
    ScriptedGateway,
    _cfg,
    _initial,
)


def _script():
    return [("tool", "search_hybrid", {"query": "karbon"}), ("submit", _GOOD_ANSWER, _GOOD_CITATION)]


def test_in_memory_saver_interrupt_before_tools_then_resume():
    from langgraph.checkpoint.memory import InMemorySaver

    gw = ScriptedGateway(_script())
    app = build_agent_graph(
        gateway=gw,
        context_builder=FakeContextBuilder(),
        registry=ToolRegistry(FakeService([_CHUNK])),
        config=_cfg(),
        checkpointer=InMemorySaver(),
        interrupt_before=["tools"],
    )
    cfg = {"configurable": {"thread_id": "sess-1"}}

    first = app.invoke(_initial(), cfg)
    assert first.get("final_response") is None          # tools öncesi kesildi
    snapshot = app.get_state(cfg)
    assert snapshot.next == ("tools",)                   # devam noktası tools
    assert len(gw.calls) == 1                            # yalnızca ilk agent turu koştu

    resumed = app.invoke(None, cfg)                      # aynı thread'den devam
    assert resumed["final_response"]["answer"] == _GOOD_ANSWER
    assert resumed["validation"]["passed"] is True
    assert len(gw.calls) == 2


@pytest.mark.db
def test_postgres_saver_interrupt_resume(live_db):
    """Gerçek PostgresSaver serileştirme/devam. İZOLE geçici şema kullanır —
    ragintel'in checkpoint tablolarına DOKUNMAZ (manuel-DDL disiplini korunur;
    üretimde docs/FAZ4_Sema.sql elle uygulanır). Şema test sonunda düşürülür."""
    from langgraph.checkpoint.postgres import PostgresSaver

    from ragintel.config.settings import DbSettings

    settings = DbSettings()
    schema = "faz4_ckpt_test"
    with live_db.connection() as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.execute(f"CREATE SCHEMA {schema}")
    # search_path'i geçici şemaya yönlendir (ragintel dışı).
    conninfo = (
        f"host={settings.host} port={settings.port} dbname={settings.name} "
        f"user={settings.user} password={settings.password} "
        f"options=-csearch_path={schema}"
    )
    try:
        with PostgresSaver.from_conn_string(conninfo) as saver:
            saver.setup()  # tablolar geçici şemada
            gw = ScriptedGateway(_script())
            app = build_agent_graph(
                gateway=gw,
                context_builder=FakeContextBuilder(),
                registry=ToolRegistry(FakeService([_CHUNK])),
                config=_cfg(),
                checkpointer=saver,
                interrupt_before=["tools"],
            )
            cfg = {"configurable": {"thread_id": "sess-1"}}

            first = app.invoke(_initial(), cfg)
            assert first.get("final_response") is None
            assert app.get_state(cfg).next == ("tools",)

            resumed = app.invoke(None, cfg)
            assert resumed["final_response"]["answer"] == _GOOD_ANSWER
            assert resumed["validation"]["passed"] is True
    finally:
        with live_db.connection() as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")

"""FAZ 4 prepare node: bütçe/çalışma alanı ilklendirme, permission context.

`user_ctx` girdiden gelir (API/oturum katmanı doldurur) ve buradan sonra
SALT-OKUNUR'dur; tool'lara runtime enjekte edilir, LLM parametresi değildir.
Session memory MVP'de stub (memory_search tool'u üzerinden).
"""

from __future__ import annotations

import time

from ...config.loader import EffectiveConfig, load_config
from ...observability.tracing import start_span


def prepare_state(state: dict, *, config: EffectiveConfig | None = None) -> dict:
    cfg = config or load_config()
    agent_cfg = cfg.group("agent")
    with start_span("agent.prepare", session_id=str(state.get("session_id", ""))):
        budget = {
            "iteration": 0,
            "max_iterations": int(agent_cfg.max_iterations),
            "tokens_used": 0,
            "max_tokens": int(agent_cfg.max_tokens),
            "deadline_ts": time.time() + float(agent_cfg.timeout_sec),
        }
    # Sadece delta döndürülür (messages reducer=operator.add; tam state dönersek çiftlenir).
    return {
        "retrieved": list(state.get("retrieved") or []),
        "context": None,
        "pending_tool_calls": None,
        "draft_answer": None,
        "citations": [],
        "validation": None,
        "budget": budget,
        "retry_count": 0,
        "final_response": None,
    }

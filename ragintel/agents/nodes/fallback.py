"""FAZ 4 fallback node."""

from __future__ import annotations

from ...config.loader import EffectiveConfig
from .compose import compose_response


def fallback_response(state: dict, *, config: EffectiveConfig | None = None, trace_id: str | None = None) -> dict:
    updated = {
        **state,
        "draft_answer": "Güvenilir yanıt üretilemedi. Bulunan kaynaklar aşağıdadır.",
    }
    resp = compose_response(updated, config=config, trace_id=trace_id)
    resp["confidence"] = "low"
    return resp

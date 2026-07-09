"""FAZ 4 fallback node."""

from __future__ import annotations

from ...config.loader import EffectiveConfig
from .compose import compose_response


def fallback_response(state: dict, *, config: EffectiveConfig | None = None, trace_id: str | None = None) -> dict:
    # FAZ 5: reddetme yolu → cevap bulunamadı. sources=[] (uydurma yok); incelenen chunk'lar
    # meta.reviewed_sources'a gider (compose declined=True ile ayırır).
    updated = {**state, "draft_answer": "Cevap bulunamadı. İncelenen kaynaklar aşağıdadır."}
    return compose_response(updated, config=config, trace_id=trace_id, declined=True)

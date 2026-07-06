"""ADR-014 canlı TEI rerank smoke."""

from __future__ import annotations

import pytest

from ragintel.config.settings import TeiSettings

pytestmark = pytest.mark.slow


def test_live_tei_rerank_changes_order():
    import httpx

    settings = TeiSettings()
    texts = [
        "Bu metin futbol transfer haberleri ile ilgilidir.",
        "Karbon vergisi, emisyon maliyetini fiyatlayarak iklim politikasina katkı sağlar.",
    ]
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                f"{settings.rerank_url.rstrip('/')}/rerank",
                json={"query": "karbon vergisi nedir", "texts": texts},
            )
            resp.raise_for_status()
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException) as exc:
        pytest.skip(f"TEI erişilemez ({settings.rerank_url}): {exc}")

    payload = resp.json()
    results = payload if isinstance(payload, list) else payload.get("results")
    assert isinstance(results, list) and len(results) == 2
    ranked = sorted(results, key=lambda item: float(item["score"]), reverse=True)
    assert int(ranked[0]["index"]) == 1

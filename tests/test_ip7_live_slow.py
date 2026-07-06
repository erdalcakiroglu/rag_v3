"""İP-7 canlı Ollama smoke — endpoint erişilebilirse (aksi halde skip).

`pytest -m slow`. Erişilebilir bir ağdan (VPN/firewall açık) çalıştırılmalı;
bu dev ortamından 10.50.130.55:11434 TCP erişilemiyor (skip).
"""

from __future__ import annotations

import math

import pytest

from ragintel.config.settings import OllamaSettings
from ragintel.ingestion.embedding import OllamaEmbedder

pytestmark = pytest.mark.slow


def test_live_ollama_embed():
    import httpx

    s = OllamaSettings()
    emb = OllamaEmbedder(s.base_url, model=s.model, timeout=10)
    texts = ["Karbon vergisi nedir?", "What is a carbon tax?", "İklim kanunu 2026"]
    try:
        vecs = emb.embed_batch(texts)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException) as exc:
        pytest.skip(f"Ollama erişilemez ({s.base_url}): {exc}")

    assert len(vecs) == 3
    for v in vecs:
        assert len(v) == 1024
        assert abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 1e-5   # client L2-normalize

"""İP-7 testleri için httpx.MockTransport tabanlı sahte Ollama endpoint'i."""

from __future__ import annotations

import json
import random

import httpx

DIM = 1024


def _vec(seed_text: str, *, normalized: bool = False) -> list[float]:
    r = random.Random(hash(seed_text) % 100000)
    v = [r.uniform(-1, 1) for _ in range(DIM)]
    if normalized:
        import math
        n = math.sqrt(sum(x * x for x in v))
        v = [x / n for x in v]
    return v


def make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mock")


def ok_handler(stats: dict | None = None, *, bad_texts=(), identical=False):
    """200 döner; `bad_texts` için NaN vektör; `identical`=True tüm vektörler aynı."""
    def handler(request):
        if stats is not None:
            stats["calls"] = stats.get("calls", 0) + 1
        body = json.loads(request.content)
        texts = body["input"]
        embs = []
        for t in texts:
            if t in bad_texts:
                embs.append([0.0] * DIM)          # sıfır-norm (JSON-uyumlu bozuk)
            elif identical:
                embs.append(_vec("SAME"))
            else:
                embs.append(_vec(t))
        return httpx.Response(200, json={"model": "bge-m3", "embeddings": embs})
    return handler


def flaky_handler(fail_times: int, status: int = 503, stats: dict | None = None):
    """İlk `fail_times` çağrıda `status`, sonra 200."""
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if stats is not None:
            stats["calls"] = stats.get("calls", 0) + 1
        if state["n"] <= fail_times:
            return httpx.Response(status, json={"error": "server busy"})
        body = json.loads(request.content)
        embs = [_vec(t) for t in body["input"]]
        return httpx.Response(200, json={"model": "bge-m3", "embeddings": embs})
    return handler


def size_gated_handler(ok_max_size: int, status: int = 503):
    """Batch boyutu > ok_max_size ise `status`, aksi halde 200 (halving testi)."""
    def handler(request):
        body = json.loads(request.content)
        texts = body["input"]
        if len(texts) > ok_max_size:
            return httpx.Response(status, json={"error": "too large"})
        embs = [_vec(t) for t in texts]
        return httpx.Response(200, json={"model": "bge-m3", "embeddings": embs})
    return handler


def poison_handler(marker: str = "|", status: int = 500, stats: dict | None = None):
    """İçinde `marker` GEÇEN herhangi bir metin varsa `status` (deterministik);
    aksi halde 200. Uzak embed ucunun pipe-ayraçlı flatten-tablo token dizisinde
    verdiği içerik-tetikli 500'ü taklit eder (sanitize-fallback testi)."""
    def handler(request):
        if stats is not None:
            stats["calls"] = stats.get("calls", 0) + 1
        body = json.loads(request.content)
        texts = body["input"]
        if any(marker in t for t in texts):
            return httpx.Response(status, json={"error": "poison token"})
        embs = [_vec(t) for t in texts]
        return httpx.Response(200, json={"model": "bge-m3", "embeddings": embs})
    return handler


def unreachable_handler(exc=None):
    """Bağlantı hatası (erişilemezlik) simülasyonu."""
    def handler(request):
        raise (exc or httpx.ConnectError("connection refused"))
    return handler


def timeout_handler():
    def handler(request):
        raise httpx.ReadTimeout("read timed out")
    return handler

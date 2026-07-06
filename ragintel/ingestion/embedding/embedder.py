"""Embedding backend adaptörü (İP-7 / ADR-012): remote Ollama HTTP.

`embed_batch(texts) -> list[vector]` arayüzü backend-bağımsızdır; vLLM/TEI/
FlagEmbedding'e geçiş bu imzanın arkasında kalır. Ollama çıktısının normalize
olup olmadığına GÜVENİLMEZ — L2-normalize İSTEMCİ tarafında yapılır.

Damga: üretilen tüm vektörler `bge-m3@ollama` model_name'iyle işaretlenir
(tek korpus = tek backend; FAZ 3 sorgu embedding'i de aynı backend).
"""

from __future__ import annotations

import math
from typing import Protocol

MODEL_STAMP = "bge-m3@ollama"


class EmbeddingBackendError(RuntimeError):
    """Embedding ALTYAPI hatası (erişilemezlik/kalıcı backend hatası).

    Dosya hatası DEĞİLDİR: pipeline durur, dosya FAILED işaretlenmez (İP-10
    RETRY akışıyla yeniden denenir).
    """


def l2_normalize(vec: list[float]) -> list[float]:
    """Vektörü birim norma indirger. Norm 0/NaN ise olduğu gibi bırakır
    (QC sıfır-norm/NaN olarak yakalar)."""
    n = math.sqrt(sum(x * x for x in vec))
    if n == 0.0 or not math.isfinite(n):
        return list(vec)
    return [x / n for x in vec]


class Embedder(Protocol):
    model_name: str
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    """Ollama /api/embed backend'i. Ham HTTP çağrısı; retry/batch-halving
    SERVİS katmanındadır (bu sınıf tek isteği yapar)."""

    model_name = MODEL_STAMP

    def __init__(self, base_url: str, *, model: str = "bge-m3",
                 timeout: float = 30.0, client=None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import httpx
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Tek HTTP isteği: /api/embed. Dense çıktı, istemci tarafı L2-normalize.

        httpx istisnaları (Timeout/HTTPStatusError/ConnectError) YUKARI atılır;
        servis bunları retry/batch-halving/erişilemezlik olarak sınıflandırır.
        """
        if not texts:
            return []
        resp = self.client.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": texts},
        )
        resp.raise_for_status()   # 4xx/5xx -> httpx.HTTPStatusError
        data = resp.json()

        embs = data.get("embeddings")
        if embs is None and "embedding" in data:   # tekil yanıt toleransı
            embs = [data["embedding"]]
        if embs is None or len(embs) != len(texts):
            raise EmbeddingBackendError(
                f"Ollama yanıtı geçersiz: beklenen {len(texts)} embedding, "
                f"gelen {0 if embs is None else len(embs)}"
            )
        return [l2_normalize(v) for v in embs]

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

def ollama_tag(model: str) -> str:
    """HF repo id → Ollama model etiketi (`BAAI/bge-m3` → `bge-m3`).

    Tek otorite `embedding.model` HF repo id'sidir (tokenizer `AutoTokenizer`
    için ZORUNLU). Ollama `/api/embed` ise etiket ister; ikisi tek alandan türer.
    """
    return model.rsplit("/", 1)[-1]


def model_stamp(model: str) -> str:
    """`core_vectors.model_name` köken damgası: `bge-m3@ollama`.

    Damga ETİKETTEN türer (repo id'den değil) — böylece M-4 öncesi yazılmış korpusla
    birebir uyumludur ve backfill gerekmez.
    """
    return f"{ollama_tag(model)}@ollama"


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
    SERVİS katmanındadır (bu sınıf tek isteği yapar).

    `model` = `embedding.model` (HF repo id, TEK OTORİTE). Ollama etiketi ve
    köken damgası buradan türer — sınıf sabiti YOK, yani model değişince damga da
    değişir (eskiden `model_name` sabitti ve köken bilgisi yanlış olabiliyordu).
    """

    def __init__(self, base_url: str, *, model: str = "BAAI/bge-m3",
                 timeout: float = 30.0, client=None):
        if not base_url:
            raise EmbeddingBackendError("Ollama base_url tanımsız (RAGINTEL_OLLAMA_BASE_URL)")
        self.base_url = base_url.rstrip("/")
        self.hf_model = model
        self.model = ollama_tag(model)        # /api/embed'e giden etiket
        self.model_name = model_stamp(model)  # core_vectors.model_name damgası
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

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


def ollama_wire_tag(model: str) -> str:
    """M-9: İSTEĞE giden etiket (`BAAI/bge-m3` → `bge-m3:latest`).

    KİMLİK DAMGASINDAN AYRIDIR — bilerek. Open WebUI (H200 proxy'si) model adını kendi
    kaydına karşı BİREBİR doğrular: `bge-m3` → 400 "not found", `bge-m3:latest` → 200.
    (Doğrudan Ollama etiketsiz adı çözer; katı olan proxy katmanıdır.)

    Etiket bir SUNUM detayıdır, model KİMLİĞİ değildir: damga (`model_stamp`) bundan
    ETKİLENMEZ. Aksi hâlde damga `bge-m3:latest@ollama` olur ve korpustaki 1478 vektörle
    uyum kırılırdı (`assert_corpus_model` patlar, gereksiz reprocess dayatılırdı).
    Model adında etiket zaten varsa (ör. `qwen3.5:35b`) dokunulmaz.
    """
    tag = ollama_tag(model)
    return tag if ":" in tag else f"{tag}:latest"


def model_stamp(model: str) -> str:
    """`core_vectors.model_name` köken damgası: `bge-m3@ollama`.

    Damga ETİKETTEN türer (repo id'den değil) — böylece M-4 öncesi yazılmış korpusla
    birebir uyumludur ve backfill gerekmez.

    M-9: sondaki `:latest` damgaya GİRMEZ. Gerekçe: `:latest` bir SÜRÜM değil,
    "varsayılan etiket" takma adıdır — model kimliğinin parçası değildir. Aksi hâlde
    config'e `bge-m3:latest` yazan biri damgayı `bge-m3:latest@ollama`'ya kaydırır ve
    korpustaki 1478 vektör sessizce "yabancı model" sayılır (assert_corpus_model patlar,
    gereksiz reprocess dayatılır).
    DİKKAT: `:latest` DIŞINDAKİ etiketler damgada KALIR (`bge-m3:v2` GERÇEKTEN başka bir
    modeldir; onu `bge-m3` ile aynı damgaya indirmek iki farklı vektör uzayını karıştırırdı).
    """
    tag = ollama_tag(model)
    if tag.endswith(":latest"):
        tag = tag[: -len(":latest")]
    return f"{tag}@ollama"


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


def sanitize_for_embed(text: str) -> str:
    """Deterministik embed-500 için SON ÇARE sanitizasyonu (yalnız fallback yolunda).

    Uzak embed ucu (H200 Open WebUI/Ollama proxy'si) belirli PIPE-ayraçlı flatten-
    tablo token dizilerinde deterministik HTTP 500 veriyor (ölçüldü 2026-08-05:
    "5411 sayılı Bankacılık Kanunu.pdf" chunk#266 `POZİSYON UNVANI | ADEDİ\n…`;
    3/3 500, yük değil, kodumuz değil — dar bir uzak-sunucu bug'ı). Ayracı ('|')
    boşlukla değiştirmek 500'ü gideriyor (asciifi/NFC/pipe→tab denendi; etkili
    olan tek dönüşüm bu). Satır sonları KORUNUR (tablo satır yapısı; zehir yalnız
    pipe token'ı). SADECE tek-chunk kalıcı 5xx'te ve metin GERÇEKTEN değişiyorsa
    çağrılır; değişmiyorsa fallback anlamsızdır → gerçek altyapı arızası yükseltilir.

    Saklanan `chunk_text` bu dönüşümden ETKİLENMEZ — yalnız gönderilen embed payload'ı
    temizlenir; sapma `qc_findings('embed_sanitized')` ile şeffafça işaretlenir.
    """
    return text.replace("|", " ")


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
                 timeout: float = 30.0, client=None, api_key: str = ""):
        if not base_url:
            raise EmbeddingBackendError("Ollama base_url tanımsız (RAGINTEL_OLLAMA_BASE_URL)")
        if not model:
            # SESSİZ KESME KAPISI: boş model `ollama_wire_tag("")` üzerinden `":latest"`
            # olur ve /api/embed 500'ü ~575µs'de döner — kaynak sorunu gibi okunur.
            # Ölçüldü (2026-08-14): eval yolunda TÜM RAGAS metriklerini sessizce
            # sıfırlıyordu. Boş model artık çağrı ANINDA patlar.
            raise EmbeddingBackendError(
                "embedding modeli boş — `embedding.model` (DB otoritesi) ya da "
                "RAGINTEL_OLLAMA_MODEL verilmeli"
            )
        self.base_url = base_url.rstrip("/")
        self.hf_model = model
        self.model = ollama_wire_tag(model)   # M-9: /api/embed'e giden etiket (`bge-m3:latest`)
        self.model_name = model_stamp(model)  # core_vectors.model_name damgası (`bge-m3@ollama`)
        self.timeout = timeout
        # M-9: auth'lu uç (H200/Open WebUI) Bearer ister; auth'suz doğrudan Ollama'da
        # anahtar BOŞ kalır ve başlık hiç gönderilmez (geriye dönük uyum).
        self.api_key = api_key
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import httpx
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            self._client = httpx.Client(timeout=self.timeout, headers=headers)
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

"""Judge gömme modeli otoritesi — sessiz kesme kapısı.

KUSUR (ölçüldü 2026-08-14, H200): `JudgeEmbedder` modeli koşulsuz `OllamaSettings.model`
alıyordu. O alanın BOŞ olması normaldir (M-4: otorite DB'deki `embedding.model`), ve
`RAGINTEL_OLLAMA_MODEL` yalnızca docker-compose `environment:`'ında tanımlı — eval
harness'ı ise konteyner DIŞINDA, host venv'inde koşuyor. Sonuç: uca `":latest"` gidiyor,
/api/embed 500'ü ~575µs'de dönüyor, `answer_relevancy` her CEVAPLANAN kayıtta patlıyor
ve karne `n=0 / faithfulness 0.000` basıyor. Kaynak (VRAM) sorunu gibi okunuyordu.

Bu dosya DB'siz koşar; ağ çağrısı yapmaz.
"""
from __future__ import annotations

import pytest

from ragintel.eval.judge import _VARSAYILAN_EMBED, JudgeEmbedder
from ragintel.ingestion.embedding.embedder import EmbeddingBackendError, OllamaEmbedder


class _SahteAyar:
    """`OllamaSettings` yerine geçer; `model` alanı BOŞ (host'taki gerçek durum)."""

    def __init__(self, model: str = ""):
        self.base_url = "http://h200/ollama"
        self.model = model
        self.timeout = 30.0
        self.api_key = ""


def test_bos_model_CAGRI_ANINDA_patlar():
    """Boş model artık `":latest"`e dönüşüp sessizce 500 almaz."""
    with pytest.raises(EmbeddingBackendError, match="embedding modeli boş"):
        OllamaEmbedder("http://h200/ollama", model="")


def test_judge_DB_otoritesini_kullanir(monkeypatch):
    """Çağıran `embedding.model` verdiğinde uca o gider — boş .env'e RAĞMEN."""
    monkeypatch.setattr("ragintel.eval.judge.OllamaSettings", _SahteAyar)
    e = JudgeEmbedder(model="BAAI/bge-m3")._emb
    assert e.model == "bge-m3:latest"
    assert e.model_name == "bge-m3@ollama"


def test_judge_cagiran_vermezse_VARSAYILANA_duser(monkeypatch):
    """Regresyon çıpası: eskiden burada `":latest"` üretiliyordu."""
    monkeypatch.setattr("ragintel.eval.judge.OllamaSettings", _SahteAyar)
    e = JudgeEmbedder()._emb
    assert e.model != ":latest", "boş model uca sızmamalı"
    assert e.hf_model == _VARSAYILAN_EMBED


def test_env_modeli_ARA_basamak_olarak_kalir(monkeypatch):
    """Öncelik: çağıran (DB) > .env > repo varsayılanı."""
    monkeypatch.setattr("ragintel.eval.judge.OllamaSettings",
                        lambda: _SahteAyar(model="BAAI/bge-m3"))
    assert JudgeEmbedder()._emb.model == "bge-m3:latest"

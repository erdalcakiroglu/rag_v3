"""İP-7 EmbeddingService — ağ dayanıklılık + QC (mock transport; DB gerekmez)."""

from __future__ import annotations

import math

import pytest

from ragintel.config.loader import load_config
from ragintel.ingestion.chunking.chunk import Chunk
from ragintel.ingestion.embedding import (
    EmbeddingBackendError,
    EmbeddingService,
    OllamaEmbedder,
)
from tests import _ollama_mock as om

DIM = om.DIM


def _cfg(batch_size=4):
    return load_config(db_reader=lambda: {
        "embedding": {"model": "BAAI/bge-m3", "dim": DIM,
                      "batch_size": batch_size, "normalize": True}})


def _svc(handler, *, batch_size=4, retries=3):
    emb = OllamaEmbedder("http://mock", client=om.make_client(handler))
    return EmbeddingService(config=_cfg(batch_size), embedder=emb,
                            retries=retries, backoff_base=0.0, sleep=lambda s: None)


def _chunks(n):
    return [Chunk(i, f"metin {i}", f"metin {i}", 5) for i in range(n)]


def test_embed_batch_normalizes_dim_and_stamp():
    emb = OllamaEmbedder("http://mock", client=om.make_client(om.ok_handler()))
    vecs = emb.embed_batch(["a", "b", "c"])
    assert len(vecs) == 3 and len(vecs[0]) == DIM
    for v in vecs:
        assert abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 1e-6   # client L2-normalize
    assert emb.model_name == "bge-m3@ollama"


def test_batches_and_request_count():
    res = _svc(om.ok_handler(), batch_size=4).embed_chunks(_chunks(10))
    assert res.metrics["embedded"] == 10 and res.metrics["failed"] == 0
    assert res.metrics["request_count"] == 3          # 4+4+2
    assert res.metrics["model_name"] == "bge-m3@ollama"
    assert abs(res.metrics["mean_norm"] - 1.0) < 1e-6


def test_retry_on_5xx_then_success():
    stats = {}
    res = _svc(om.flaky_handler(2, 503, stats), batch_size=8).embed_chunks(_chunks(3))
    assert res.metrics["embedded"] == 3
    assert res.metrics["retry_count"] == 2            # 2 retry sonra başarı
    assert stats["calls"] == 3


def test_batch_halving_on_persistent_5xx():
    # boyut>2 -> 503; retry tükenince yarıla (4 -> 2+2 -> 200).
    res = _svc(om.size_gated_handler(ok_max_size=2), batch_size=4).embed_chunks(_chunks(4))
    assert res.metrics["embedded"] == 4
    assert res.metrics["batch_halvings"] >= 1


def test_timeout_triggers_retry():
    stats = {}
    # timeout hep -> retry tükenir -> tek istek de timeout -> backend error
    with pytest.raises(EmbeddingBackendError):
        _svc(om.timeout_handler(), batch_size=1, retries=3).embed_chunks(_chunks(1))


def test_unreachable_raises_backend_error_fast():
    with pytest.raises(EmbeddingBackendError) as ei:
        _svc(om.unreachable_handler(), batch_size=4).embed_chunks(_chunks(4))
    assert "erişilemez" in str(ei.value)


def test_persistent_5xx_single_request_raises():
    with pytest.raises(EmbeddingBackendError):
        _svc(om.size_gated_handler(ok_max_size=0), batch_size=4).embed_chunks(_chunks(4))


def test_qc_embed_failed_skips_bad_vector():
    res = _svc(om.ok_handler(bad_texts={"metin 1"}), batch_size=8).embed_chunks(_chunks(4))
    assert res.metrics["embedded"] == 3 and res.metrics["failed"] == 1
    assert 1 in res.metrics["failed_chunk_indexes"]
    assert "embed_failed" in res.findings
    # atlanan chunk'ın vektörü None.
    assert [i for i in res.items if i.vector is None][0].chunk.chunk_index == 1


def test_embed_anomaly_on_identical_vectors():
    res = _svc(om.ok_handler(identical=True), batch_size=8).embed_chunks(_chunks(5))
    assert res.metrics["mean_pairwise_cosine"] > 0.98
    assert "embed_anomaly" in res.findings


def test_sanitize_fallback_recovers_poison_table_chunk():
    # Uzak uç pipe-ayraçlı chunk'ta deterministik 500; halving tek chunk'a
    # iner, sanitize (pipe→boşluk) varyantı geçer -> dosya kaybetmeden tamamlanır.
    poison = "POZİSYON | ADEDİ\nToplam | 155"
    chunks = [Chunk(0, "temiz metin", "temiz metin", 5),
              Chunk(1, poison, poison, 5),
              Chunk(2, "diğer metin", "diğer metin", 5)]
    res = _svc(om.poison_handler(), batch_size=4).embed_chunks(chunks)
    assert res.metrics["embedded"] == 3 and res.metrics["failed"] == 0   # kayıp YOK
    assert res.metrics["sanitized_count"] == 1
    assert 1 in res.metrics["sanitized_chunk_indexes"]
    assert "embed_sanitized" in res.findings
    # parite: her chunk için vektör var.
    assert all(i.vector is not None for i in res.items)


def test_sanitize_fallback_not_triggered_on_real_outage():
    # Marker olmayan gerçek altyapı arızası (her şey 500) -> sanitize metni
    # değiştirmez, fallback tetiklenmez -> HARD HALT (discipline b).
    with pytest.raises(EmbeddingBackendError):
        _svc(om.poison_handler(marker=""), batch_size=4).embed_chunks(_chunks(4))


def test_sanitize_fallback_reraises_when_variant_also_500s():
    # Sanitize edilmiş metin de 500 verirse (boşluk da zehir) -> içerik-tetikli
    # değil gerçek arıza -> yükselt, sessizce yutma.
    with pytest.raises(EmbeddingBackendError):
        _svc(om.poison_handler(marker=" "), batch_size=4).embed_chunks(
            [Chunk(0, "a | b", "a | b", 5)])


def test_1000_chunks_completes():
    stats = {}
    res = _svc(om.ok_handler(stats), batch_size=64).embed_chunks(_chunks(1000))
    assert res.metrics["embedded"] == 1000 and res.metrics["failed"] == 0
    assert res.metrics["request_count"] == math.ceil(1000 / 64)   # 16
    assert stats["calls"] == 16

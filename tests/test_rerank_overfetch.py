"""Rerank over-fetch: havuz partileme + search_hybrid içi aday derinliği.

NEDEN AYRI DOSYA
    `test_ip33_rerank.py` rerank'in TEK BAŞINA sözleşmesini (passthrough/TEI/
    fail-open) koruyor. Buradaki testler bambaşka bir kusur sınıfını kilitliyor:
    over-fetch açıldığında rerank 200 adayla çağrılıyor ve iki sessiz bozulma
    yolu doğuyor —
      (a) TEI `--max-client-batch-size` (32) aşılırsa 413; 413 fail-open
          listesinde DEĞİL, yani arama komple çöker.
      (b) partilenince TEI'nin `index`'i PARTİYE göredir; ofset eklenmezse
          sonraki partiler öncekilerin skorunu üzerine yazar ve HATA VERMEDEN
          bozuk sıra üretir. Sessiz olan, gürültülü olandan tehlikelidir.
"""

from __future__ import annotations

import json

import httpx
import pytest

from ragintel.config.loader import ConfigError, load_config
from ragintel.retrieval import RetrievalService


class _Embedder:
    model_name = "bge-m3@ollama"

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class _Store:
    """Sahte depo — `search_hybrid`'in İSTEDİĞİ derinliği kaydeder."""

    def __init__(self, havuz_boyu: int):
        self.texts = {i: f"t{i}" for i in range(havuz_boyu)}
        self.istenen_top_k: int | None = None

    def search_hybrid(self, *, top_k, **_kw):
        self.istenen_top_k = top_k
        return [
            {"chunk_id": i, "text": f"t{i}", "detail": {}}
            for i in range(min(top_k, len(self.texts)))
        ]

    def rerank_texts(self, *, chunk_ids, allowed_doc_scopes):
        return [{"chunk_id": cid, "text": self.texts[cid]} for cid in chunk_ids]


def _svc(store, *, backend, pool=200, batch=32, client=None, max_top_k=20):
    cfg = load_config(
        db_reader=lambda: {
            "retrieval": {
                "rerank_backend": backend,
                "rerank_pool": pool,
                "rerank_client_batch": batch,
                "max_top_k": max_top_k,
                "rerank_retries": 0,
            }
        }
    )
    return RetrievalService(config=cfg, embedder=_Embedder(), store=store, rerank_client=client)


def _ctx():
    return {"user_id": "u", "tenant_id": "t", "roles": ["reader"], "allowed_doc_scopes": ["default"]}


def _tei_client(istekler: list[list[str]]):
    """Gerçek TEI gibi: skor = metnin sayısı, parti İÇİNDE azalan sırada döner."""

    def handler(request):
        texts = json.loads(request.content)["texts"]
        istekler.append(texts)
        sonuc = [{"index": i, "score": float(t[1:])} for i, t in enumerate(texts)]
        sonuc.sort(key=lambda r: r["score"], reverse=True)
        return httpx.Response(200, json=sonuc)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mock", timeout=5.0)


def test_havuz_parti_parti_gonderilir_ve_hicbir_istek_siniri_asmaz():
    """413'ün doğduğu yer: tek istekte 32'den fazla metin."""
    istekler: list[list[str]] = []
    store = _Store(100)
    svc = _svc(store, backend="tei", client=_tei_client(istekler), batch=32)

    svc.rerank("q", list(range(100)), user_ctx=_ctx())

    assert len(istekler) == 4  # ceil(100/32)
    assert all(len(p) <= 32 for p in istekler)
    assert sum(len(p) for p in istekler) == 100


def test_parti_ofseti_atlanirsa_yakalanir_siralama_bozulmaz():
    """Ofset kusurunun İMZASI: son partinin skorları ilk adaylara yapışır.

    5 aday, parti 2 → [t0,t1] [t2,t3] [t4]. Ofset eklenmezse t4'ün skoru
    t0'a yazılır ve t0 en tepeye çıkar. Beklenen sıra saf azalan: 4..0.
    """
    istekler: list[list[str]] = []
    store = _Store(5)
    svc = _svc(store, backend="tei", client=_tei_client(istekler), batch=2)

    out = svc.rerank("q", [0, 1, 2, 3, 4], user_ctx=_ctx())

    assert [r["chunk_id"] for r in out] == [4, 3, 2, 1, 0]
    assert [r["rerank_score"] for r in out] == [4.0, 3.0, 2.0, 1.0, 0.0]
    assert len(istekler) == 3


def test_rerank_acikken_db_den_havuz_kadar_aday_cekilir_sonuc_top_k():
    """Kazancın geldiği yer: top_k değil `rerank_pool` çekilir, sonra kırpılır."""
    istekler: list[list[str]] = []
    store = _Store(200)
    svc = _svc(store, backend="tei", client=_tei_client(istekler), pool=200)

    out = svc.search_hybrid("q", top_k=10, user_ctx=_ctx())

    assert store.istenen_top_k == 200, "DB'den havuz kadar aday çekilmedi"
    assert len(out) == 10, "kullanıcıya top_k'dan fazlası dönmemeli"
    # En yüksek skorlu 10 aday, cross-encoder sırasında.
    assert [c["chunk_id"] for c in out] == list(range(199, 189, -1))


def test_passthrough_iken_over_fetch_yapilmaz():
    """Varsayılan kurulum (TEI yok) fazladan DB maliyeti ÖDEMEMELİ."""
    store = _Store(200)
    svc = _svc(store, backend="passthrough", pool=200)

    out = svc.search_hybrid("q", top_k=10, user_ctx=_ctx())

    assert store.istenen_top_k == 10
    assert [c["chunk_id"] for c in out] == list(range(10))


def test_havuz_ust_sinirin_altina_inemez():
    """Sessiz daralma kapısı: pool < max_top_k → sonuç SAYISI düşerdi."""
    with pytest.raises(ConfigError, match="rerank_pool"):
        load_config(db_reader=lambda: {"retrieval": {"rerank_pool": 10, "max_top_k": 20}})

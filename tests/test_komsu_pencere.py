"""Komşu-chunk pencere genişletmesi — DB'siz bekçi.

NEDEN VAR (2026-08-14 ölçümü, v1-bddk synthesis r@10 0.145): altın chunk'ların %40'ı
200 adaylık havuza HİÇ giremiyor ve bunların 9/10'unda doğru DOSYA zaten havuzda.
Retriever belgeyi buluyor, kanıtı taşıyan parçayı seçemiyor. Havuz dışı chunk'ların
%50'si ±1 komşu mesafesinde.

Bu testler kazancı ÖLÇMEZ — kazanç ancak canlı A/B ile bilinir. Burada sabitlenen şey
genişletmenin DAVRANIŞI: varsayılan kapalı, kuyruğa ekler, mükerrer üretmez, kapsam
süzgecini atlatmaz ve çökerse aramayı düşürmez.
"""

from __future__ import annotations

import pytest

from ragintel.config.loader import load_config
from ragintel.retrieval import RetrievalService
from ragintel.retrieval import repository as repo


def _cfg(**overrides):
    retrieval = {"default_top_k": 10, "max_top_k": 20, "rerank_pool": 200}
    retrieval.update(overrides)
    return load_config(db_reader=lambda: {"retrieval": retrieval})


def _chunk(cid: int, fid: int = 1) -> dict:
    return {"chunk_id": cid, "text": f"metin-{cid}", "score": 1.0 / cid,
            "source": {"file_id": fid, "file_name": "a.pdf", "page": 1,
                       "section": None, "version": 1},
            "retrieval_method": "hybrid"}


class _Store:
    """Komşu çağrısını kaydeden sahte depo."""

    def __init__(self, komsular=None, patla=False):
        self.komsular = komsular or []
        self.patla = patla
        self.cagri = None

    def fetch_neighbours(self, *, allowed_doc_scopes, chunk_ids, window, exclude_ids=None):
        self.cagri = {"scopes": allowed_doc_scopes, "tohum": list(chunk_ids),
                      "window": window, "haric": list(exclude_ids or [])}
        if self.patla:
            raise RuntimeError("DB gitti")
        return [c for c in self.komsular if c["chunk_id"] not in set(exclude_ids or [])]


def _servis(store, **cfg_over):
    return RetrievalService(db=None, config=_cfg(**cfg_over), store=store)


def test_varsayilan_KAPALI():
    """Ayar verilmezse hiçbir şey değişmez — depoya bile gidilmez."""
    store = _Store(komsular=[_chunk(99)])
    svc = _servis(store)
    havuz = [_chunk(1), _chunk(2)]
    sonuc, eklenen = svc._komsu_genislet(havuz, ["s"])
    assert sonuc == havuz and eklenen == 0
    assert store.cagri is None, "kapalıyken komşu sorgusu HİÇ atılmamalı"


def test_acikken_kuyruga_eklenir_bas_bozulmaz():
    """Komşu havuzun SONUNA eklenir: rerank fail-open'a düşerse top_k değişmez."""
    store = _Store(komsular=[_chunk(50), _chunk(51)])
    svc = _servis(store, rerank_neighbor_window=1)
    havuz = [_chunk(1), _chunk(2), _chunk(3)]
    sonuc, eklenen = svc._komsu_genislet(havuz, ["s"])
    assert eklenen == 2
    assert [c["chunk_id"] for c in sonuc] == [1, 2, 3, 50, 51]


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    """SQL'i çalıştırmadan parametreleri yakalayan sahte bağlantı."""

    def __init__(self, rows):
        self._rows = rows
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params
        return _Cur(self._rows)


def test_komsunun_skoru_sifir_ve_yontemi_lookup():
    """Komşunun hibrit skoru YOKTUR — uydurulmuş skor sıralamayı sessizce bozardı.

    Ayrıca `retrieval_method` 'lookup' olmalı: bu chunk hibrit aramanın bulduğu değil,
    pencerenin getirdiği bir chunk'tır ve kaynak gösteriminde öyle görünmelidir.
    """
    conn = _Conn([(50, "komsu metin", 7, "a.pdf", 3, "Madde 4", 1)])
    out = repo.fetch_neighbours(conn, allowed_doc_scopes=["s"], chunk_ids=[49], window=1)
    assert len(out) == 1
    assert out[0]["score"] == 0.0
    assert out[0]["retrieval_method"] == "lookup"
    assert out[0]["chunk_id"] == 50
    assert out[0]["source"] == {"file_id": 7, "file_name": "a.pdf", "page": 3,
                                "section": "Madde 4", "version": 1}


def test_repo_pencereyi_iki_yone_de_acar_ve_kapsami_gecer():
    """SQL parametre sırası: (tohum, -window, +window, kapsam, hariç)."""
    conn = _Conn([])
    repo.fetch_neighbours(conn, allowed_doc_scopes=["sc"], chunk_ids=[10],
                          window=2, exclude_ids=[10, 11])
    assert conn.params == ([10], 2, 2, ["sc"], [10, 11])
    assert "doc_scope = ANY" in conn.sql, "kapsam süzgeci SQL'den düşmüş olamaz"


def test_repo_haric_verilmezse_tohum_haric_tutulur():
    conn = _Conn([])
    repo.fetch_neighbours(conn, allowed_doc_scopes=["sc"], chunk_ids=[10, 12], window=1)
    assert conn.params[-1] == [10, 12]


def test_yalniz_tepedeki_chunklarin_komsusu_istenir():
    """Havuzun TAMAMINA uygulamak TEI'ye giden metni katlar — tohum tepeyle sınırlı."""
    store = _Store()
    svc = _servis(store, rerank_neighbor_window=1, rerank_neighbor_top=2)
    svc._komsu_genislet([_chunk(i) for i in range(1, 11)], ["s"])
    assert store.cagri["tohum"] == [1, 2]
    assert store.cagri["window"] == 1


def test_havuzdakiler_haric_gonderilir_mukerrer_olmaz():
    """Dışlama listesi TOHUM değil HAVUZUN TAMAMI olmalı.

    Yalnız tohum dışlanırsa havuzun derinlerinde zaten duran bir chunk komşu diye
    ikinci kez eklenir; rerank onu iki kez skorlar ve top_k'da mükerrer görünür.
    """
    store = _Store(komsular=[_chunk(5), _chunk(50)])
    svc = _servis(store, rerank_neighbor_window=1, rerank_neighbor_top=2)
    havuz = [_chunk(1), _chunk(2), _chunk(5)]
    sonuc, eklenen = svc._komsu_genislet(havuz, ["s"])
    assert store.cagri["haric"] == [1, 2, 5]
    assert eklenen == 1
    assert [c["chunk_id"] for c in sonuc] == [1, 2, 5, 50]


def test_kapsam_suzgeci_komsuya_da_gecer():
    """Pencere, kullanıcının göremeyeceği chunk'ı yan kapıdan havuza SOKAMAZ."""
    store = _Store()
    svc = _servis(store, rerank_neighbor_window=2)
    svc._komsu_genislet([_chunk(1)], ["scope_a", "scope_b"])
    assert store.cagri["scopes"] == ["scope_a", "scope_b"]


def test_genisletme_cokerse_arama_dusmez():
    """Genişletme OPSİYONEL: DB komşu sorgusunda patlarsa hibrit sonuç aynen döner."""
    store = _Store(patla=True)
    svc = _servis(store, rerank_neighbor_window=1)
    havuz = [_chunk(1), _chunk(2)]
    sonuc, eklenen = svc._komsu_genislet(havuz, ["s"])
    assert sonuc == havuz and eklenen == 0


def test_repo_bos_girdide_sorgu_atmaz():
    """window=0 / boş tohum / boş kapsam → DB'ye hiç gidilmemeli (conn=None ile kanıt)."""
    assert repo.fetch_neighbours(None, allowed_doc_scopes=["s"], chunk_ids=[1], window=0) == []
    assert repo.fetch_neighbours(None, allowed_doc_scopes=["s"], chunk_ids=[], window=1) == []
    assert repo.fetch_neighbours(None, allowed_doc_scopes=[], chunk_ids=[1], window=1) == []


def test_config_sinirlari():
    """Pencere üst sınırı olmalı: ±50 komşu havuzu patlatır, sessizce kabul edilmemeli."""
    assert int(_cfg().group("retrieval").rerank_neighbor_window) == 0
    with pytest.raises(Exception):
        _cfg(rerank_neighbor_window=50)


def test_lookup_window_ile_karismaz():
    """AYRI ayar: `lookup_window` kaynak panelinin bağlam penceresi, bu değil.

    Aynı adı kullansaydık sunum katmanı sessizce değişirdi.
    """
    rc = _cfg(rerank_neighbor_window=1).group("retrieval")
    assert int(rc.lookup_window) == 2
    assert int(rc.rerank_neighbor_window) == 1

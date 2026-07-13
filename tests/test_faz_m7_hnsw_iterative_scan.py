"""M-7: filtreli-ANN aday tükenmesi (scope'lu sorguda SONUÇ KAYBI) — regresyon kilidi.

BULGU (canlıda yakalandı): HNSW önce `ef_search` kadar en yakın adayı bulur, doc_scope
süzgeci SONRA uygulanır. Adayların TAMAMI kullanıcının scope'u DIŞINDAysa sonuç BOŞ
döner — belge var, benzerlik makul, ama getirilemez. Bu bir sızıntı değil (fail-closed
sağlam), SESSİZ bir kalite kaybıdır: agent "bulunamadı" der.

Bu dosya davranış FARKINI kilitler: iterative_scan='off' iken çöküş YENİDEN ÜRETİLİR,
'relaxed_order' iken kurtarılır. Yalnızca "düzeltilmiş hâl çalışıyor" demek yetmez —
o test, bug hiç var olmasaydı da geçerdi (vacuous). Farkın kendisi kanıtlanmalı.
"""

from __future__ import annotations

import pytest

from ragintel.config.loader import load_config
from ragintel.config.settings import RetrievalConfig
from ragintel.database import make_db_reader
from ragintel.retrieval import RetrievalService

# Envanter belgelerinin konusu (SQL Server envanteri); default korpus karbon vergisi.
# Bu sorgunun en yakın komşuları 'envanter' scope'undadır → süzgeç onları eler.
_CAPRAZ_SORGU = "SQL Server instance performance"
_DEFAULT_CTX = {"user_id": "u", "tenant_id": "t", "roles": ["user"],
                "allowed_doc_scopes": ["default"]}


def _service(live_db, iterative_scan: str) -> RetrievalService:
    """Ayarı GERÇEKTEN değiştir: RetrievalService config'i `group()`'un döndürdüğü
    TİPLİ modelden okur — `pipeline` sözlüğünü değiştirmek ETKİSİZDİR (ilk denemede
    bu tuzağa düştüm: 'off' testi sessizce relaxed_order ile koşup yeşil verdi)."""
    cfg = load_config(db_reader=make_db_reader(live_db))
    cfg._typed["retrieval"] = cfg.group("retrieval").model_copy(
        update={"hnsw_iterative_scan": iterative_scan})
    svc = RetrievalService(db=live_db, config=cfg)
    assert svc.retrieval_cfg.hnsw_iterative_scan == iterative_scan   # override tuttu mu?
    return svc


@pytest.mark.db
def test_iterative_scan_off_reproduces_candidate_starvation(live_db):
    """REGRESYON KİLİDİ: eski davranış (off) çöküşü YENİDEN ÜRETİR.

    Bu test 'başarısızlığı' kanıtlar — düzeltmenin gerçekten bir şeyi değiştirdiğini
    gösteren tek şey budur. Bir gün pgvector/veri değişir de çöküş artık yaşanmazsa
    bu test kırılır ve bize 'kilit anlamsızlaştı' der (sessizce yeşil kalmaz).
    """
    svc = _service(live_db, "off")
    hits = svc.search_hybrid(_CAPRAZ_SORGU, top_k=10, user_ctx=_DEFAULT_CTX)
    assert hits == [], (
        "iterative_scan='off' iken çapraz sorgunun BOŞ dönmesi bekleniyordu; dönmediyse "
        "aday tükenmesi artık yaşanmıyor demektir → bu regresyon kilidi anlamını yitirdi, "
        "gözden geçirin (korpus/pgvector değişmiş olabilir)."
    )


@pytest.mark.db
def test_iterative_scan_recovers_own_scope_results(live_db):
    """DÜZELTME: relaxed_order ile kullanıcı KENDİ scope'undan sonuç alır."""
    svc = _service(live_db, "relaxed_order")
    hits = svc.search_hybrid(_CAPRAZ_SORGU, top_k=10, user_ctx=_DEFAULT_CTX)
    assert hits, "relaxed_order ile çapraz sorgu kendi scope'undan sonuç ÜRETMELİ"

    # ...ve bu sonuçların HEPSİ kendi scope'undan olmalı (kurtarma sızıntıya dönüşmesin!).
    with live_db.connection() as conn:
        envanter = {r[0] for r in conn.execute(
            "SELECT file_name FROM core_files WHERE doc_scope='envanter';").fetchall()}
    getirilen = {h["source"]["file_name"] for h in hits}
    assert not (getirilen & envanter), (
        "aday tükenmesini çözerken SCOPE SIZINTISI oluştu — fail-closed bozuldu")


@pytest.mark.db
def test_normal_query_unaffected(live_db):
    """Karşı-örnek: kendi scope'undaki normal sorgu her iki ayarda da çalışır —
    yani 'off' her şeyi bozmuyor, yalnızca FİLTRELİ aday tükenmesinde çöküyor."""
    for mode in ("off", "relaxed_order"):
        hits = _service(live_db, mode).search_hybrid(
            "Karbon vergisi nedir?", top_k=10, user_ctx=_DEFAULT_CTX)
        assert hits, f"normal sorgu {mode} ayarında sonuç vermeli"


def test_default_is_relaxed_order():
    """Varsayılan güvenli tarafta: yeni kurulumda çöküş YAŞANMAZ."""
    assert RetrievalConfig().hnsw_iterative_scan == "relaxed_order"


def test_invalid_value_rejected():
    """Değer SQL'e string olarak gömülüyor → allowlist dışı değer REDDEDİLMELİ."""
    from ragintel.retrieval.repository import _apply_hnsw_settings
    with pytest.raises(ValueError):
        _apply_hnsw_settings(object(), 80, "relaxed_order; DROP TABLE core_chunks--")

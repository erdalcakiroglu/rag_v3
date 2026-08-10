"""Kodun yazdığı her qc_findings.finding değeri DDL'deki CHECK listesinde olmalı.

NEDEN: Bu kusur iki kez yaşandı. Ek4'te 'embed_sanitized', Ek5'te
'encoding_repaired'/'encoding_broken' CHECK listesine eklenmemişti; insert
kısıtı ihlal edince dosyanın TÜM transaction'ı rollback oldu ve dosya FAILED'e
düştü. İkisi de birim testlerinden geçti çünkü testler DB'ye yazmıyor -- kusur
yalnızca canlı korpusta göründü (2026-08-10: 35 dosya FAILED, onarım aslında
çalışmıştı). Bu test o farkı DB'siz yakalar.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

KOK = Path(__file__).resolve().parents[1]
KOD = KOK / "ragintel"
SEMA = KOK / "docs"

# finding="x"  |  finding='x'  |  "finding": "x"
_KOD_DESENI = re.compile(r"""finding(?:=|"\s*:\s*)["']([a-z_][a-z0-9_]*)["']""")


def _ddl_dosyasi() -> Path:
    """Kısıtı EN SON tanımlayan şema eki (ad sırası = uygulama sırası)."""
    adaylar = sorted(
        p for p in SEMA.glob("FAZ1_Sema*.sql")
        if "ADD CONSTRAINT qc_findings_finding_check" in p.read_text(encoding="utf-8")
    )
    assert adaylar, "qc_findings_finding_check'i tanımlayan şema eki bulunamadı"
    return adaylar[-1]


def _ddl_bulgulari() -> set[str]:
    metin = _ddl_dosyasi().read_text(encoding="utf-8")
    govde = metin.split("ADD CONSTRAINT qc_findings_finding_check", 1)[1]
    govde = govde.split("));", 1)[0]
    # SQL yorum satırları liste dışıdır: 'encoding_broken' geçen bir yorum
    # kısıtı genişletmez, testi yanlış yere yeşile boyamasın.
    govde = "\n".join(s.split("--", 1)[0] for s in govde.splitlines())
    return set(re.findall(r"'([a-z_][a-z0-9_]*)'", govde))


def _kod_bulgulari() -> dict[str, str]:
    """bulgu -> onu yazan ilk dosya (hata mesajı işe yarasın diye)."""
    bulunan: dict[str, str] = {}
    for py in sorted(KOD.rglob("*.py")):
        for ad in _KOD_DESENI.findall(py.read_text(encoding="utf-8")):
            bulunan.setdefault(ad, str(py.relative_to(KOK)))
    return bulunan


def test_ddl_listesi_okunabiliyor():
    """Ölçüm aracının kendisi: liste boş/eksik dönerse test sahte yeşil verir."""
    ddl = _ddl_bulgulari()
    assert {"empty_chunk", "too_short", "embed_failed"} <= ddl, ddl


def test_kodda_yazilan_her_bulgu_ddlde_var():
    ddl = _ddl_bulgulari()
    kod = _kod_bulgulari()
    assert kod, "kodda hiç finding literali bulunamadı -- desen bozulmuş olabilir"
    eksik = {ad: yer for ad, yer in kod.items() if ad not in ddl}
    assert not eksik, (
        "qc_findings CHECK listesinde OLMAYAN bulgular kodda yazılıyor -- canlıda "
        f"insert patlar ve dosya FAILED olur. Yeni bir şema eki gerekli: {eksik}"
    )


@pytest.mark.parametrize("ad", ["encoding_repaired", "encoding_broken"])
def test_glif_bulgulari_ddlde(ad):
    """Ek5 regresyonu: Adım-4 bulguları açıkça aranır."""
    assert ad in _ddl_bulgulari()

"""Kanıt probe'unun sarmalayıcısı ÜRETİM imzasından geri kalmamalı.

NEDEN VAR
    `scripts/kilit_kacis_kanit_probe.py` gerçek `_forced_final_complete`'i koşturup
    `gateway.complete`'i sarmalayarak ölçer. Sarmalayıcı sabit imzalıydı
    (`messages, tools, max_retries`); üretime `timeout=` eklenince (7a61239) çağrı uca
    HİÇ gitmeden `TypeError`'a düştü. `TypeError` retry edilebilir değil → yükseldi,
    kayıt listesi BOŞ kaldı ve probe bunu "kilit oluşmadı" diye okudu. Dört koşum
    boyunca tek bir istek çıkmadı; sonuç "kilit kayboldu" sanıldı.

    Bu, ölçüm aracının kendi arızasının olguya dönüşmesidir — karne kendi paydasını
    ilan etmelidir ailesinin dördüncü örneği. İki koruma kilitleniyor:
      1. sarmal `**kw` ile geçirir → yeni parametre sessizce kırmaz, KAYDEDİLİR.
      2. hiç çağrı kaydedilmediyse bu ayrı bir sonuçtur (exit 4), "kilit yok" DEĞİL.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from ragintel.agents.nodes.agent import _forced_final_complete
from ragintel.agents.tools import ToolRegistry

_YOL = Path(__file__).resolve().parents[1] / "scripts" / "kilit_kacis_kanit_probe.py"


def _probe():
    spec = importlib.util.spec_from_file_location("kilit_kacis_kanit_probe", _YOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Gecirgen:
    """Üretim gibi: adlandırılmış her şeyi kabul eder, ne geldiğini saklar."""
    def __init__(self):
        self.gorulen: list[dict] = []

    def complete(self, *, messages, tools, **kw):
        self.gorulen.append(kw)
        return "YANIT"


def test_sarmalayici_uretimin_gonderdigi_her_seyi_gecirir():
    """Regresyon: `timeout` sarmalayıcıda yokken çağrı uca hiç gitmiyordu."""
    gw, kayit = _Gecirgen(), []
    _probe()._izleyen(gw, kayit)

    _forced_final_complete(gw, ToolRegistry(None), [{"role": "user", "content": "q"}], {},
                           timeout=45.0)

    assert gw.gorulen == [{"max_retries": 0, "timeout": 45.0}]   # uca AYNEN gitti
    assert kayit[0]["timeout"] == 45.0 and kayit[0]["sonuc"] == "OK"


def test_sarmalayici_yeni_parametreye_dayanikli():
    """İmza bir daha üretimden geri kalmasın: bilinmeyen parametre de geçer."""
    gw, kayit = _Gecirgen(), []
    sarmalanmis = _probe()._izleyen(gw, kayit)

    sarmalanmis.complete(messages=[], tools=[{"x": 1}], max_retries=0, timeout=45.0,
                         henuz_olmayan_parametre="z")

    assert gw.gorulen[0]["henuz_olmayan_parametre"] == "z"
    assert kayit[0]["tools"] == 1

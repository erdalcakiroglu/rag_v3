"""İçerik-mükerrer kestirimi doğru sayı üretmeli — DB'siz bekçi.

NEDEN VAR: `scripts/mukerrer_icerik_probe.py` bir dosyanın içeriğinin başka bir
dosyada geçip geçmediğine bottom-k MinHash TAHMİNİYLE karar verir. Tahmin sessizce
yanlışsa probe "mükerrer yok" der ve 08-13'te elenen türden altı kopya korpusta
kalmaya devam eder — bu projede beş kez yakalanan "ölçüm aracının kendi kusurunu
olgu olarak raporlaması" ailesinin aynısı.

Bu yüzden kestirim BİLİNEN GERÇEĞE karşı sınanır: içerilme oranını elle bildiğimiz
küme çiftleri verilir, çıkan sayı toleransla kıyaslanır.

Testi yazarken bir kez YANLIŞ kurdum ve kaydı burada tutuyorum: shingle hash'i
yerine ardışık tamsayı (0,1,2…) verirsem A ve B'nin bottom-k taslakları birebir
aynı çıkar ve tahmin 0.500 yerine 0.750 döner. Kusur algoritmada değil test
verisindeydi — bottom-k hash'lerin DÜZGÜN DAĞILDIĞINI varsayar (gerçekte
Python `hash()` = SipHash, uniform). Rastgele değer kullanmak şart.
"""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from mukerrer_icerik_probe import _adaylar, _etiket  # noqa: E402

K = 256
_RND = random.Random(7)
_H: dict[int, int] = {}


def _h(i: int) -> int:
    if i not in _H:
        _H[i] = _RND.getrandbits(63)
    return _H[i]


def _taslak(s):
    return frozenset(sorted(_h(x) for x in s)[:K])


def _olc(a: set[int], b: set[int]):
    """(içerilme_A, içerilme_B) — çift bulunamazsa (0.0, 0.0)."""
    r = _adaylar({1: _taslak(a), 2: _taslak(b)}, {1: len(a), 2: len(b)},
                 K, 0.0, kalabalik=50)
    if not r:
        return 0.0, 0.0
    return r[0][2], r[0][3]


def test_tam_alt_kume():
    """A tamamen B'nin içinde: A⊂B = 1.0, B⊂A = |A|/|B| = 0.5."""
    ca, cb = _olc(set(range(1000)), set(range(2000)))
    assert ca > 0.95
    assert abs(cb - 0.50) < 0.08


def test_birebir_ayni():
    ca, cb = _olc(set(range(1000)), set(range(1000)))
    assert ca > 0.95 and cb > 0.95
    assert _etiket(ca, cb) == "ÇİFT YÖNLÜ ≈AYNI"


def test_yari_ortusme_mukerrer_sayilmaz():
    """Ortak mevzuat metni alıntılayan iki AYRI belge — silme adayı OLMAMALI."""
    ca, cb = _olc(set(range(0, 1000)), set(range(500, 1500)))
    assert abs(ca - 0.50) < 0.08 and abs(cb - 0.50) < 0.08
    assert _etiket(ca, cb) == "KISMİ ÖRTÜŞME"


def test_ilgisiz_dosyalar_cift_uretmez():
    ca, cb = _olc(set(range(1000)), set(range(50000, 51000)))
    assert ca == 0.0 and cb == 0.0


def test_kucuk_ortusme_esigi_asmaz():
    ca, cb = _olc(set(range(1000)), set(range(900, 1900)))
    assert ca < 0.20 and cb < 0.20


def test_esik_altindaki_cift_raporlanmaz():
    """--esik gerçekten süzmeli: %10 örtüşen çift 0.60 eşiğinde çıkmamalı."""
    r = _adaylar({1: _taslak(set(range(1000))), 2: _taslak(set(range(900, 1900)))},
                 {1: 1000, 2: 1000}, K, 0.60, kalabalik=50)
    assert r == []


def test_kalabalik_kalip_metni_cift_uretmez():
    """Kalıp shingle (çok dosyada geçen) aday üretiminde atlanmalı.

    Mevzuat korpusunda "MADDE 1 – (1) Bu Yönetmeliğin amacı…" her yerde geçer;
    atlanmazsa hem karesel çift patlaması olur hem de alakasız dosyalar eşleşir.
    """
    ortak = set(range(9000, 9100))
    taslaklar = {i: _taslak(ortak | {900000 + i}) for i in range(1, 31)}
    boyutlar = {i: 101 for i in taslaklar}
    assert _adaylar(taslaklar, boyutlar, K, 0.0, kalabalik=20) == []
    assert _adaylar(taslaklar, boyutlar, K, 0.0, kalabalik=50) != []

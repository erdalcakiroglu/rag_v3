"""Glif onarımı (bozuk font kodlaması) — saf katman testleri, DB YOK.

Ölçüm zemini: eşik ve davranışlar 2026-08-09 korpus ölçümünden geliyor
(gerçekten bozuk dosyalar imza/1000 = 27..120; metni sağlam olup meşru imza
taşıyanlar 5'in altında). Testler o ayrımı ve "asla daha kötüsüyle değiştirme"
invaryantını koruyor.
"""

from __future__ import annotations

from contextlib import contextmanager

from ragintel.ingestion.parsing.glyph_repair import (
    birlestir,
    bozuk_sayfalar,
    imza_yogunlugu,
)
from ragintel.ingestion.parsing.parsed_document import (
    Figure,
    Page,
    ParsedDocument,
    Section,
    Table,
)

# Aile-A gerçek örneği (60._Yilinda): kaydırılmış ASCII + imza glifleri.
BOZUK = ("60. YÕlÕnda Trkiye Bankalar Birli÷i ve Trk bankacÕlÕk sistemi "
         "hakkÕnda deùerlendirme raporu. " * 4)
SAGLAM = ("Bankaların faaliyet izni Kurul tarafından verilir ve bu Kanunda "
          "belirtilen şartlar aranır. İlgili düzenlemeler saklıdır. " * 4)


def _sayfa(no: int, metin: str) -> Page:
    return Page(page_no=no, text_blocks=[metin])


def _belge(*sayfalar: Page) -> ParsedDocument:
    pd = ParsedDocument()
    pd.pages = list(sayfalar)
    return pd


# --- tespit -----------------------------------------------------------------

def test_imza_yogunlugu_bozuk_ile_saglami_ayirir():
    assert imza_yogunlugu(BOZUK) >= 10.0
    assert imza_yogunlugu(SAGLAM) < 1.0
    assert imza_yogunlugu("") == 0.0


def test_bozuk_sayfa_bulunur_saglam_sayfa_isaretlenmez():
    pd = _belge(_sayfa(1, SAGLAM), _sayfa(2, BOZUK), _sayfa(3, SAGLAM))
    assert bozuk_sayfalar(pd) == {2}


def test_kisa_sayfa_tetigi_degerlendirmez():
    """Kapak/boş sayfada yoğunluk tek karakterle patlar — eşik oraya bakmamalı."""
    pd = _belge(_sayfa(1, "Birli÷i"))
    assert imza_yogunlugu("Birli÷i") >= 10.0        # yoğunluk gerçekten yüksek
    assert bozuk_sayfalar(pd) == set()              # ama sayfa kısa -> elenir


def test_esik_config_ile_gelir():
    pd = _belge(_sayfa(1, SAGLAM), _sayfa(2, BOZUK))
    assert bozuk_sayfalar(pd, imza_bin=1000.0) == set()
    assert bozuk_sayfalar(pd, imza_bin=0.1) == {2}


# --- birleştirme ------------------------------------------------------------

def test_bozuk_sayfa_ocrdan_saglam_sayfa_referanstan_gelir():
    ref = _belge(_sayfa(1, SAGLAM), _sayfa(2, BOZUK))
    ocr = _belge(_sayfa(1, "OCR bunu okumamaliydi cunku sayfa saglamdi. " * 3),
                 _sayfa(2, SAGLAM))
    out = birlestir(ref, ocr, {2})
    assert out.pages[0].text == SAGLAM              # referans korundu
    assert out.pages[1].text == SAGLAM              # OCR'dan geldi
    assert bozuk_sayfalar(out) == set()


def test_hicbir_sayfa_dusmez():
    ref = _belge(_sayfa(1, SAGLAM), _sayfa(2, BOZUK), _sayfa(3, SAGLAM))
    ocr = _belge(_sayfa(2, SAGLAM))                 # OCR yalnız bir sayfa döndü
    out = birlestir(ref, ocr, {2})
    assert [p.page_no for p in out.pages] == [1, 2, 3]


def test_ocr_bos_dondugunde_sayfa_degistirilmez():
    """Onarım tek yönlüdür: OCR sayfayı okuyamadıysa bozuk metin SİLİNMEZ."""
    ref = _belge(_sayfa(1, BOZUK))
    ocr = _belge(_sayfa(1, "  "))
    out = birlestir(ref, ocr, {1})
    assert out.pages[0].text == BOZUK
    assert any("daha iyi değildi" in u for u in out.parse_warnings)


def test_ocr_daha_bozuk_dondugunde_sayfa_degistirilmez():
    ref = _belge(_sayfa(1, SAGLAM))
    ocr = _belge(_sayfa(1, BOZUK))
    out = birlestir(ref, ocr, {1})
    assert out.pages[0].text == SAGLAM


def test_ocr_cok_kisa_dondugunde_sayfa_degistirilmez():
    """İmza sıfır olsa bile metnin dörtte birinden azı kabul edilmez."""
    ref = _belge(_sayfa(1, BOZUK))
    ocr = _belge(_sayfa(1, "Bankaların faaliyet izni Kurul tarafindan verilir."))
    out = birlestir(ref, ocr, {1})
    assert out.pages[0].text == BOZUK


def test_tablo_sayfasiyla_birlikte_tasinir_ve_yeniden_numaralanir():
    ref = _belge(_sayfa(1, SAGLAM), _sayfa(2, BOZUK))
    ref.tables = [Table(index=0, data=[["a"]], flattened_text="a", page_no=1),
                  Table(index=1, data=[["bozuk"]], flattened_text="bozuk", page_no=2)]
    ocr = _belge(_sayfa(1, SAGLAM), _sayfa(2, SAGLAM))
    ocr.tables = [Table(index=0, data=[["ocr1"]], flattened_text="ocr1", page_no=1),
                  Table(index=1, data=[["ocr2"]], flattened_text="ocr2", page_no=2)]
    out = birlestir(ref, ocr, {2})
    assert [(t.index, t.page_no, t.flattened_text) for t in out.tables] == [
        (0, 1, "a"), (1, 2, "ocr2"),
    ]


def test_sekiller_her_zaman_referanstan_gelir():
    """Şekil görüntüsü piksellerden üretilir; metin bozulmasından etkilenmez."""
    ref = _belge(_sayfa(1, BOZUK))
    ref.figures = [Figure(index=0, page_no=1, caption="ref", image_png=b"png")]
    ocr = _belge(_sayfa(1, SAGLAM))
    ocr.figures = [Figure(index=0, page_no=1, caption="ocr", image_png=None)]
    out = birlestir(ref, ocr, {1})
    assert [(f.caption, f.image_png) for f in out.figures] == [("ref", b"png")]


def test_char_span_birlesik_metne_gore_yeniden_hesaplanir():
    # Başlık ÜRETİMDE de sayfa metninin içindedir: docling başlık öğesini hem
    # sections'a hem sayfanın text_blocks'una yazar.
    baslik = "İKİNCİ BÖLÜM"
    ref = _belge(_sayfa(1, "BİRİNCİ BÖLÜM " + SAGLAM), _sayfa(2, BOZUK))
    ref.sections = [Section(title="BİRİNCİ BÖLÜM", level=1, page_start=1,
                            char_span=(999, 1012))]
    ocr = _belge(_sayfa(1, SAGLAM), _sayfa(2, baslik + " " + SAGLAM))
    ocr.sections = [Section(title=baslik, level=1, page_start=2, char_span=(0, 12))]
    out = birlestir(ref, ocr, {2})
    govde = out.body_text
    for s in out.sections:
        assert s.char_span is not None
        bas, son = s.char_span
        assert govde[bas:son] == s.title


def test_char_span_bulunamazsa_none_kalir():
    """Yanlış aralık yazmaktansa boş bırakılır (kontrat None'a izin veriyor)."""
    ref = _belge(_sayfa(1, SAGLAM))
    ref.sections = [Section(title="metinde OLMAYAN baslik", level=1, page_start=1)]
    out = birlestir(ref, _belge(), set())
    assert out.sections[0].char_span is None


def test_uyari_onarilan_sayfalari_sayiyla_bildirir():
    ref = _belge(_sayfa(1, BOZUK), _sayfa(2, BOZUK))
    ocr = _belge(_sayfa(1, SAGLAM), _sayfa(2, SAGLAM))
    out = birlestir(ref, ocr, {1, 2})
    assert any("2 sayfa tam-sayfa OCR" in u for u in out.parse_warnings)


# --- maliyet kapısı (adapter) -----------------------------------------------
# Tam-sayfa OCR dosya düzeyinde bir bayraktır: 2 bozuk sayfa için tüm dosya
# yeniden okunur. Ölçüldü (2026-08-09): kuyruktaki 22 dosya toplam OCR
# bütçesinin %58'ini yiyor ve bozuk içerikleri istisnasız kapak/künye.

class _SahteBackend:
    """Tam-sayfa OCR destekler ama çağrılırsa patlar — kapı geçildi mi kanıtı."""

    name = "sahte"
    supports_full_page_ocr = True

    def parse(self, *a, **kw):  # pragma: no cover - çağrılmamalı
        raise AssertionError("OCR kolu koşmamalıydı")


class _SahteDb:
    """Metrik yazımını yutar — kapı AÇILDIĞINDA koşan yolun DB'ye uzanması
    testin konusu değil; konu OCR kolunun gerçekten çağrılmış olması."""

    @contextmanager
    def connection(self):
        yield None


def _adapter(esik: float | None = None, *, db=None):
    from ragintel.config.loader import load_config
    from ragintel.ingestion.parsing import ParseAdapter

    env = ({"RAGINTEL_QUALITY_GLYPH_REPAIR": '{"min_broken_page_ratio":%r}' % esik}
           if esik is not None else None)
    cfg = load_config(db_reader=None, environ=env)
    return ParseAdapter(db=db, config=cfg, backend=_SahteBackend())


def _kapi_acildi(monkeypatch, esik=None) -> str:
    """Kapıyı geçen kol sahte backend'i çağırır; hata detaya yazılır."""
    from ragintel.ingestion.parsing import adapter as _ad

    monkeypatch.setattr(_ad, "insert_metric", lambda *a, **k: None)
    att = _seyrek_bozuk(100, 7) if esik == 0.0 else _seyrek_bozuk(10, 3)
    _final, glif = _adapter(esik, db=_SahteDb())._glif_onar(1, "x.pdf", "pdf", att, [])
    return glif["detail"]


def _seyrek_bozuk(n_sayfa: int, bozuk_no: int):
    """n_sayfa sayfalık, tek sayfası bozuk belge -> gerçek kuyruk profili."""
    from ragintel.ingestion.parsing.adapter import ParseAttempt

    pd = _belge(*[_sayfa(i, BOZUK if i == bozuk_no else SAGLAM)
                  for i in range(1, n_sayfa + 1)])
    return ParseAttempt(1, False, 5, metrics={}, parsed=pd)


def test_maliyet_kapisi_seyrek_bozulmada_onarimi_atlar():
    att = _seyrek_bozuk(100, 7)                      # oran 0.01 < 0.02
    final, glif = _adapter()._glif_onar(1, "x.pdf", "pdf", att, [])
    assert final is att                              # belge DEĞİŞMEDİ
    assert glif["finding"] == "encoding_broken"      # ama sessiz de geçilmedi
    assert "ATLANDI" in glif["detail"]
    assert "oran=0.0100" in glif["detail"]


def test_maliyet_kapisi_yogun_bozulmayi_gecirir(monkeypatch):
    """Eşik üstünde (oran 0.10) kapı açılır ve OCR kolu gerçekten koşar."""
    detay = _kapi_acildi(monkeypatch)
    assert "ATLANDI" not in detay
    assert "koşmamalıydı" in detay                   # sahte backend çağrıldı


def test_maliyet_kapisi_configten_okunur_kodda_sabit_yok():
    """Aynı belge (oran 0.01): eşik 0.5'te kapı kapalı — eşik kodda sabit değil."""
    att = _seyrek_bozuk(100, 7)
    assert "ATLANDI" in _adapter(0.5)._glif_onar(1, "x.pdf", "pdf", att, [])[1]["detail"]


def test_maliyet_kapisi_sifir_esikle_tamamen_kalkar(monkeypatch):
    """0.0 kapıyı kaldırır: oran 0.01 olan belge bile onarım koluna girer."""
    detay = _kapi_acildi(monkeypatch, esik=0.0)
    assert "ATLANDI" not in detay
    assert "koşmamalıydı" in detay

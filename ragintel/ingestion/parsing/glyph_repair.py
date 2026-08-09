"""Bozuk font kodlaması onarımı — sayfa düzeyinde tam-sayfa OCR birleştirmesi.

SORUN (2026-08-09'da ölçüldü, `scripts/karakter_envanteri_probe.py`): korpustaki
bazı PDF'lerin fontları subset'lenirken glifler yeniden indekslenmiş, sahte bir
`WinAnsiEncoding` beyanı kalmış ve doğru eşlemeyi taşıyan `ToUnicode` tablosu
düşürülmüş. Glif→Unicode bilgisi DOSYADA BULUNMUYOR; metin katmanı okunuyor ama
anlamsız ("7UNL\\H EDQNDFÕOÕN" = "Türkiye bankacılık"). İki aile gözlendi:

  A) sabit ASCII kaydırması (+0x1D) + dosyaya özgü ASCII-dışı glif tablosu
  B) Unicode öncesi Türkçe PostScript fontu (›=ı ‹=İ ¤=ğ fl=ş fi=Ş)

NEDEN OCR, NEDEN GLİF TABLOSU DEĞİL: kaydırma sabit ama tablo dosya başına
veriden türetilmek zorunda ve türetme ÖLÇÜLDÜ-ÇÜRÜDÜ — `konut_2`'de veriden
öğrenilen eşleme "temiz token %72.7→%100" derken metni `infla edilmekte` diye
kuruyordu; aynı satırı OCR `inşa edilmekte` yapıyor. Ayrıca üç ayrı pdf_backend
ve iki harici kütüphane (pymupdf, pypdfium2-doğrudan) aynı çöpü veriyor, yani
config/backend değişikliği bu aileleri kurtaramıyor.

NEDEN SAYFA DÜZEYİNDE: `force_full_page_ocr` DOSYA düzeyinde bir bayraktır ve
39 dosyanın 32'si yalnızca KISMEN bozuk (`60._Yilinda` %45.8, `Basel_II` %43.1,
`Bankacilik_Kanunu_2` %19.2). OCR'ın sağlam metne maliyeti ölçüldü: yoğun kanun
düzyazısında %12.4 ve **patlama** biçiminde (okunamaz token'ların %93.4'ü 5+
token'lık koşlarda; metnin %69.6'sı kesintisiz temiz). Yani sağlam sayfayı
OCR'a vermek gerçek kayıptır. Sayfa düzeyinde birleştirme bu kaybı SIFIRLAR:
bozuk sayfada alternatif zaten %100 kayıptır, sağlam sayfa hiç dokunulmadan
metin katmanından gelir.

Bu modül SAF'tır (DB/config/IO yok) — tetikleme ve kayıt ParseAdapter'ın işidir.
"""

from __future__ import annotations

from .parsed_document import Figure, Page, ParsedDocument, Section, Table
from .text_utils import detect_language

# Bölüm A envanterinde GÖZLENEN imza karakterleri (tahmin değil, sayımdan).
# İki aile karıştırılmamalı; birleşik küme yalnızca "bu sayfa bozuk mu"
# sorusu için kullanılır.
_IMZA_A = (
    "Õ"  # Õ   ı
    "ú"  # ú   ş
    "÷"  # ÷   ğ
    "ø"  # ø   İ
    "ù"  # ù   Ş
    "ඈ"  # (Sinhala) i
    "½"  # ½   ğ
    "¾"  # ¾   İ
    "¿"  # ¿   ı
    "À"
    "Á"
)
_IMZA_B = (
    "›"  # ›   ı
    "‹"  # ‹   İ
    "¤"  # ¤   ğ
)
IMZA = _IMZA_A + _IMZA_B


def imza_yogunlugu(metin: str) -> float:
    """1000 karakter başına imza karakteri sayısı."""
    n = len(metin)
    if n == 0:
        return 0.0
    return 1000.0 * sum(metin.count(c) for c in IMZA) / n


def bozuk_sayfalar(
    parsed: ParsedDocument, *, imza_bin: float = 10.0, min_karakter: int = 200
) -> set[int]:
    """Metin katmanı bozuk olan sayfa numaraları.

    ÖLÇÜT imza yoğunluğudur, Türkçe diyakritik yoğunluğu DEĞİL. Sebep: bozulma
    diyakritiği yok etmiş olabilir de olmayabilir de (aile-B diyakritiği yerine
    başka glif koyar, aile-A tamamen siler), ama imza karakteri her iki ailede
    de metinde DURUR. Eşik 10/1000 keyfi değil: Bölüm E'de gerçekten bozuk
    dosyalar 27..120 aralığında, metni sağlam olup az miktarda meşru imza
    taşıyanlar (OSMANLI 1.73, Catikkas 4.60) 5'in altında ölçüldü.

    `min_karakter` kısa sayfaları (kapak, boş sayfa, tek satırlık başlık)
    dışarıda bırakır — orada yoğunluk tek bir karakterle patlar.

    BİLİNEN KÖR NOKTA: kaynağında hiç Türkçe diyakritik olmayan bir sayfa
    aile-A ile bozulduğunda imza üretmez (yalnız kaydırılmış ASCII kalır) ve
    burada YAKALANMAZ. Türkçe mevzuat/bankacılık metninde diyakritiksiz tam
    sayfa gerçekçi değil; yine de eşik düşürülerek değil, ayrı bir ölçütle
    (işlev sözcüğü yoğunluğu) kapatılmalıdır — o ölçüt sayısal tablo
    sayfalarında yanlış pozitif verdiği için bilinçli olarak EKLENMEDİ.
    """
    out: set[int] = set()
    for p in parsed.pages:
        metin = p.text
        if len(metin) < min_karakter:
            continue
        if imza_yogunlugu(metin) >= imza_bin:
            out.add(p.page_no)
    return out


def _daha_iyi(aday: Page, mevcut: Page | None) -> bool:
    """OCR sayfası mevcut sayfanın yerini almayı hak ediyor mu?

    İki koşul: (1) anlamlı miktarda metin üretmiş olmalı — OCR bir sayfayı hiç
    okuyamadığında birkaç karakter döner ve o sayfayı almak metni SİLMEK olur;
    (2) imza yoğunluğu düşmüş olmalı — onarımın tanımı budur.
    """
    metin = aday.text
    if len(metin.strip()) < 40:
        return False
    if mevcut is None:
        return True
    eski = mevcut.text
    if len(metin) < 0.25 * len(eski):
        return False
    return imza_yogunlugu(metin) < imza_yogunlugu(eski)


def _sayfa_ofsetleri(sayfalar: list[Page]) -> dict[int, int]:
    """Her sayfanın `body_text` içindeki başlangıç ofseti.

    `body_text` sayfaları "\\n" ile birleştirdiği için ofset, önceki sayfa
    metinlerinin uzunlukları + ayraç sayısıdır.
    """
    ofset: dict[int, int] = {}
    imlec = 0
    for p in sayfalar:
        ofset[p.page_no] = imlec
        imlec += len(p.text) + 1
    return ofset


def birlestir(
    referans: ParsedDocument, ocr: ParsedDocument, ocr_sayfalar: set[int]
) -> ParsedDocument:
    """Sayfa bazında iki parse'ı birleştirir: `ocr_sayfalar` OCR'dan, kalanı referanstan.

    ŞEKİLLER HER ZAMAN REFERANSTAN gelir — şekil görüntüsü metin katmanından
    değil sayfanın piksellerinden üretilir, bozulmadan etkilenmez; iki koşumdan
    birini seçmek gereksiz fark yaratır.

    TABLOLAR sayfasıyla birlikte taşınır: bozuk sayfanın tablosu da bozuktur
    (TableFormer hücre metnini metin katmanından alır).

    `Section.char_span` YENİDEN HESAPLANIR — birleştirme metni kaydırdığı için
    kaynak parse'ın ofsetleri artık geçersizdir. Başlık birleşik metinde kendi
    sayfasının ofsetinden itibaren aranır; bulunamazsa span None bırakılır
    (kontrat izin veriyor) — yanlış bir aralık yazmaktansa boş bırakmak yeğdir.
    """
    yeni = ParsedDocument()
    ocr_sayfa = {p.page_no: p for p in ocr.pages}
    ref_sayfa = {p.page_no: p for p in referans.pages}

    # OCR yalnız bir sayfa aralığıyla koşulmuş olabilir; sayfa listesi
    # referanstan alınır ki hiçbir sayfa DÜŞMESİN.
    kullanilan: set[int] = set()
    reddedilen: list[int] = []
    for no in sorted(set(ref_sayfa) | set(ocr_sayfa)):
        aday = ocr_sayfa.get(no) if no in ocr_sayfalar else None
        kaynak = ref_sayfa.get(no)
        if aday is not None and _daha_iyi(aday, kaynak):
            kullanilan.add(no)
            kaynak = aday
        elif aday is not None:
            # DEĞİŞTİRME KURALI TEK YÖNLÜ: OCR bir sayfayı ancak DAHA İYİ
            # yapıyorsa alınır. OCR o sayfada boş/çöp döndüyse (motor sayfayı
            # okuyamadı, görüntü çıkmadı) bozuk metni bozuk metinle değiştirmek
            # kazanç değil, sessiz veri kaybıdır.
            reddedilen.append(no)
        if kaynak is None:
            continue
        yeni.pages.append(Page(page_no=no, text_blocks=list(kaynak.text_blocks)))
    if not yeni.pages:
        yeni.pages.append(Page(page_no=1, text_blocks=[]))

    tablolar: list[Table] = [t for t in referans.tables if t.page_no not in kullanilan]
    tablolar += [t for t in ocr.tables if t.page_no in kullanilan]
    tablolar.sort(key=lambda t: (t.page_no or 0,))
    yeni.tables = [
        Table(index=i, data=t.data, flattened_text=t.flattened_text,
              page_no=t.page_no, sheet_name=t.sheet_name)
        for i, t in enumerate(tablolar)
    ]

    yeni.figures = [
        Figure(index=f.index, page_no=f.page_no, caption=f.caption, image_png=f.image_png)
        for f in referans.figures
    ]

    govde = yeni.body_text
    ofset = _sayfa_ofsetleri(yeni.pages)
    bolumler = [s for s in referans.sections if s.page_start not in kullanilan]
    bolumler += [s for s in ocr.sections if s.page_start in kullanilan]
    bolumler.sort(key=lambda s: (s.page_start or 0,))
    for s in bolumler:
        bas = ofset.get(s.page_start or 0, 0)
        yer = govde.find(s.title, bas)
        yeni.sections.append(Section(
            title=s.title, level=s.level, page_start=s.page_start,
            char_span=(yer, yer + len(s.title)) if yer >= 0 else None,
        ))

    yeni.parse_warnings = list(referans.parse_warnings) + [
        f"ocr: {u}" for u in ocr.parse_warnings
    ]
    if kullanilan:
        yeni.warn(
            f"glif onarımı: {len(kullanilan)} sayfa tam-sayfa OCR'dan alındı "
            f"(sayfa: {sorted(kullanilan)[:20]}"
            f"{'...' if len(kullanilan) > 20 else ''})"
        )
    else:
        yeni.warn("glif onarımı: OCR sonucu hiçbir sayfaya uygulanamadı")
    if reddedilen:
        # Sessizce yutulmaz: bozuk kalan sayfalar SAYIYLA raporlanır, yoksa
        # "onarıldı" kaydı onarılmamış sayfaları da kapsıyormuş gibi okunur.
        yeni.warn(
            f"glif onarımı: {len(reddedilen)} sayfada OCR daha iyi değildi, "
            f"metin katmanı korundu (sayfa: {reddedilen[:20]}"
            f"{'...' if len(reddedilen) > 20 else ''})"
        )
    yeni.language = detect_language(yeni.body_text)
    return yeni

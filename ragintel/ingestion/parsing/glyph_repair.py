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


# Meşru düzen kontrol karakterleri: sayfa/satır/sekme ayraçları. Bunlar bozulma
# kanıtı DEĞİLDİR ve C0 ölçütünün dışında tutulur.
MESRU_KONTROL = "\t\n\r\x0b\x0c"


def imza_yogunlugu(metin: str) -> float:
    """1000 karakter başına imza karakteri sayısı."""
    n = len(metin)
    if n == 0:
        return 0.0
    return 1000.0 * sum(metin.count(c) for c in IMZA) / n


def c0_yogunlugu(metin: str) -> float:
    """1000 karakter başına ANLAMSIZ C0 kontrol karakteri sayısı.

    Aile-A'nın kaydırması (+0x1D) TÜM basılabilir aralığa uygulanır: boşluk
    (0x20) 0x03'e, 'T' (0x54) '7'ye düşer. Boşluk her metinde en sık karakter
    olduğundan, kaydırılmış bir sayfa imza karakteri hiç üretmese bile C0
    kontrol karakteri YAĞMURU üretir. İmza kolunun kör noktası tam olarak
    budur ve bu ölçüt onu kapatır.

    `MESRU_KONTROL` dışarıda: \\n\\t\\r ile \\x0B/\\x0C (dikey sekme, sayfa
    ayracı) her PDF'te meşru olarak bulunur.
    """
    n = len(metin)
    if n == 0:
        return 0.0
    k = sum(1 for c in metin if ord(c) < 0x20 and c not in MESRU_KONTROL)
    return 1000.0 * k / n


def bozukluk_yogunlugu(metin: str) -> float:
    """İki kolun toplamı — 'bu metin ne kadar bozuk' tek sayıda.

    `_daha_iyi` bunu kullanır: yalnız imzaya bakan bir karşılaştırma, aile-A
    ile bozulmuş diyakritiksiz bir sayfada 0 < 0 verir ve OCR'ın DOĞRU
    okumasını reddeder — yani C0 kolu tetiklese bile onarım uygulanmazdı.
    """
    return imza_yogunlugu(metin) + c0_yogunlugu(metin)


def tablo_metinleri(parsed: ParsedDocument) -> dict[int, str]:
    """Sayfa numarası -> o sayfanın tablolarının düzleştirilmiş metni.

    `page_no` taşımayan tablolar (xlsx sayfaları) dışarıda kalır; onların
    sayfa kavramı yoktur ve bu modül yalnız PDF sayfası için çağrılır.
    """
    kova: dict[int, list[str]] = {}
    for t in parsed.tables:
        if t.page_no is None or not t.flattened_text:
            continue
        kova.setdefault(t.page_no, []).append(t.flattened_text)
    return {no: "\n".join(v) for no, v in kova.items()}


def _birlesik(duzyazi: str, tablo: str) -> str:
    """Yalnız UZUNLUK kapıları için — yoğunluk ASLA birleşik metinde ölçülmez."""
    if not tablo:
        return duzyazi
    return f"{duzyazi}\n{tablo}" if duzyazi else tablo


def en_bozuk(*parcalar: str) -> float:
    """Parçaların EN YÜKSEK bozukluk yoğunluğu — toplamınki değil.

    SEYRELTME YASAĞI: düzyazı ile tabloyu birleştirip tek yoğunluk ölçmek
    yanlış NEGATİF üretir. 1000 karakterlik düzyazıda 50 C0 (yoğunluk 50)
    yanına 100.000 karakterlik temiz bir tablo gelirse birleşik yoğunluk
    0.495'e düşer ve 0.5 eşiğinin ALTINDA kalır — bugün yakalanan sayfa
    yarın kaçardı. Parçalar ayrı ölçülüp maksimum alınınca düzyazı kolu
    tabloların varlığından hiç etkilenmez; tablo kolu yalnızca EKLENİR.
    """
    return max((bozukluk_yogunlugu(p) for p in parcalar), default=0.0)


def bozuk_sayfalar(
    parsed: ParsedDocument, *, imza_bin: float = 10.0, c0_bin: float = 0.5,
    min_karakter: int = 200,
) -> set[int]:
    """Metin katmanı bozuk olan sayfa numaraları — İKİ ölçüt, VEYA'lı.

    SAYFANIN METNİ İKİ PARÇADIR: `Page.text` (düzyazı) ve O SAYFANIN TABLOLARI.
    `Page.text` yalnızca `text_blocks`tir; tablolar ParsedDocument'ta AYRI
    alandır. Yalnız düzyazıya bakmak ÖLÇÜLMÜŞ bir kör nokta üretiyordu
    (2026-08-10, `c0_tanim_probe` Bölüm E): korpusta C0 taşıyan 760 chunk'ın
    **760'ı** tablo kökenliydi, düzyazı 0 — tablo tabanı %21.2 iken. Mekanizma:
    `cleaner._strip_junk` düzyazıdaki tüm kategori-C karakterlerini siler,
    `cleaner.py:121` tabloları muaf tutar. Düzyazısı temiz / tablosu bozuk
    sayfa tespit DIŞINDA kalıyordu.

    İKİ PARÇA AYRI ÖLÇÜLÜR, BİRLEŞTİRİLMEZ (bkz. `en_bozuk`): birleştirmek
    büyük ve temiz bir tablonun bozuk düzyazıyı eşiğin altına seyreltmesine
    yol açar. Her parça kendi eşiğine karşı tartılır, sayfa herhangi biri
    tetiklerse işaretlenir.

    KOL-1 (imza): 1000 karakterde imza karakteri. Türkçe diyakritik yoğunluğu
    DEĞİL — bozulma diyakritiği yok etmiş olabilir de olmayabilir de (aile-B
    yerine başka glif koyar, aile-A tamamen siler), ama imza karakteri her iki
    ailede de metinde DURUR. Eşik 10/1000 keyfi değil: gerçekten bozuk dosyalar
    27..120, metni sağlam olup az miktarda meşru imza taşıyanlar (OSMANLI 1.73,
    Catikkas 4.60) 5'in altında ölçüldü.

    KOL-2 (C0): 1000 karakterde anlamsız kontrol karakteri. Eşik 0.5 VERİDEN
    seçildi (`scripts/glif_esik_probe.py`, 2026-08-10): 639 sayfalık temiz
    örneklemde 0.5'te YANLIŞ POZİTİF SIFIR, ve C0 üreten bozuk dosyada 231
    sayfanın 230'u yakalanıyor (%99.6). Daha yüksek her eşik yalnız kaybettirir
    (1.0'da 222, 10.0'da 129 sayfa). 0.0 vermek kolu KAPATIR — 'her sayfa
    bozuk' demek değil.

    Kol-2 kol-1'i kapsamaz: aynı ölçümde birlik 232 sayfa, tek başına C0 230 —
    yani imzanın tek başına yakaladığı 2 sayfa var. İki kol da gerekli.

    EŞİK YENİDEN ÖLÇÜLMEDİ ve gerekmiyor: ayrı ölçüm sayesinde DÜZYAZI KOLU
    BİT BİT ESKİSİYLE AYNI kalır — tabloların varlığı onun paydasına dokunmaz,
    yani 639 sayfalık temiz örneklemde ölçülen "yanlış pozitif sıfır" sonucu
    aynen geçerlidir. Tablo kolu yalnızca EKLENİR ve ancak tablo metninin
    KENDİSİ eşiği aşarsa tetikler.

    `min_karakter` HER PARÇAYA AYRI uygulanır: kısa metinde yoğunluk tek
    karakterle patlar ve bu tablo için de doğrudur. Kazanç şurada: düzyazısı
    iki satır olan bir tablo sayfası eskiden tümüyle eleniyordu, artık
    tablosu yeterince uzunsa değerlendirilir.

    KALAN KÖR NOKTA: kaynağında ne Türkçe diyakritik ne de boşluk bulunan bir
    sayfa (gerçekçi değil) hâlâ yakalanmaz. Ölçütün kapsamadığı üçüncü bir
    aile varsa bu iki kol onu da göstermez; kanıt gelmeden ölçüt eklenmez.
    """
    out: set[int] = set()
    tablo = tablo_metinleri(parsed)
    for p in parsed.pages:
        for metin in (p.text, tablo.get(p.page_no, "")):
            if len(metin) < min_karakter:
                continue
            if imza_yogunlugu(metin) >= imza_bin:
                out.add(p.page_no)
            elif c0_bin > 0.0 and c0_yogunlugu(metin) >= c0_bin:
                out.add(p.page_no)
    return out


def _daha_iyi(
    aday: Page, mevcut: Page | None, *, aday_tablo: str = "", mevcut_tablo: str = "",
) -> bool:
    """OCR sayfası mevcut sayfanın yerini almayı hak ediyor mu?

    İki koşul: (1) anlamlı miktarda metin üretmiş olmalı — OCR bir sayfayı hiç
    okuyamadığında birkaç karakter döner ve o sayfayı almak metni SİLMEK olur;
    (2) bozukluk yoğunluğu düşmüş olmalı — onarımın tanımı budur.

    (2) İMZA DEĞİL `bozukluk_yogunlugu`: aile-A ile bozulmuş diyakritiksiz bir
    sayfada imza yoğunluğu hem öncesinde hem sonrasında 0'dır; yalnız imzaya
    bakan karşılaştırma `0 < 0` verip OCR'ın doğru okumasını REDDEDER. O hâlde
    C0 kolu tetiklense bile onarım hiçbir sayfaya uygulanmazdı.

    KARŞILAŞTIRMA TABLOYU DA İÇERİR — `bozuk_sayfalar` ile AYNI parçalar
    üzerinden, ve aynı sebeple maksimumla (`en_bozuk`), toplamla değil. İkisi
    ayrışırsa kol kendi kendini iptal eder: tablosu yüzünden seçilen bir sayfa,
    düzyazısı zaten temiz olduğu için burada `0 < 0` ile reddedilir ve tespit
    genişlemesi hiçbir işe yaramaz.

    UZUNLUK kapıları ise birleşik metne bakar — onlar "OCR anlamlı hacimde
    metin üretti mi" sorusudur, yoğunluk sorusu değil; tablo ağırlıklı bir
    sayfa aksi hâlde "OCR boş döndü" sanılır.
    """
    metin = _birlesik(aday.text, aday_tablo)
    if len(metin.strip()) < 40:
        return False
    if mevcut is None:
        return True
    eski = _birlesik(mevcut.text, mevcut_tablo)
    if len(metin) < 0.25 * len(eski):
        return False
    return (en_bozuk(aday.text, aday_tablo)
            < en_bozuk(mevcut.text, mevcut_tablo))


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
    ref_tablo = tablo_metinleri(referans)
    ocr_tablo = tablo_metinleri(ocr)

    kullanilan: set[int] = set()
    reddedilen: list[int] = []
    for no in sorted(set(ref_sayfa) | set(ocr_sayfa)):
        aday = ocr_sayfa.get(no) if no in ocr_sayfalar else None
        kaynak = ref_sayfa.get(no)
        if aday is not None and _daha_iyi(
            aday, kaynak,
            aday_tablo=ocr_tablo.get(no, ""), mevcut_tablo=ref_tablo.get(no, ""),
        ):
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

"""Glif onarimini (Adim-4 uretim kodu) KORPUS UZERINDE dogrular. SALT-OKUNUR.

Neyi dogruluyor: `ragintel/ingestion/parsing/glyph_repair.py` + docling'in
`force_full_page_ocr` kolu birlikte, KISMEN bozuk bir dosyada bozuk sayfayi
OCR'dan alip saglam sayfayi metin katmaninda BIRAKIYOR mu? Bu soru masa
basinda cevaplanamaz: sayfa esleme (page_no), OCR'in bos donmesi ve
"daha iyi mi" kurali ancak gercek dosyada gorulur.

ONEMLI: bu prob URETIM FONKSIYONLARINI CAGIRIR, benzerini yeniden yazmaz.
`bozuk_sayfalar`/`birlestir` burada da orada da aynidir; prob gecerse
kanit uretim koduna aittir.

DB'ye/dosyaya/config'e YAZMAZ; yalnizca SELECT ve parse yapar. Reprocess
DEGILDIR -- korpus bu prob ile degismez.

  G bolumu (--dosya): tek dosyada uctan uca onarim + sayfa sayfa karar tablosu
  H bolumu (varsayilan): korpus geneli tetik envanteri + OCR butcesi (SQL, parse yok)
  H2 bolumu (--sinir N): kuyruktaki (az bozuk chunk'li) dosyalarin chunk METNI

Kullanim (H200, /opt/ragintel):
    python scripts/glif_onarim_probe.py
    python scripts/glif_onarim_probe.py --sinir 2
    python scripts/glif_onarim_probe.py --dosya 60._Yilinda_Turkiye_Bankalar_Birligi_2.pdf --sayfa 12
    python scripts/glif_onarim_probe.py --dosya Bankacilik_Kanunu_%2528Turkce%2529_2.pdf --sayfa 24 --bas-sayfa 10
"""

from __future__ import annotations

import argparse
import sys
import time

from ragintel.ingestion.parsing.glyph_repair import (
    IMZA,
    birlestir,
    bozuk_sayfalar,
    imza_yogunlugu,
)


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _parse_ayarlari(db):
    """URETIMDEKI parse ayarlari -- AYNI iki kaynaktan (bkz. adapter.py).

    figure_*/tableformer_mode/parse_num_threads -> config zinciri 'ingestion';
    pdf_backend -> ParsingSettings (ENV + kod). Karistirmak sessiz hatadir.
    `EffectiveConfig` nokta erisimi DESTEKLEMEZ; grup adiyla cagrilir.
    """
    from ragintel.config.loader import load_config
    from ragintel.config.settings import ParsingSettings
    from ragintel.database.config_store import make_db_reader

    cfg = load_config(db_reader=make_db_reader(db))
    return cfg.group("ingestion"), cfg.group("quality"), ParsingSettings()


# =============================================================== BOLUM H =====
# Tetik envanteri DEPOLANMIS chunk'lardan okunur (parse yok, saniyeler surer).
# Chunk-basina yogunluk sayfa-basina yogunlugun VEKILIDIR: uretim tetigi
# sayfada calisir, burada chunk'ta. Kismen bozuk dosyada dosya-geneli
# ortalama esigin ALTINDA kalir (bozuk pay seyrelir) -- bu yuzden asil olcut
# "esigi asan chunk var mi", dosya ortalamasi degil.
_SQL_TETIK = """
WITH c AS (
    SELECT c.file_id,
           length(c.chunk_text)                                             AS kar,
           length(c.chunk_text) - length(translate(c.chunk_text, %(imza)s, '')) AS im
    FROM core_chunks c
),
d AS (
    SELECT file_id,
           count(*)                                              AS chunk,
           sum(kar)                                              AS kar,
           sum(im)                                               AS im,
           count(*) FILTER (WHERE kar >= 200
                              AND 1000.0 * im / kar >= %(bin)s)   AS bozuk_chunk,
           sum(kar) FILTER (WHERE kar >= 200
                              AND 1000.0 * im / kar >= %(bin)s)   AS bozuk_kar,
           max(CASE WHEN kar >= 200 THEN 1000.0 * im / kar END)   AS en_yuksek
    FROM c GROUP BY file_id
),
p AS (
    SELECT DISTINCT ON (m.file_id) m.file_id,
           (m.detail->>'page_count')::int AS sayfa
    FROM metrics_ingestion m
    WHERE m.step = 'parse'
    ORDER BY m.file_id, m.metric_id DESC
)
SELECT f.file_name, d.chunk, d.bozuk_chunk, d.kar,
       coalesce(d.bozuk_kar, 0), coalesce(d.en_yuksek, 0),
       1000.0 * d.im / greatest(d.kar, 1), coalesce(p.sayfa, 0)
FROM d
JOIN core_files f USING (file_id)
LEFT JOIN p USING (file_id)
WHERE d.bozuk_chunk > 0
ORDER BY coalesce(d.bozuk_kar, 0) DESC;
"""


def bolum_h(db, bin_deger: float, sn_sayfa: float) -> int:
    print("=" * 100)
    print("BOLUM H  TETIK ENVANTERI + OCR BUTCESI -- hangi dosyalar onarim koluna girecek?")
    print("=" * 100)
    print(f"  esik: imza/1000 >= {bin_deger:.1f}   (quality.glyph_repair.signature_per_1k)")
    print(f"  imza kumesi: {len(IMZA)} karakter (uretim koduyla AYNI: glyph_repair.IMZA)")
    print("  KAYNAK depolanmis chunk'lar -- uretim tetigi SAYFADA calisir, bu")
    print("  yuzden asagidaki liste bir TAHMINDIR (yon: kucuk chunk'ta yogunluk")
    print("  sayfaya gore oynak). Kesin cevap G bolumunun sayfa tablosudur.\n")

    with db.connection() as conn:
        satirlar = conn.execute(_SQL_TETIK, {"imza": IMZA, "bin": bin_deger}).fetchall()
        toplam_kar, toplam_dosya = conn.execute(
            "SELECT coalesce(sum(length(chunk_text)), 0), count(DISTINCT file_id) "
            "FROM core_chunks;").fetchone()

    if not satirlar:
        print("  Esigi asan chunk YOK -> onarim kolu hicbir dosyada tetiklenmez.")
        return 0

    print(f"  {'dosya':<52} {'chunk':>6} {'bozuk':>6} {'%':>6} {'en_yuk':>7} "
          f"{'dosya_ort':>9} {'sayfa':>6}")
    for ad, chunk, bozuk, kar, bkar, enyuk, ort, sayfa in satirlar:
        pay = 100.0 * float(bkar) / max(float(kar), 1.0)
        print(f"  {ad[:52]:<52} {int(chunk):>6,} {int(bozuk):>6,} {pay:>5.1f}% "
              f"{float(enyuk):>7.1f} {float(ort):>9.2f} {int(sayfa):>6,}")

    n_dosya = len(satirlar)
    bozuk_kar = sum(int(r[4]) for r in satirlar)
    sayfa_top = sum(int(r[7]) for r in satirlar)
    eksik_sayfa = sum(1 for r in satirlar if int(r[7]) == 0)
    print(f"\n  tetiklenen dosya : {n_dosya:,} / {int(toplam_dosya):,}")
    print(f"  bozuk karakter   : {bozuk_kar:,} / {int(toplam_kar):,} "
          f"= %{100.0 * bozuk_kar / max(int(toplam_kar), 1):.1f}")

    print("\n" + "-" * 100)
    print("H1 OCR BUTCESI -- reprocess ne kadar uzar?")
    print("-" * 100)
    print("  MALIYET DOSYA DUZEYINDEDIR: `force_full_page_ocr` docling'de dosya")
    print("  bayragidir. Sayfa duzeyinde SECIM yapiyoruz ama sayfa duzeyinde")
    print("  PARSE etmiyoruz -- tetiklenen dosyanin TUM sayfalari OCR'dan gecer.")
    print(f"  Olculen hiz: ~{sn_sayfa:.2f} s/sayfa (216 DPI, H200; --sn-sayfa ile degistir)")
    if eksik_sayfa:
        print(f"  UYARI: {eksik_sayfa} dosyada page_count YOK (metrics_ingestion) -> "
              f"butce EKSIK hesaplaniyor.")
    ek = sayfa_top * sn_sayfa
    print(f"\n  ek OCR sayfasi   : {sayfa_top:,}")
    print(f"  ek sure          : {ek / 60:.0f} dk  ({ek / 3600:.2f} sa)")
    print("\n  Bu sure reprocess'in USTUNE binen EK yuktur; onarim kolu ancak")
    print("  bozuk sayfa bulununca acilir, diger dosyalar hic etkilenmez.")

    # H1b -- maliyet ile KAZANC ayni birimde degil: maliyet SAYFA (dosya
    # duzeyinde OCR), kazanc BOZUK KARAKTER. Ikisini yan yana koymadan
    # "esik konsun mu" sorusu cevaplanamaz.
    print("\n" + "-" * 100)
    print("H1b MALIYET/KAZANC AYRISMASI -- butce nereye gidiyor?")
    print("-" * 100)
    kovalar = (("10+ bozuk chunk", 10, 10 ** 9),
               ("3-9 bozuk chunk", 3, 9),
               ("1-2 bozuk chunk", 1, 2))
    print(f"  {'kova':<18} {'dosya':>6} {'sayfa':>7} {'sure':>8} "
          f"{'bozuk_kar':>12} {'kazanc payi':>12}")
    for ad, alt, ust in kovalar:
        grup = [r for r in satirlar if alt <= int(r[2]) <= ust]
        s = sum(int(r[7]) for r in grup)
        k = sum(int(r[4]) for r in grup)
        print(f"  {ad:<18} {len(grup):>6} {s:>7,} {s * sn_sayfa / 60:>6.0f} dk "
              f"{k:>12,} {100.0 * k / max(bozuk_kar, 1):>11.2f}%")
    print("\n  Kuyruk kovasi (1-2 chunk) butcenin buyuk kismini yiyip kazancin")
    print("  ihmal edilebilir kismini getiriyorsa `min_broken_pages` turu bir")
    print("  dosya-duzeyi kapisi tartisilir. Once H2: o chunk'lar GERCEKTEN")
    print("  bozuk mu, yoksa mesru imza mi? Karar metni gormeden verilmez.")
    return 0


# H2 -- sinir vakalarinin METNI. Sayi "bozuk chunk=2" der, ama o iki chunk
# gercek bozulma da olabilir tablo/kaynakca isaretlerinin yogunlastigi mesru
# bir parca da. Ayrimi yalnizca metin gosterir ([[on-veri-kontrolu-kural]]).
_SQL_SINIR = """
WITH c AS (
    SELECT c.file_id, c.chunk_index, c.chunk_text,
           length(c.chunk_text)                                             AS kar,
           length(c.chunk_text) - length(translate(c.chunk_text, %(imza)s, '')) AS im
    FROM core_chunks c
),
b AS (
    SELECT * FROM c WHERE kar >= 200 AND 1000.0 * im / kar >= %(bin)s
),
d AS (SELECT file_id, count(*) AS n FROM b GROUP BY file_id)
SELECT f.file_name, d.n, b.chunk_index, b.kar, 1000.0 * b.im / b.kar,
       substring(b.chunk_text from 1 for 240)
FROM b
JOIN d USING (file_id)
JOIN core_files f ON f.file_id = b.file_id
WHERE d.n <= %(tavan)s
ORDER BY d.n, f.file_name, b.chunk_index;
"""


def bolum_h2(db, bin_deger: float, tavan: int) -> int:
    print("=" * 100)
    print(f"BOLUM H2  SINIR VAKALARI -- bozuk chunk sayisi <= {tavan} olan dosyalarin METNI")
    print("=" * 100)
    print("  Soru: bu chunk'lar gercek bozulma mi, yoksa mesru imza yogunlasmasi mi?")
    print("  Gercekse kuyruk dosyalarini onarim disinda birakmak VERI KAYBIDIR;")
    print("  mesruysa butcenin yarisi bosa gidiyor demektir.\n")
    with db.connection() as conn:
        satirlar = conn.execute(
            _SQL_SINIR, {"imza": IMZA, "bin": bin_deger, "tavan": tavan}).fetchall()
    if not satirlar:
        print("  Sinir vakasi YOK.")
        return 0
    for ad, n, idx, kar, yog, metin in satirlar:
        gecen = sorted({ch for ch in IMZA if ch in metin})
        print(f"  --- {ad[:70]}  (dosyada {int(n)} bozuk chunk) ---")
        print(f"      chunk_index={int(idx)}  {int(kar):,} karakter  "
              f"imza/1000={float(yog):.1f}  gecen imza: "
              + " ".join(f"U+{ord(c):04X}" for c in gecen))
        for parca in (metin[:120], metin[120:240]):
            if parca.strip():
                print(f"      {parca}")
        print()
    print(f"  {len(satirlar)} sinir chunk basildi. Okuma kurali: metin Turkce ve")
    print("  okunabiliyorsa imza MESRUDUR (tablo cizgisi, kaynakca, tirnak);")
    print("  okunamiyorsa gercek bozulmadir ve dosya onarim koluna girmelidir.")
    return 0


# =============================================================== BOLUM G =====
def _sayfa_ozet(p) -> tuple[int, float]:
    m = p.text
    return len(m), imza_yogunlugu(m)


def _ornek_satirlar(metin: str, n: int = 4) -> list[str]:
    out = []
    for satir in metin.splitlines():
        s = satir.strip()
        if len(s) >= 45:
            out.append(s)
        if len(out) >= n:
            break
    return out


def bolum_g(db, dosya_adi: str, sayfa: int, bas_sayfa: int, bin_deger: float | None) -> int:
    print("=" * 100)
    print(f"BOLUM G  UCTAN UCA ONARIM -- {dosya_adi}")
    print("=" * 100)
    with db.connection() as conn:
        satir = conn.execute(
            "SELECT file_id, source_path, file_type FROM core_files "
            "WHERE file_name = %s ORDER BY file_id LIMIT 1;", (dosya_adi,)).fetchone()
    if satir is None:
        print(f"  HATA: core_files'ta '{dosya_adi}' yok.")
        return 1
    fid, yol, tur = satir
    if tur != "pdf":
        print("  Onarim kolu yalniz PDF'te calisir.")
        return 1

    from ragintel.ingestion.parsing import docling_backend as dbk

    ing, kal, ps = _parse_ayarlari(db)
    gr = kal.glyph_repair
    esik = float(gr.signature_per_1k) if bin_deger is None else bin_deger
    min_kar = int(gr.min_page_chars)
    print(f"  file_id={fid}  yol={yol}")
    print(f"  config: enabled={gr.enabled} signature_per_1k={gr.signature_per_1k} "
          f"min_page_chars={min_kar} max_retry={gr.max_retry}")
    if bin_deger is not None:
        print(f"  (esik --imza-bin ile {esik} yapildi -- config DEGISMEDI)")
    pencere = f"{bas_sayfa}-{bas_sayfa + sayfa - 1}" if sayfa else "TAM DOSYA"
    print(f"  sayfa penceresi: {pencere}")

    be = dbk.DoclingBackend(
        figure_images=False,
        figure_image_scale=float(ing.figure_image_scale),
        pdf_backend=str(ps.pdf_backend or "pypdfium2"),
        tableformer_mode=str(ing.tableformer_mode),
        parse_num_threads=int(ing.parse_num_threads),
    )
    if not getattr(be, "supports_full_page_ocr", False):
        print("  HATA: backend tam-sayfa OCR desteklemiyor -> onarim kolu KAPALI.")
        return 1

    def _cevir(conv):
        try:
            return (conv.convert(yol, page_range=(bas_sayfa, bas_sayfa + sayfa - 1))
                    if sayfa else conv.convert(yol))
        except TypeError:
            print("    (page_range desteklenmiyor -> tam dosya)", flush=True)
            return conv.convert(yol)

    # ------------------------------------------------------------------ G1
    print("\n" + "-" * 100)
    print("G1 REFERANS PARSE + TETIK -- bozuk_sayfalar() ne diyor?")
    print("-" * 100)
    t0 = time.perf_counter()
    ref = dbk._map_document(_cevir(be._converter(False)).document, ocr=False)
    t_ref = time.perf_counter() - t0
    bozuk = bozuk_sayfalar(ref, imza_bin=esik, min_karakter=min_kar)
    print(f"  referans: {len(ref.pages)} sayfa, {len(ref.body_text):,} karakter, "
          f"{t_ref:.1f}s")
    print(f"  bozuk sayfa: {len(bozuk)}/{len(ref.pages)}  {sorted(bozuk)[:25]}")
    if not bozuk:
        print("\n  Tetik ATESLENMEDI -> uretimde bu dosyaya dokunulmaz. Olcum burada biter.")
        print("  (Bu bir BASARISIZLIK degil; dosyanin metin katmani saglam demektir.)")
        return 0

    # ------------------------------------------------------------------ G2
    print("\n" + "-" * 100)
    print("G2 TAM-SAYFA OCR -- metin katmani YOK SAYILIYOR (yavas)")
    print("-" * 100)
    print("    (kosuyor...)", flush=True)
    t0 = time.perf_counter()
    ocr = dbk._map_document(_cevir(be._converter(True, True)).document,
                            ocr=True, full_page_ocr=True)
    t_ocr = time.perf_counter() - t0
    n = len(ref.pages) or 1
    print(f"  OCR: {len(ocr.pages)} sayfa, {len(ocr.body_text):,} karakter, {t_ocr:.1f}s "
          f"= {t_ocr / n:.2f} s/sayfa  (referansin {t_ocr / max(t_ref, 0.01):.1f} kati)")
    if not any("tam-sayfa OCR etkin" in u for u in ocr.parse_warnings):
        print("  UYARI: 'tam-sayfa OCR etkin' uyarisi YOK -> bayrak gecmemis olabilir.")

    # ------------------------------------------------------------------ G3
    print("\n" + "-" * 100)
    print("G3 SAYFA SAYFA KARAR -- hangi sayfa nereden geldi?")
    print("-" * 100)
    birlesik = birlestir(ref, ocr, bozuk)
    ref_s = {p.page_no: p for p in ref.pages}
    ocr_s = {p.page_no: p for p in ocr.pages}
    yeni_s = {p.page_no: p for p in birlesik.pages}
    print(f"  {'sayfa':>5} {'tetik':>6} | {'ref_kar':>8} {'ref_im':>7} | "
          f"{'ocr_kar':>8} {'ocr_im':>7} | {'KARAR':<12} {'son_im':>7}")
    degisen = korunan = reddedilen = 0
    for no in sorted(yeni_s):
        rk, ri = _sayfa_ozet(ref_s[no]) if no in ref_s else (0, 0.0)
        ok, oi = _sayfa_ozet(ocr_s[no]) if no in ocr_s else (0, 0.0)
        _sk, si = _sayfa_ozet(yeni_s[no])
        tetik = "BOZUK" if no in bozuk else "-"
        if no in bozuk and yeni_s[no].text == (ocr_s[no].text if no in ocr_s else None):
            karar, degisen = "OCR'DAN", degisen + 1
        elif no in bozuk:
            karar, reddedilen = "RED(ref kaldi)", reddedilen + 1
        else:
            karar, korunan = "referans", korunan + 1
        print(f"  {no:>5} {tetik:>6} | {rk:>8,} {ri:>7.1f} | {ok:>8,} {oi:>7.1f} | "
              f"{karar:<12} {si:>7.1f}")

    # ------------------------------------------------------------------ G4
    print("\n" + "-" * 100)
    print("G4 DOGRULAMA -- iddia degil, YENIDEN OLCUM")
    print("-" * 100)
    kalan = bozuk_sayfalar(birlesik, imza_bin=esik, min_karakter=min_kar)
    onarilan = len(bozuk) - len(kalan)
    print(f"  once bozuk : {len(bozuk)}   sonra bozuk: {len(kalan)}   "
          f"ONARILAN: {onarilan}")
    print(f"  sayfa sayisi korundu mu: {len(ref.pages)} -> {len(birlesik.pages)} "
          f"{'EVET' if len(birlesik.pages) >= len(ref.pages) else 'HAYIR -- SAYFA DUSTU'}")
    print(f"  karakter   : {len(ref.body_text):,} -> {len(birlesik.body_text):,}")
    print(f"  imza/1000  : {imza_yogunlugu(ref.body_text):.2f} -> "
          f"{imza_yogunlugu(birlesik.body_text):.2f}")
    print(f"  dil        : {ref.language} -> {birlesik.language}")
    print(f"  tablo      : {len(ref.tables)} -> {len(birlesik.tables)}  "
          f"(index 0..n araliksiz mi: "
          f"{'EVET' if [t.index for t in birlesik.tables] == list(range(len(birlesik.tables))) else 'HAYIR'})")
    print(f"  sekil      : {len(ref.figures)} -> {len(birlesik.figures)} "
          f"(referanstan gelmeli)")
    span_var = sum(1 for s in birlesik.sections if s.char_span is not None)
    span_dogru = sum(
        1 for s in birlesik.sections if s.char_span is not None
        and birlesik.body_text[s.char_span[0]:s.char_span[1]] == s.title)
    print(f"  bolum      : {len(birlesik.sections)}  span dolu {span_var}  "
          f"span METNE UYAN {span_dogru}"
          f"{'  <-- UYUSMAZLIK' if span_dogru != span_var else ''}")
    print("\n  uyarilar:")
    for u in birlesik.parse_warnings:
        if u.startswith("glif") or u.startswith("ocr:"):
            print(f"    - {u[:150]}")
    if onarilan <= 0:
        print("\n  ONARIM YOK -> uretimde birlestirme UYGULANMAZ, bulgu")
        print("  'encoding_broken' olarak yazilir (adapter._glif_onar).")
    else:
        print(f"\n  Uretimde bu dosya 'encoding_repaired' bulgusu alir: "
              f"onarilan={onarilan} kalan={len(kalan)}")

    # ------------------------------------------------------------------ G5
    print("\n" + "-" * 100)
    print("G5 GOZ DENETIMI -- sayi degil METIN; okunabilir mi?")
    print("-" * 100)
    ornek_no = sorted(bozuk)[:2]
    for no in ornek_no:
        print(f"\n  --- sayfa {no} (BOZUK olarak isaretlendi) ---")
        print("   ONCE (metin katmani):")
        for s in _ornek_satirlar(ref_s[no].text if no in ref_s else "", 3):
            print(f"     {s[:110]}")
        print("   SONRA (birlesik):")
        for s in _ornek_satirlar(yeni_s[no].text, 3):
            print(f"     {s[:110]}")
    saglam_no = [p.page_no for p in birlesik.pages if p.page_no not in bozuk][:1]
    for no in saglam_no:
        print(f"\n  --- sayfa {no} (tetiklenmedi -- DEGISMEMIS olmali) ---")
        ayni = no in ref_s and ref_s[no].text == yeni_s[no].text
        print(f"   metin birebir ayni mi: {'EVET' if ayni else 'HAYIR -- BEKLENMEDIK'}")
        for s in _ornek_satirlar(yeni_s[no].text, 2):
            print(f"     {s[:110]}")
    return 0


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dosya", metavar="DOSYA_ADI",
                    help="Bolum G: tek dosyada uctan uca onarim (parse eder, yavas)")
    ap.add_argument("--sayfa", type=int, default=0, metavar="N",
                    help="Bolum G: yalniz N sayfa (0 = tam dosya)")
    ap.add_argument("--bas-sayfa", type=int, default=1, dest="bas_sayfa", metavar="N",
                    help="Bolum G: pencere bu sayfadan baslar (onyuz temsili degildir)")
    ap.add_argument("--imza-bin", type=float, default=None, dest="imza_bin",
                    metavar="N", help="Esigi gecici olarak degistir (config'e DOKUNMAZ)")
    ap.add_argument("--sn-sayfa", type=float, default=1.0, dest="sn_sayfa",
                    metavar="N", help="Bolum H butcesi icin s/sayfa (olculen: 1.00)")
    ap.add_argument("--sinir", type=int, default=None, metavar="N",
                    help="Bolum H2: bozuk chunk sayisi <= N olan dosyalarin "
                         "chunk METNINI basar (kuyruk gercek mi mesru mu)")
    a = ap.parse_args()

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database

    db = Database(DbSettings()).open()
    esik = a.imza_bin if a.imza_bin is not None else 10.0
    try:
        if a.dosya:
            return bolum_g(db, a.dosya, max(0, a.sayfa), max(1, a.bas_sayfa), a.imza_bin)
        if a.sinir is not None:
            return bolum_h2(db, esik, max(1, a.sinir))
        return bolum_h(db, esik, a.sn_sayfa)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

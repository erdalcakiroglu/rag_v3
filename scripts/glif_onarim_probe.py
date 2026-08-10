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
  I bolumu (--tire-tarama N): DUZYAZIDAKI 0x02 -- parse-zamani ornek. DB'den
      olculemeyen tek olcum; _strip_junk kaniti temizlikte imha ediyordu.
  J bolumu (--kapsam): KESIN reprocess kapsami. imza VE C0 birlikte, tablo/
      duzyazi ayrimli, cikti = `ragintel ingest reprocess` icin file_id listesi.
      Bolum D (c0_tanim_probe) bunun yerini TUTMAZ: D yalniz C0 sayar, oysa
      imzayla bozulmus bir tablo hic C0 uretmeden bozuk olabilir.

DIKKAT -- H, TETIGIN KENDISINI OLCMEZ. H depolanmis chunk'lar uzerinden ve
YALNIZ imza olcutuyle calisan bir TAHMINDIR; uretim tetigi ise SAYFA uzerinde
ve iki kolla (imza + C0) calisir. "Yeni tablo-farkinda tetik hangi sayfalari
aciyor?" sorusunun cevabi YALNIZ G bolumundedir (--dosya). Korpus geneli sayfa
envanteri diye bir sey yok: 1118 PDF'in yeniden parse'i gerekirdi.

Kullanim (H200, /opt/ragintel):
    python scripts/glif_onarim_probe.py
    python scripts/glif_onarim_probe.py --sinir 2
    # ucuz: yalniz "tetik ne seciyor, tabloyu goruyor mu" -- OCR kosmaz
    python scripts/glif_onarim_probe.py --dosya 2bankakartlari_2.pdf --yalniz-tetik
    # pahali: uctan uca onarim + goz denetimi
    python scripts/glif_onarim_probe.py --dosya 60._Yilinda_Turkiye_Bankalar_Birligi_2.pdf --sayfa 12
"""

from __future__ import annotations

import argparse
import sys
import time

from ragintel.ingestion.parsing.glyph_repair import (
    IMZA,
    birlestir,
    bozuk_sayfalar,
    c0_yogunlugu,
    imza_yogunlugu,
    tablo_metinleri,
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
       substring(b.chunk_text from 1 for 240),
       -- imzalar TUM chunk'tan toplanir: yogunluk da oyle olcduldu, yalnizca
       -- basilan 240 karakteri taramak listeyi yaniltici sekilde bos gosterir.
       left(regexp_replace(b.chunk_text, '[^' || %(imza)s || ']', '', 'g'), 400)
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
    for ad, n, idx, kar, yog, metin, imzalar in satirlar:
        gecen = sorted(set(imzalar or ""))
        disarida = sorted({c for c in gecen if c not in metin})
        print(f"  --- {ad[:70]}  (dosyada {int(n)} bozuk chunk) ---")
        print(f"      chunk_index={int(idx)}  {int(kar):,} karakter  "
              f"imza/1000={float(yog):.1f}  gecen imza (tum chunk): "
              + " ".join(f"U+{ord(c):04X}" for c in gecen)
              + (f"   [basilan 240 karakter DISINDA: "
                 + " ".join(f"U+{ord(c):04X}" for c in disarida) + "]"
                 if disarida else ""))
        for parca in (metin[:120], metin[120:240]):
            if parca.strip():
                print(f"      {parca}")
        print()
    print(f"  {len(satirlar)} sinir chunk basildi. Okuma kurali: metin Turkce ve")
    print("  okunabiliyorsa imza MESRUDUR (tablo cizgisi, kaynakca, tirnak);")
    print("  okunamiyorsa gercek bozulmadir ve dosya onarim koluna girmelidir.")
    return 0


# =============================================================== BOLUM I =====
# DUZYAZIDAKI TIRE HASARI -- DB'den OLCULEMEZ, cunku kanit temizlikte imha
# edilir: `cleaning._strip_junk` 0x02'yi siler ve geriye "Su bat" kalir.
# core_chunks'ta gorunen 0x02'lerin hepsi TABLO kokenli olmasinin sebebi
# tablonun temizlikten muaf tutulmasidir (Bolum E), tablonun daha bozuk
# olmasi DEGIL. Yani "54 dosya" bir ALT SINIRDIR ve gercek yayginlik ancak
# PARSE ANINDA, temizlikten once olculebilir. Bu bolum tam olarak onu yapar.
#
# KATMAN SECIMI (2026-08-10 dersi): ilk kosumda `rastgele` 20 dosyanin 20'si
# de 1-2 sayfalik mevzuat_*.pdf cikti -- toplam ~24 sayfa, korpusun binde
# biri. Sonuc "0/20" idi ve HICBIR SEY olcmedi: hasar (Bolum J tire sutunu)
# kitaplarda yogunlasiyor, korpus ise 1119 dosyanin ~1050'si kucuk mevzuat.
# Duzgun rastgele ornek bile yanlis katmani cekiyor. Bu yuzden ornek KATMANLI
# secilir; varsayilan artik `tireli`.
_SQL_ORNEK = {
    # Depolanmis chunk'inda 0x02 olan dosyalar. Asil soru bunlarda:
    # "tabloda tire hasari VAR; ayni dosyanin DUZYAZISINDA da var mi?"
    "tireli": """
SELECT f.file_id, f.file_name, f.source_path
FROM core_files f
JOIN (SELECT file_id, count(*) AS n FROM core_chunks
      WHERE strpos(chunk_text, chr(2)) > 0 GROUP BY file_id) t USING (file_id)
WHERE f.file_type = 'pdf' AND f.status = 'COMPLETED'
ORDER BY t.n DESC
LIMIT %(n)s;
""",
    # En cok sayfali dosyalar = kitap katmani. Ayrica kitapta s/sayfa parse
    # maliyetini olcer (Bolum J butcesinin bilinmeyeni).
    "buyuk": """
SELECT f.file_id, f.file_name, f.source_path
FROM core_files f
JOIN (SELECT DISTINCT ON (file_id) file_id,
             (detail->>'page_count')::int AS s
      FROM metrics_ingestion WHERE step = 'parse'
      ORDER BY file_id, metric_id DESC) p USING (file_id)
WHERE f.file_type = 'pdf' AND f.status = 'COMPLETED'
ORDER BY p.s DESC NULLS LAST
LIMIT %(n)s;
""",
    # Kontrol grubu: hicbir seye gore secilmemis. Tek basina KANIT DEGIL --
    # korpus dagilimi yuzunden neredeyse hep mevzuat ceker.
    "rastgele": """
SELECT f.file_id, f.file_name, f.source_path
FROM core_files f
WHERE f.file_type = 'pdf' AND f.status = 'COMPLETED'
ORDER BY md5(f.file_name)
LIMIT %(n)s;
""",
}

_KELIME_DISI = " \t\n\r|.,;:!?()[]{}\"'«»…/\\"


def _kelimeler(metin: str, sinir: int = 12) -> list[str]:
    """0x02 gecislerinin URETECEGI kelimeler -- iddia degil, gercek cikti."""
    from ragintel.ingestion.cleaning.cleaner import onar_tire_glifi

    out = []
    for i, ch in enumerate(metin):
        if ch != "\x02":
            continue
        sol = i
        while sol > 0 and metin[sol - 1] not in _KELIME_DISI:
            sol -= 1
        sag = i + 1
        while sag < len(metin) and metin[sag] in " \t":
            sag += 1
        while sag < len(metin) and metin[sag] not in _KELIME_DISI:
            sag += 1
        out.append(onar_tire_glifi(metin[sol:sag])[0])
        if len(out) >= sinir:
            break
    return out


_KATMAN_ACIK = {
    "tireli": "depolanmis chunk'inda 0x02 olan dosyalar (en cok olandan aza)",
    "buyuk": "en cok sayfali dosyalar = kitap katmani (+ s/sayfa olcumu)",
    "rastgele": "md5(file_name) sirali kontrol grubu -- TEK BASINA KANIT DEGIL",
}


def bolum_i(db, n: int, katman: str) -> int:
    from ragintel.ingestion.cleaning.cleaner import onar_tire_glifi
    from ragintel.ingestion.parsing import docling_backend as dbk

    print("=" * 100)
    print(f"BOLUM I  DUZYAZIDAKI TIRE HASARI -- {n} dosya, katman={katman}")
    print("=" * 100)
    print("  NEDEN ornek: DB'de duzyazi 0x02'si YOK, cunku _strip_junk silmis.")
    print("  Kanit yalniz temizlikten ONCE, parse ciktisinda gorulebilir.")
    print(f"  katman: {_KATMAN_ACIK[katman]}")
    if katman == "rastgele":
        print("  UYARI: korpusun ~%94'u 1-2 sayfalik mevzuat_*.pdf. Rastgele")
        print("  ornek neredeyse hep o katmani ceker ve kitaplardaki hasari")
        print("  GOREMEZ. 'rastgele' sonucu tek basina 'hasar yok' demek DEGIL.\n")
    else:
        print("  Secim deterministik: ayni komut ayni dosyalari verir.\n")

    with db.connection() as conn:
        dosyalar = conn.execute(_SQL_ORNEK[katman], {"n": n}).fetchall()
    if not dosyalar:
        print("  Ornek bos -- olcut hicbir dosyayi secmedi.")
        return 1

    ing, _kal, ps = _parse_ayarlari(db)
    be = dbk.DoclingBackend(
        figure_images=False,
        figure_image_scale=float(ing.figure_image_scale),
        pdf_backend=str(ps.pdf_backend or "pypdfium2"),
        tableformer_mode=str(ing.tableformer_mode),
        parse_num_threads=int(ing.parse_num_threads),
    )
    conv = be._converter(False)

    print(f"  {'dosya':<40} {'sayfa':>5} {'dy_0x02':>8} {'tb_0x02':>8} "
          f"{'birles':>7} {'tire':>6} {'sn':>7} {'sn/syf':>7}")
    top_dy = top_tb = top_b = top_t = 0
    dy_dosya = tb_dosya = 0
    top_sayfa = 0
    top_sn = 0.0
    ilk = True
    ornek_kelime: list[str] = []
    for fid, ad, yol in dosyalar:
        t0 = time.perf_counter()
        try:
            pd = dbk._map_document(conv.convert(yol).document, ocr=False)
        except Exception as e:                                  # noqa: BLE001
            print(f"  {ad[:44]:<44} PARSE HATASI: {type(e).__name__}")
            continue
        gecen = time.perf_counter() - t0
        duzyazi = pd.body_text
        tablo = _tum_tablo_metni(pd)
        dy = duzyazi.count("\x02")
        tb = tablo.count("\x02")
        _m, b, t = onar_tire_glifi(duzyazi)
        _m2, b2, t2 = onar_tire_glifi(tablo)
        top_dy += dy
        top_tb += tb
        top_b += b + b2
        top_t += t + t2
        dy_dosya += 1 if dy else 0
        tb_dosya += 1 if tb else 0
        if dy and len(ornek_kelime) < 40:
            ornek_kelime.extend(_kelimeler(duzyazi, 6))
        sayfa = len(pd.pages)
        if not ilk:                      # ilk dosya model yuklemesini yutar
            top_sayfa += sayfa
            top_sn += gecen
        ilk = False
        print(f"  {ad[:40]:<40} {sayfa:>5,} {dy:>8,} {tb:>8,} "
              f"{b + b2:>7,} {t + t2:>6,} {gecen:>7.1f} "
              f"{gecen / max(sayfa, 1):>7.2f}")

    k = len(dosyalar)
    print(f"\n  ornek                 : {k} dosya (katman={katman})")
    print(f"  DUZYAZIDA 0x02 olan   : {dy_dosya}/{k} dosya, {top_dy:,} gecis")
    print(f"  TABLODA   0x02 olan   : {tb_dosya}/{k} dosya, {top_tb:,} gecis")
    print(f"  onarim                : {top_b:,} birlestirme + {top_t:,} tire")
    if top_dy:
        print(f"  duzyazi/tablo orani   : {top_dy / max(top_tb, 1):.2f}x")
    if top_sayfa:
        sps = top_sn / top_sayfa
        print(f"  PARSE MALIYETI        : {top_sayfa:,} sayfa / {top_sn:.1f} sn "
              f"= {sps:.2f} sn/sayfa  (ilk dosya haric -- model yuklemesi)")
        print(f"    -> Bolum J'nin 15,856 sayfalik tam kapsami ~{15856 * sps / 60:.0f} dk")
    print("\n  KARSILASTIRMA: DB'de (c0_tanim_probe Bolum A) 0x02 -> 529 chunk /"
          " 56 dosya,")
    print("  hepsi tablo kokenli -- ama bu tablonun daha bozuk oldugunu DEGIL,")
    print("  duzyazinin _strip_junk'ta silindigini gosterir. Ustteki dy_0x02")
    print("  sutunu sifirdan buyukse hasar duzyaziya da yayilmis demektir ve")
    print("  56 rakami bir ALT SINIRDIR. katman=tireli'de sifir cikarsa -- yani")
    print("  tabloda 0x02 TASIYAN dosyalarin duzyazisinda bile yoksa -- hasar")
    print("  gercekten tabloya ozgudur ve 56 kapsamin TAMAMIDIR.")
    if ornek_kelime:
        print("\n  duzyazidan uretilecek kelimeler (ilk 40):")
        for kel in ornek_kelime[:40]:
            print(f"    {kel!r}")
    return 0


# =============================================================== BOLUM J =====
# KESIN REPROCESS KAPSAMI. Bolum D (c0_tanim_probe) kapsami veremez, iki
# sebeple: (1) yalniz C0 sayar, oysa aile-A'nin imza kolu (Õ ¿ ÷ ...) C0
# uretmez -- imzayla bozulmus bir tablo D'de HIC gorunmez; (2) depolanmis
# chunk'lar onarim SONRASI durumdur. Burada iki olcut birlikte ve tablo/
# duzyazi ayrimiyla okunur, cikti dogrudan `ragintel ingest reprocess` icin
# file_id listesidir.
_SQL_KAPSAM = """
WITH c AS (
    SELECT c.file_id,
           (c.table_id IS NOT NULL) AS tablo,
           length(c.chunk_text) AS kar,
           length(c.chunk_text) - length(translate(c.chunk_text, %(imza)s, ''))
               AS im,
           length(c.chunk_text) - length(translate(c.chunk_text, %(kay)s, ''))
               AS kay,
           length(c.chunk_text) - length(translate(c.chunk_text, %(tire)s, ''))
               AS tire
    FROM core_chunks c
),
d AS (
    SELECT file_id,
           count(*) AS chunk,
           count(*) FILTER (WHERE kar >= 200 AND 1000.0 * im / kar >= %(bin)s)
               AS imza_chunk,
           count(*) FILTER (WHERE kar >= 200 AND 1000.0 * im / kar >= %(bin)s
                              AND tablo) AS imza_tablo,
           count(*) FILTER (WHERE kay > 0)              AS kay_chunk,
           count(*) FILTER (WHERE kay > 0 AND tablo)    AS kay_tablo,
           count(*) FILTER (WHERE tire > 0)             AS tire_chunk,
           sum(kar) FILTER (WHERE kar >= 200 AND 1000.0 * im / kar >= %(bin)s)
               AS bozuk_kar
    FROM c GROUP BY file_id
),
p AS (
    SELECT DISTINCT ON (m.file_id) m.file_id,
           (m.detail->>'page_count')::int AS sayfa
    FROM metrics_ingestion m
    WHERE m.step = 'parse'
    ORDER BY m.file_id, m.metric_id DESC
)
SELECT d.file_id, f.file_name, d.chunk, d.imza_chunk, d.imza_tablo,
       d.kay_chunk, d.kay_tablo, d.tire_chunk,
       coalesce(d.bozuk_kar, 0), coalesce(p.sayfa, 0)
FROM d
JOIN core_files f USING (file_id)
LEFT JOIN p USING (file_id)
WHERE d.imza_chunk > 0 OR d.kay_chunk > 0 OR d.tire_chunk > 0
ORDER BY (d.imza_chunk > 0 OR d.kay_chunk > 0) DESC,
         coalesce(d.bozuk_kar, 0) DESC;
"""

# Aile-A kaydirmasinin C0'a dusen parcasi. 0x02 (tire) ve 0x01 BILEREK disarida:
# 0x02 cleaning'de onariliyor, 0x01 olculmedi. 0x0B/0x0C mesru duzen karakteri.
_KAYDIRMA_KAR = "".join(chr(k) for k in list(range(0x03, 0x09)) + list(range(0x0E, 0x20)))


def bolum_j(db, bin_deger: float, sn_sayfa: float, kazanc: float = 0.98) -> int:
    print("=" * 100)
    print("BOLUM J  KESIN REPROCESS KAPSAMI -- hangi dosya, hangi tedavi?")
    print("=" * 100)
    print(f"  imza esigi: {bin_deger:.1f}/1000   kaydirma kumesi: 0x03-0x08,0x0E-0x1F")
    print("  Iki tedavi:")
    print("    OCR   -> imza VEYA kaydirma izi var; isaretli sayfalar OCR'a gider")
    print("    CLEAN -> yalniz 0x02 tire var; OCR kolu tetiklenmez (b7602f8)")
    print("  MALIYET MODELI: reprocess her iki kolda da dosyanin TAMAMINI parse")
    print("  eder -- bu kacinilmazdir ve baskin kalemdir. OCR yalnizca tetigin")
    print("  isaretledigi sayfalara EK olarak biner. Yani 'CLEAN ucuz' demek")
    print("  'OCR yok' demektir, 'bedava' demek degil.\n")

    with db.connection() as conn:
        satirlar = conn.execute(_SQL_KAPSAM, {
            "imza": IMZA, "kay": _KAYDIRMA_KAR, "tire": "\x02", "bin": bin_deger,
        }).fetchall()
    if not satirlar:
        print("  Kapsam BOS -- hicbir dosyada imza/kaydirma/tire izi yok.")
        return 0

    ocr, temiz = [], []
    for (fid, ad, _ch, imza, imza_tb, kay, kay_tb, tire, _bk, sayfa) in satirlar:
        kayit = (int(fid), ad, int(imza), int(imza_tb), int(kay), int(kay_tb),
                 int(tire), int(sayfa))
        (ocr if imza > 0 or kay > 0 else temiz).append(kayit)

    # OCR kolunu KAZANCA gore bol: bozuk chunk'larin `kazanc` payini tasiyan
    # dosyalar "YOGUN", geri kalan "KUYRUK". Kuyruk dosya basina 1-2 bozuk
    # chunk tasir ama yuzlerce sayfa parse ettirir -- bedeli kazancindan
    # buyuktur. Esik keyfi degil, kirilim noktasindan secildi (2026-08-10
    # kapsami): %95 -> 13 dosya/3310 sayfa, %98 -> 18/4522, %99 -> 26/6012.
    ocr.sort(key=lambda r: r[2] + r[4], reverse=True)
    top_bozuk = sum(r[2] + r[4] for r in ocr)
    kesme = kazanc * top_bozuk
    birikim = 0
    yogun_n = 0
    for r in ocr:
        if birikim >= kesme:
            break
        birikim += r[2] + r[4]
        yogun_n += 1
    yogun, kuyruk = ocr[:yogun_n], ocr[yogun_n:]

    print(f"  {'fid':>5} {'dosya':<46} {'imza':>5}/{'tb':<4} {'kay':>4}/{'tb':<4} "
          f"{'tire':>5} {'sayfa':>6}  TEDAVI")
    for etiket, grup in (("OCR-YOGUN", yogun), ("OCR-KUYRUK", kuyruk),
                         ("CLEAN", temiz)):
        for (fid, ad, imza, imza_tb, kay, kay_tb, tire, sayfa) in grup:
            print(f"  {fid:>5} {ad[:46]:<46} {imza:>5}/{imza_tb:<4} "
                  f"{kay:>4}/{kay_tb:<4} {tire:>5} {sayfa:>6,}  {etiket}")

    def _ozet(ad: str, grup: list, pay: str) -> tuple[int, float]:
        syf = sum(r[7] for r in grup)
        dk = syf * sn_sayfa / 60.0
        bz = sum(r[2] + r[4] for r in grup)
        print(f"  {ad:<12}: {len(grup):>3} dosya  {syf:>6,} sayfa  ~{dk:>4.0f} dk"
              f"   bozuk chunk {bz:>5,}  ({pay})")
        return syf, dk

    print()
    _, dk_y = _ozet("OCR-YOGUN", yogun,
                    f"kazancin %{100 * birikim / max(top_bozuk, 1):.1f}'i")
    _, dk_k = _ozet("OCR-KUYRUK", kuyruk,
                    f"kazancin %{100 * (top_bozuk - birikim) / max(top_bozuk, 1):.1f}'i")
    _, dk_c = _ozet("CLEAN", temiz, "imza/kaydirma YOK, yalniz 0x02")
    print(f"  {'TOPLAM':<12}: {len(satirlar):>3} dosya  "
          f"{sum(r[7] for r in ocr) + sum(r[7] for r in temiz):>6,} sayfa  "
          f"~{dk_y + dk_k + dk_c:>4.0f} dk")
    print(f"\n  [{sn_sayfa:.2f} sn/sayfa varsayimi -- KITAP katmaninda OLCULMEDI.")
    print("   G'de 1.45, kucuk mevzuat'ta 0.10 sn/sayfa cikti (14 kat fark).")
    print("   Gercek deger icin: --tire-tarama 6 --katman buyuk]")
    print("\n  'tb' sutunu = o bozuklugun TABLO kokenli olan payi. Buyuk olmasi")
    print("  dc99c78'in (tablo-farkinda tetik) neyi kurtardigini gosterir.")
    print("\n  --- calistirilacak komutlar (SALT ONERI; bu prob YAZMAZ) ---")
    for ad, grup, dk in (("1) OCR-YOGUN -- once bu", yogun, dk_y),
                         ("2) CLEAN", temiz, dk_c),
                         ("3) OCR-KUYRUK -- bedeli kazancindan buyuk, ISTEGE BAGLI",
                          kuyruk, dk_k)):
        print(f"  # {ad} ({len(grup)} dosya, ~{dk:.0f} dk)")
        print(f"  for i in {' '.join(str(r[0]) for r in grup)}; do "
              f"ragintel ingest reprocess $i; done\n")
    return 0


# =============================================================== BOLUM G =====
# TABLO-FARKINDA RAPORLAMA. Bu bolum bir sure yalniz `p.text` okuyordu ve
# uretim tetigiyle AYNI kor noktayi tasiyordu: c0_tanim_probe Bolum E'de
# olculdu ki C0 tasiyan 760 chunk'in 760'i (kaydirma tasiyan 241'in 241'i)
# TABLO kokenli, duzyazi 0. Yani duzyazi sutunlari temiz gorunurken bozukluk
# raporun disinda kaliyordu. Artik her olcum duzyazi/tablo AYRI verilir.
def _sayfa_ozet(p, tablo: str = "") -> tuple[int, float, float, int, float, float]:
    """(dy_kar, dy_imza, dy_c0, tb_kar, tb_imza, tb_c0) -- ASLA birlesik olcum.

    Birlestirip tek yogunluk olcmek buyuk temiz bir tablonun bozuk duzyaziyi
    (veya tersinin) esigin altina SEYRELTMESI demektir; uretimdeki `en_bozuk`
    de bu yuzden max alir, toplam degil.
    """
    m = p.text
    return (len(m), imza_yogunlugu(m), c0_yogunlugu(m),
            len(tablo), imza_yogunlugu(tablo), c0_yogunlugu(tablo))


def _neden(dy: str, tb: str, imza_bin: float, c0_bin: float, min_kar: int) -> str:
    """Sayfayi hangi PARCA secti? Tetigin kendi mantigini birebir tekrarlar."""
    etiket = []
    for ad, metin in (("duzyazi", dy), ("tablo", tb)):
        if len(metin) < min_kar:
            continue
        if imza_yogunlugu(metin) >= imza_bin:
            etiket.append(f"{ad}/imza")
        elif c0_bin > 0.0 and c0_yogunlugu(metin) >= c0_bin:
            etiket.append(f"{ad}/c0")
    return "+".join(etiket) if etiket else "-"


def _tum_tablo_metni(pd) -> str:
    return "\n".join(tablo_metinleri(pd).values())


def _ornek_satirlar(metin: str, n: int = 4, asgari: int = 45) -> list[str]:
    out = []
    for satir in metin.splitlines():
        s = satir.strip()
        if len(s) >= asgari:
            out.append(s)
        if len(out) >= n:
            break
    return out


def _goster(baslik: str, metin: str, n: int = 2, asgari: int = 45) -> None:
    """Kontrol karakterleri gorunmez oldugu icin <0xNN> olarak yazilir --
    aksi halde 'temiz gorunen' bir satir bozuk oldugunu gizler."""
    satirlar = _ornek_satirlar(metin, n, asgari)
    print(f"   {baslik}")
    if not satirlar:
        print("     (ornek satir yok)")
        return
    for s in satirlar:
        gorunur = "".join(c if ord(c) >= 0x20 or c in "\t" else f"<0x{ord(c):02X}>"
                          for c in s)
        print(f"     {gorunur[:130]}")


def bolum_g(db, dosya_adi: str, sayfa: int, bas_sayfa: int, bin_deger: float | None,
            yalniz_tetik: bool = False) -> int:
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
    c0_esik = float(getattr(gr, "control_per_1k", 0.0))
    min_kar = int(gr.min_page_chars)
    print(f"  file_id={fid}  yol={yol}")
    print(f"  config: enabled={gr.enabled} signature_per_1k={gr.signature_per_1k} "
          f"control_per_1k={c0_esik} min_page_chars={min_kar} max_retry={gr.max_retry}")
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
    bozuk = bozuk_sayfalar(ref, imza_bin=esik, c0_bin=c0_esik, min_karakter=min_kar)
    ref_tablo = tablo_metinleri(ref)
    print(f"  referans: {len(ref.pages)} sayfa, {len(ref.body_text):,} karakter, "
          f"{len(ref.tables)} tablo / {len(_tum_tablo_metni(ref)):,} tablo karakteri, "
          f"{t_ref:.1f}s")
    print(f"  bozuk sayfa: {len(bozuk)}/{len(ref.pages)}  {sorted(bozuk)[:25]}")

    # --- G1b ATIF: sayfayi DUZYAZI mi TABLO mu secti? -----------------------
    # Tablo kolunun tek basina acdigi sayfalar, dc99c78 oncesi tetigin
    # GORMEDIGI sayfalardir; bu satir o farkin kendisidir.
    yalniz_tablo = yalniz_duzyazi = ikisi = 0
    if bozuk:
        print(f"\n  {'sayfa':>5} | {'dy_kar':>7} {'dy_imza':>7} {'dy_c0':>6} | "
              f"{'tb_kar':>7} {'tb_imza':>7} {'tb_c0':>6} | SECEN")
        for p in ref.pages:
            if p.page_no not in bozuk:
                continue
            tb = ref_tablo.get(p.page_no, "")
            dk, di, dc, tk, ti, tc = _sayfa_ozet(p, tb)
            secen = _neden(p.text, tb, esik, c0_esik, min_kar)
            if secen.startswith("tablo") and "duzyazi" not in secen:
                yalniz_tablo += 1
            elif secen.startswith("duzyazi") and "tablo" not in secen:
                yalniz_duzyazi += 1
            else:
                ikisi += 1
            print(f"  {p.page_no:>5} | {dk:>7,} {di:>7.1f} {dc:>6.1f} | "
                  f"{tk:>7,} {ti:>7.1f} {tc:>6.1f} | {secen}")
        print(f"\n  YALNIZ TABLO yuzunden secilen : {yalniz_tablo}   "
              f"yalniz duzyazi: {yalniz_duzyazi}   ikisi: {ikisi}")
        print("  (yalniz-tablo sutunu = dc99c78 oncesi tetigin KACIRDIGI sayfalar)")

    if not bozuk:
        print("\n  Tetik ATESLENMEDI -> uretimde bu dosyaya dokunulmaz. Olcum burada biter.")
        print("  (Bu bir BASARISIZLIK degil; dosyanin metin katmani saglam demektir.)")
        return 0

    if yalniz_tetik:
        ilk, son = min(bozuk), max(bozuk)
        print("\n  --yalniz-tetik: OCR KOSULMADI (pahali kisim atlandi).")
        print(f"  Onerilen dar pencere:  --bas-sayfa {ilk} --sayfa {son - ilk + 1}")
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
    ocr_tablo = tablo_metinleri(ocr)
    yeni_tablo = tablo_metinleri(birlesik)
    # Bozukluk sutunlari en_bozuk mantigiyla: duzyazi/tablo AYRI, buyuk olan.
    print(f"  {'sayfa':>5} {'tetik':>6} | {'ref_dy':>7} {'ref_tb':>7} {'ref_koti':>8} | "
          f"{'ocr_dy':>7} {'ocr_tb':>7} {'ocr_koti':>8} | {'KARAR':<14} {'son_koti':>8}")
    degisen = korunan = reddedilen = 0

    def _koti(p, tablo: str) -> float:
        return max(imza_yogunlugu(p.text) + c0_yogunlugu(p.text),
                   imza_yogunlugu(tablo) + c0_yogunlugu(tablo))

    for no in sorted(yeni_s):
        rtb, otb, ytb = (ref_tablo.get(no, ""), ocr_tablo.get(no, ""),
                         yeni_tablo.get(no, ""))
        rk = len(ref_s[no].text) if no in ref_s else 0
        ok = len(ocr_s[no].text) if no in ocr_s else 0
        rkoti = _koti(ref_s[no], rtb) if no in ref_s else 0.0
        okoti = _koti(ocr_s[no], otb) if no in ocr_s else 0.0
        skoti = _koti(yeni_s[no], ytb)
        tetik = "BOZUK" if no in bozuk else "-"
        if no in bozuk and yeni_s[no].text == (ocr_s[no].text if no in ocr_s else None):
            karar, degisen = "OCR'DAN", degisen + 1
        elif no in bozuk:
            karar, reddedilen = "RED(ref kaldi)", reddedilen + 1
        else:
            karar, korunan = "referans", korunan + 1
        print(f"  {no:>5} {tetik:>6} | {rk:>7,} {len(rtb):>7,} {rkoti:>8.1f} | "
              f"{ok:>7,} {len(otb):>7,} {okoti:>8.1f} | {karar:<14} {skoti:>8.1f}")
    print(f"\n  OCR'dan alinan: {degisen}   reddedilen: {reddedilen}   "
          f"dokunulmayan: {korunan}")

    # ------------------------------------------------------------------ G4
    print("\n" + "-" * 100)
    print("G4 DOGRULAMA -- iddia degil, YENIDEN OLCUM")
    print("-" * 100)
    kalan = bozuk_sayfalar(birlesik, imza_bin=esik, c0_bin=c0_esik, min_karakter=min_kar)
    onarilan = len(bozuk) - len(kalan)
    print(f"  once bozuk : {len(bozuk)}   sonra bozuk: {len(kalan)}   "
          f"ONARILAN: {onarilan}")
    print(f"  sayfa sayisi korundu mu: {len(ref.pages)} -> {len(birlesik.pages)} "
          f"{'EVET' if len(birlesik.pages) >= len(ref.pages) else 'HAYIR -- SAYFA DUSTU'}")
    print(f"  karakter   : {len(ref.body_text):,} -> {len(birlesik.body_text):,}")
    print(f"  DUZYAZI    : imza/1000 {imza_yogunlugu(ref.body_text):.2f} -> "
          f"{imza_yogunlugu(birlesik.body_text):.2f}   "
          f"c0/1000 {c0_yogunlugu(ref.body_text):.2f} -> "
          f"{c0_yogunlugu(birlesik.body_text):.2f}")
    # ASIL SINAV: bu dosyada bozukluk duzyazida degil TABLODA (Bolum E).
    # Duzyazi satiri iyilesip bu satir iyilesmezse onarim SAHTEDIR.
    r_tb, y_tb = _tum_tablo_metni(ref), _tum_tablo_metni(birlesik)
    print(f"  TABLO      : imza/1000 {imza_yogunlugu(r_tb):.2f} -> "
          f"{imza_yogunlugu(y_tb):.2f}   "
          f"c0/1000 {c0_yogunlugu(r_tb):.2f} -> {c0_yogunlugu(y_tb):.2f}   "
          f"(karakter {len(r_tb):,} -> {len(y_tb):,})")
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
        secen = _neden(ref_s[no].text if no in ref_s else "",
                       ref_tablo.get(no, ""), esik, c0_esik, min_kar)
        print(f"\n  --- sayfa {no} (BOZUK; secen: {secen}) ---")
        _goster("DUZYAZI  ONCE :", ref_s[no].text if no in ref_s else "")
        _goster("DUZYAZI  SONRA:", yeni_s[no].text)
        # Bozukluk tabloda oldugu icin GOZ DENETIMININ ASIL YERI burasi.
        _goster("TABLO    ONCE :", ref_tablo.get(no, ""), asgari=20)
        _goster("TABLO    SONRA:", yeni_tablo.get(no, ""), asgari=20)
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
    ap.add_argument("--yalniz-tetik", action="store_true", dest="yalniz_tetik",
                    help="Bolum G: G1'den sonra DUR -- OCR kosturmaz (ucuz). "
                         "'Yeni tablo kolu bu dosyada sayfa aciyor mu?' sorusu "
                         "icin yeterlidir; pahali G2-G5 atlanir")
    ap.add_argument("--imza-bin", type=float, default=None, dest="imza_bin",
                    metavar="N", help="Esigi gecici olarak degistir (config'e DOKUNMAZ)")
    ap.add_argument("--sn-sayfa", type=float, default=1.0, dest="sn_sayfa",
                    metavar="N", help="Bolum H butcesi icin s/sayfa (olculen: 1.00)")
    ap.add_argument("--sinir", type=int, default=None, metavar="N",
                    help="Bolum H2: bozuk chunk sayisi <= N olan dosyalarin "
                         "chunk METNINI basar (kuyruk gercek mi mesru mu)")
    ap.add_argument("--tire-tarama", type=int, default=None, dest="tire_tarama",
                    metavar="N", help="Bolum I: N dosyayi PARSE edip temizlikten "
                                      "ONCE duzyazi/tablo 0x02 sayar (DB'den "
                                      "olculemeyen tek sey)")
    ap.add_argument("--katman", choices=("tireli", "buyuk", "rastgele"),
                    default="tireli",
                    help="Bolum I ornek katmani (varsayilan: tireli). Rastgele "
                         "ornek korpusun %%94'u kucuk mevzuat oldugu icin kitap "
                         "katmanindaki hasari GOREMEZ")
    ap.add_argument("--kapsam", action="store_true",
                    help="Bolum J: kesin reprocess kapsami (imza VE C0 birlikte, "
                         "tablo/duzyazi ayrimli) -- file_id listesi uretir")
    ap.add_argument("--kazanc-payi", type=float, default=0.98, dest="kazanc",
                    metavar="P", help="Bolum J: OCR kolunu YOGUN/KUYRUK ayiran "
                                      "kumulatif kazanc payi (varsayilan 0.98)")
    a = ap.parse_args()

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database

    db = Database(DbSettings()).open()
    esik = a.imza_bin if a.imza_bin is not None else 10.0
    try:
        if a.dosya:
            return bolum_g(db, a.dosya, max(0, a.sayfa), max(1, a.bas_sayfa),
                           a.imza_bin, a.yalniz_tetik)
        if a.tire_tarama is not None:
            return bolum_i(db, max(1, a.tire_tarama), a.katman)
        if a.kapsam:
            return bolum_j(db, esik, a.sn_sayfa, min(max(a.kazanc, 0.0), 1.0))
        if a.sinir is not None:
            return bolum_h2(db, esik, max(1, a.sinir))
        return bolum_h(db, esik, a.sn_sayfa)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

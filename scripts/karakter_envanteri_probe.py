#!/usr/bin/env python
"""Sasirtici karakter envanteri + ayrik-harf kaynak teshisi -- SALT-OKUMA.

karakter_bozulmasi_probe.py bir kusur BULDU ama iki eksigi acikca gosterdi:

  1) Desen kumem EKSIK. S4 ornekleri benim listemde OLMAYAN karakterler
     gosterdi (O-tilde, ters-soru, A-grave) ve "kesir" dedigim desen aslinda
     kesir DEGILDI: "Birli 1/2 i" = "Birligi", "B 3/4 R 3/4 NC 3/4" = "BIRINCI".
     Yani 1/2 ve 3/4 burada birer YERINE-GECMIS Turkce harf. Ustelik S6'nin en
     kotu dosyasi (60._Yilinda..., 1.26/1000, korpus medyani 71.8) hicbir
     desenin ilk-10'unda YOK -- diyakritikleri hic yakalamadigim bir karaktere
     donusmus olmali.
     => Onarim tablosu benim TAHMINIMDEN degil, VERIDEN kurulmali.

  2) Ayrik-harf ("bankac i l i k") ONARILABILIR MI, bilinmiyor. Temizleme
     asamasi (cleaner.py `_INLINE_WS`) ardisik bosluklari TEKE indiriyor.
     Eger docling "Yabanc i  Bankalar" (iki bosluk = gercek kelime siniri) ile
     "Bankalar i n" (tek bosluk = kelime ici) ayrimini uretiyorsa, o ayrim
     temizlemede YOK EDILIYOR demektir ve onarim parse ciktisinda yapilmalidir.
     Uretmiyorsa bilgi zaten PDF'te yoktur ve mekanik onarim IMKANSIZDIR.
     => Bu, veritabanindan yanitlanamaz; dosyayi yeniden parse etmek gerekir.

BOLUM A (varsayilan, yalniz SELECT):
  S1 Sasirtici karakter envanteri -- ASCII ve Turkce harf DISINDAKI her karakter,
     sayisi ve kac dosyada gectigi. Regex YOK: translate() ile beklenen karakterler
     silinir, kalan dogrudan sayilir (collation ve kacis riski sifir).
  S2 Her sasirtici karakterin GERCEK baglamlari -- yerine gectigi harf buradan
     okunur. Mesru olanlar (tirnak, tire, madde imi) da burada elenir.
  S3 Dosya AILELERI -- en dusuk Turkce-yogunluklu dosyalarda hangi karakterler
     baskin? Tek onarim tablosu mu yeter, dosya basina ayri mi gerekir?
  S4 Ayrik-harf anatomisi -- hangi harf, ne siklikta ve ardindan gelen kelime
     BUYUK harfle mi basliyor (yeni kelime -> bosluk KALMALI) yoksa kucuk harfle
     mi (kelime ici -> bosluk SILINMELI)? Sezgisel onarimin tavanini olcer.
  S5 Aile-A KAPSAMI -- kac dosya etkilenmis? S3 en kotu 20'yi gosterir ama sayiyi
     gostermez; onarim yazma karari tam da o sayiya baglidir. Diyakritik yogunlugu
     kovalari + aile imzasiyla dogrulama (dusuk yogunluk tek basina "bozuk" DEMEK
     DEGILDIR -- Ingilizce bir belge de dusuk cikar).

BOLUM B (--parse <dosya_adi>): ayrik-harf kaynak teshisi.
  Dosyayi URETIM ayarlariyla yeniden parse eder, HAM parse ciktisinda ayrik-harf
  cevresindeki bosluk sayilarini sayar, sonra ayni metni clean_document'ten
  gecirip tekrar sayar. Hicbir sey yazmaz. Kiyas noktasi ayni dosyanin
  DEPOLANMIS chunk'larindaki sayimdir -- o olmadan sonuc yorumlanamaz.
  `--sayfa N` ile baska dosyalar bir dakikada yoklanabilir; sayfa siniri
  varken kiyas MUTLAK sayi degil 1000 karakter basina yogunluk uzerinden yapilir.

BOLUM C (--backend <dosya_adi>): YERINE-GECMIS HARF ailesinin teshisi.
  Bolum A'nin S2 baglamlari bu ailenin bir KAYDIRMA oldugunu gosterdi:
  "%DQNDODU" -> "Bankalar" (her ASCII karakter +0x1D). Bundan iki soru dogar ve
  ikisi de yalniz yeniden parse ile yanitlanir:

    C0  Temizleme ONCESI karakter envanteri. DB'deki envanter clean SONRASIDIR;
        ftfy.fix_text kontrol karakterlerini siler. Kaydirmada kucuk 'u-umlaut'
        ve RAKAMLAR C0/C1 araligina dusuyor olabilir -- oyleyse temizleme onlari
        yok etmistir ve DB'den GORULEMEZ. Kayip burada olculur.
    C1  Baska bir PDF alt-parseri dogru coz-uyor mu? Coziyorsa onarim bir KOD
        isi degil CONFIG isidir (parse.pdf_backend) -- cok daha ucuz ve guvenli.
    C2  Cozmuyorsa: kaydirma miktari VERIDEN aranir (1..63 taranir, Turkce islev
        sozcugu kazanci en yuksek olan secilir). Benim tahminim degil, olcum.
    C5  Kaydirma sonrasi bir CAKISMA kaliyor mu? C4 ayni belgede hem "olarak"
        (o = o) hem "ioin" (o = c) gosterdi. Tek bir kod noktasi iki harfe
        karsilik geliyorsa tablo IMKANSIZDIR. Ama cozucu 1:1 konum korudugu icin
        her cozulmus karakterin ham kaynagi bilinir: cozulmus 'o' ya pencere-ici
        bir ham kod noktasindan (KODLANMIS akis) ya da pencere-disi gercek bir
        'o'dan (DUZ akis) gelir. Ikisi ayri akissa cakisma yoktur ve tablo ham
        kod noktasi uzerinde KAYIPSIZ kurulur. C5 bunu olcer.

  Olcut, diyakritik yogunlugu DEGIL -- o bozulmadan etkileniyor. Diyakritigi
  OLMAYAN Turkce islev sozcukleri (ve/bir/bu/ile/olan) kullanilir; bozuk metinde
  sifira yakin, dogru cozulmus metinde yuksek cikarlar.

KOSUM (H200, venv + .env.h200 yuklu):
    python scripts/karakter_envanteri_probe.py
    python scripts/karakter_envanteri_probe.py --parse 261_2.pdf
    python scripts/karakter_envanteri_probe.py --backend 60._Yilinda_Turkiye_Bankalar_Birligi_2.pdf
"""

from __future__ import annotations

import argparse
import re
import sys


def _c(*kod_noktalari: int) -> str:
    """Kod noktalarindan dize kurar. Kaynagin ASCII kalmasi icin TEK yol budur."""
    return "".join(chr(k) for k in kod_noktalari)


# --- Karakter kumeleri (hepsi kod noktasindan; kaynak ASCII kalir) -----------
_TR_KUCUK = _c(0x0131, 0x00E7, 0x011F, 0x00F6, 0x015F, 0x00FC)   # i c g o s u
_TR_BUYUK = _c(0x0130, 0x00C7, 0x011E, 0x00D6, 0x015E, 0x00DC)   # I C G O S U
_TR = _TR_KUCUK + _TR_BUYUK

_ASCII_KUCUK = _c(*range(0x61, 0x7B))          # a-z  (aralik DEGIL, sayim)
_ASCII_BUYUK = _c(*range(0x41, 0x5B))          # A-Z
_KUCUK = _ASCII_KUCUK + _TR_KUCUK
_BUYUK = _ASCII_BUYUK + _TR_BUYUK

# "Beklenen" kume: yazdirilabilir ASCII + bosluk karakterleri + Turkce harfler.
# Bunlarin DISINDA kalan her sey sasirticidir -- mesru olanlari S2 eler.
_BEKLENEN = _c(0x09, 0x0A, 0x0D) + _c(*range(0x20, 0x7F)) + _TR

# [[:alpha:]] lc_ctype'a baglidir ve C locale'de Turkce harfi harf saymayabilir;
# bu yuzden Turkce harfler sinifa ELLE eklenir.
_ALPHA = "[[:alpha:]" + _TR + "]"

_SQL_ENVANTER = """
SELECT ch, count(*) AS n, count(DISTINCT file_id) AS dosya
FROM (
    SELECT c.file_id,
           regexp_split_to_table(translate(c.chunk_text, %(beklenen)s, ''), '') AS ch
    FROM core_chunks c
) t
WHERE ch <> ''
GROUP BY ch
ORDER BY n DESC;
"""

_SQL_BAGLAM = """
SELECT f.file_name,
       substring(c.chunk_text from greatest(1, strpos(c.chunk_text, %(ch)s) - 30)
                 for 64)
FROM core_chunks c
JOIN core_files f USING (file_id)
WHERE strpos(c.chunk_text, %(ch)s) > 0
LIMIT %(n)s;
"""

_SQL_TR_YOGUNLUK = """
SELECT c.file_id, f.file_name,
       sum(length(c.chunk_text))                       AS kar,
       sum(regexp_count(c.chunk_text, %(tr)s))         AS tr
FROM core_chunks c
JOIN core_files f USING (file_id)
GROUP BY c.file_id, f.file_name;
"""

_SQL_DOSYA_ENVANTER = """
SELECT file_id, ch, count(*) AS n
FROM (
    SELECT c.file_id,
           regexp_split_to_table(translate(c.chunk_text, %(beklenen)s, ''), '') AS ch
    FROM core_chunks c
    WHERE c.file_id = ANY(%(ids)s)
) t
WHERE ch <> ''
GROUP BY file_id, ch;
"""


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _ad(ch: str) -> str:
    """Karakteri ASCII-guvenli goster: kod noktasi + Unicode adi."""
    import unicodedata
    try:
        isim = unicodedata.name(ch)
    except ValueError:
        isim = "(adsiz)"
    return f"U+{ord(ch):04X} {isim[:38]}"


def _gorunur(s: str) -> str:
    """Bosluk VE kontrol karakterlerini GORUNUR kilar.

    Kontrol karakterlerini basmak sart: aile-A'da yerine-gecmis harflerin bir
    kismi C1 araliginda (U+0080/0081/0087...) ve terminalde hicbir iz birakmiyor.
    Ilk C3 kosumunda "Trkiye" diye gorunen dizide 'u-umlaut'un nerede oldugu
    okunamadi -- baglam ornekleri bu yuzden ise yaramadi.
    """
    out = []
    for ch in s:
        k = ord(ch)
        if ch == " ":
            out.append(_c(0x00B7))
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif k < 0x20 or 0x7F <= k <= 0x9F:
            out.append(f"<{k:02X}>")
        else:
            out.append(ch)
    return "".join(out)


def _parse_ayarlari(db):
    """URETIMDEKI parse ayarlarini AYNI kaynaklardan okur (bkz. adapter.py).

    Iki ayri kaynak var ve karistirmak sessiz hataya yol acar:
      - figure_*/tableformer_mode/parse_num_threads -> config zinciri, 'ingestion' grubu
      - backend / pdf_backend                       -> ParsingSettings (ENV + kod)
    `EffectiveConfig` nokta erisimi DESTEKLEMEZ; grup adiyla cagrilir.
    """
    from ragintel.config.loader import load_config
    from ragintel.config.settings import ParsingSettings
    from ragintel.database.config_store import make_db_reader

    ing = load_config(db_reader=make_db_reader(db)).group("ingestion")
    return ing, ParsingSettings()


# =============================================================== BOLUM A =====
def bolum_a(db) -> int:
    print("=" * 100)
    print("S1 SASIRTICI KARAKTER ENVANTERI (ASCII + Turkce harf DISINDAKI her sey)")
    print("=" * 100)
    with db.connection() as conn:
        print("  ... korpus taraniyor (translate + split, regex yok)", flush=True)
        envanter = conn.execute(_SQL_ENVANTER, {"beklenen": _BEKLENEN}).fetchall()
        yogunluk = conn.execute(_SQL_TR_YOGUNLUK, {"tr": "[" + _TR + "]"}).fetchall()

    if not envanter:
        print("  Sasirtici karakter YOK -- korpus tumuyle ASCII + Turkce.")
    toplam = sum(int(n) for _ch, n, _d in envanter)
    print(f"  farkli sasirtici karakter: {len(envanter)}   "
          f"toplam gecis: {toplam:,}\n")
    print(f"  {'karakter':<46} {'adet':>10} {'dosya':>7}")
    for ch, n, dosya in envanter[:45]:
        print(f"  {_ad(ch):<46} {int(n):>10,} {int(dosya):>7}")
    if len(envanter) > 45:
        print(f"  ... ve {len(envanter) - 45} karakter daha")

    # --------------------------------------------------------------- S2
    print("\n" + "=" * 100)
    print("S2 BAGLAMLAR -- yerine gectigi harf BURADAN okunur")
    print("=" * 100)
    print("  Nokta (.) yerine ORTA NOKTA basilan yerler BOSLUKTUR.")
    print("  Mesru karakterler (tirnak, uzun tire, madde imi) burada elenir.\n")
    with db.connection() as conn:
        for ch, n, dosya in envanter[:30]:
            print(f"  --- {_ad(ch)}  ({int(n):,} gecis / {int(dosya)} dosya) ---")
            for f_ad, parca in conn.execute(
                    _SQL_BAGLAM, {"ch": ch, "n": 3}).fetchall():
                tek = " ".join((parca or "").split())
                print(f"      {f_ad[:26]:<26} {_gorunur(tek)}")

    # --------------------------------------------------------------- S3
    print("\n" + "=" * 100)
    print("S3 DOSYA AILELERI -- en dusuk Turkce-yogunluklu 20 dosyada ne baskin?")
    print("=" * 100)
    print("  Tek bir onarim tablosu mu yeter, yoksa dosya basina ayri mi gerekir?")
    print("  Ayni karakter kumesi tekrar ediyorsa -> TEK tablo.\n")
    yog = sorted(((int(fid), ad, int(kar), int(tr or 0))
                  for fid, ad, kar, tr in yogunluk if kar),
                 key=lambda r: r[3] / r[2])[:20]
    ids = [r[0] for r in yog]
    with db.connection() as conn:
        satirlar = conn.execute(_SQL_DOSYA_ENVANTER,
                                {"beklenen": _BEKLENEN, "ids": ids}).fetchall()
    per: dict[int, list[tuple[str, int]]] = {}
    for fid, ch, n in satirlar:
        per.setdefault(int(fid), []).append((ch, int(n)))
    for fid, ad, kar, tr in yog:
        ust = sorted(per.get(fid, []), key=lambda x: -x[1])[:6]
        imza = "  ".join(f"U+{ord(c):04X}x{n}" for c, n in ust) or "(yok)"
        print(f"  {ad[:40]:<40} tr/1000={1000*tr/kar:6.2f}  {imza}")

    # --------------------------------------------------------------- S4
    print("\n" + "=" * 100)
    print("S4 AYRIK-HARF ANATOMISI -- sezgisel onarimin TAVANI")
    print("=" * 100)
    print("  Kural adayi: <harf> <bosluk> <tek Turkce harf> <bosluk> <X>")
    print("    X kucuk harfle basliyorsa  -> kelime ici,   bosluklar SILINMELI")
    print("       ('bankac i l i k'  -> 'bankacilik')")
    print("    X buyuk harfle basliyorsa  -> yeni kelime,  sagdaki bosluk KALMALI")
    print("       ('Yabanc i Bankalar' -> 'Yabanci Bankalar')")
    print("    X harf degilse             -> KARARSIZ (ornekle bakilmali)\n")
    with db.connection() as conn:
        print(f"  {'harf':<26} {'kucuk-izler':>12} {'buyuk-izler':>12} "
              f"{'digerleri':>11} {'toplam':>10}")
        gt = [0, 0, 0]
        for harf in _TR:
            sol = _ALPHA + " " + harf + " "
            sayilar = []
            # Sag taraf ILERI-BAKIS ile sinanir, TUKETILMEZ. Tuketilseydi
            # "bankac i l i k"te ilk eslesme 'l'yi yutar, ikinci ayrik harf
            # sayilmaz ve alt kovalar toplami `tum`u tutmaz -- fark sessizce
            # "digerleri"ne yazilirdi.
            for sag in ("(?=[" + _KUCUK + "])", "(?=[" + _BUYUK + "])"):
                sayilar.append(int(conn.execute(
                    "SELECT coalesce(sum(regexp_count(chunk_text, %s)), 0) "
                    "FROM core_chunks;", (sol + sag,)).fetchone()[0]))
            tum = int(conn.execute(
                "SELECT coalesce(sum(regexp_count(chunk_text, %s)), 0) "
                "FROM core_chunks;", (sol,)).fetchone()[0])
            diger = tum - sayilar[0] - sayilar[1]
            gt[0] += sayilar[0]; gt[1] += sayilar[1]; gt[2] += diger
            if tum:
                print(f"  {_ad(harf)[:26]:<26} {sayilar[0]:>12,} {sayilar[1]:>12,} "
                      f"{diger:>11,} {tum:>10,}")
        top = sum(gt) or 1
        print(f"\n  {'TOPLAM':<26} {gt[0]:>12,} {gt[1]:>12,} {gt[2]:>11,} {top:>10,}")
        print(f"  {'oran':<26} {100*gt[0]/top:>11.1f}% {100*gt[1]/top:>11.1f}% "
              f"{100*gt[2]/top:>10.1f}%")
        print("\n  -> kucuk+buyuk orani, sezgisel kuralin KARARLI olarak")
        print("     onarabilecegi paydir. 'digerleri' elle bakilacak artiktir.")

        print("\n  --- 'digerleri' kovasindan ornekler (kararsiz durumlar) ---")
        desen = _ALPHA + " [" + _TR + "] (?=[^" + _KUCUK + _BUYUK + "])"
        for f_ad, parca in conn.execute(
                "SELECT f.file_name, substring(c.chunk_text from %s) "
                "FROM core_chunks c JOIN core_files f USING (file_id) "
                "WHERE c.chunk_text ~ %s LIMIT 10;",
                (f".{{0,30}}(?:{desen}).{{0,30}}", desen)).fetchall():
            tek = " ".join((parca or "").split())
            print(f"      {f_ad[:26]:<26} {_gorunur(tek)}")

    # --------------------------------------------------------------- S5
    print("\n\n" + "=" * 100)
    print("S5 AILE-A KAPSAMI -- kac dosya? (onarim yazmaya DEGER MI?)")
    print("=" * 100)
    print("  S3 en kotu 20 dosyayi gosteriyor ama SAYIYI gostermiyor. Onarim")
    print("  kararinin dayanagi bu sayidir: 40 dosya icin font cozucusu yazmakla")
    print("  400 dosya icin yazmak ayni karar degil. Olcut yine diyakritik")
    print("  yogunlugu (korpus medyani ~72/1000); aile-A dosyalarinda ~0'a duser.\n")
    tum_y = [(int(fid), ad, int(kar), int(tr or 0))
             for fid, ad, kar, tr in yogunluk if kar]
    kovalar = [(0.0, 1.0), (1.0, 5.0), (5.0, 10.0),
               (10.0, 30.0), (30.0, 60.0), (60.0, float("inf"))]
    print(f"  {'tr/1000 araligi':<20} {'dosya':>7} {'karakter':>14}")
    for alt_k, ust_k in kovalar:
        grup = [r for r in tum_y if alt_k <= 1000 * r[3] / r[2] < ust_k]
        etiket_k = (f"{alt_k:.0f} - {ust_k:.0f}" if ust_k != float("inf")
                    else f"{alt_k:.0f} +")
        print(f"  {etiket_k:<20} {len(grup):>7} {sum(r[2] for r in grup):>14,}")

    supheli = [r for r in tum_y if 1000 * r[3] / r[2] < 10.0]
    print(f"\n  10/1000 ALTINDA: {len(supheli)} dosya / {len(tum_y)} "
          f"({100*len(supheli)/max(1,len(tum_y)):.1f}%), "
          f"{sum(r[2] for r in supheli):,} karakter")
    if not supheli:
        return 0
    # Dusuk yogunluk tek basina "bozuk" DEMEK DEGIL -- Ingilizce/sayisal bir
    # belge de dusuk cikar. Aile-A imzasi (Ovariant, u-acute, bolme isareti...)
    # ayrimi yapar: imzasi olan BOZUK, olmayan muhtemelen Turkce degil.
    ids2 = [r[0] for r in supheli]
    with db.connection() as conn:
        imza_satir = conn.execute(
            "SELECT file_id, "
            "  coalesce(sum(length(chunk_text) - "
            "                length(translate(chunk_text, %(imza)s, ''))), 0) "
            "FROM core_chunks WHERE file_id = ANY(%(ids)s) GROUP BY file_id;",
            {"imza": _IMZA, "ids": ids2}).fetchall()
    imza_map = {int(fid): int(n) for fid, n in imza_satir}
    imzali = [r for r in supheli if imza_map.get(r[0], 0) > 0]
    print(f"  bunlardan AILE-A imzasi tasiyan: {len(imzali)} dosya, "
          f"{sum(r[2] for r in imzali):,} karakter")
    print("  (imzasi olmayanlar muhtemelen Turkce degil -> onarim degil, inceleme)\n")
    print(f"  {'dosya':<52} {'tr/1000':>8} {'imza/1000':>10}")
    for fid, ad, kar, tr in sorted(imzali, key=lambda r: -imza_map[r[0]] / r[2])[:25]:
        print(f"  {ad[:52]:<52} {1000*tr/kar:>8.2f} "
              f"{1000*imza_map[fid]/kar:>10.2f}")
    if len(imzali) > 25:
        print(f"  ... ve {len(imzali) - 25} dosya daha")
    return 0


# =============================================================== BOLUM B =====
# Sondaki karakter ILERI-BAKISTIR (tuketilmez): "bankac i l i k" gibi ardisik
# ayrik harflerin IKINCISI de sayilabilsin diye.
_HAM = re.compile("([" + _KUCUK + _BUYUK + "])( +)([" + _TR + "])( +)(?=\\S)")


def bolum_b(db, dosya_adi: str, sayfa: int = 0) -> int:
    print("=" * 100)
    print(f"BOLUM B  KAYNAK TESHISI -- {dosya_adi}")
    print("=" * 100)
    with db.connection() as conn:
        satir = conn.execute(
            "SELECT file_id, source_path, file_type FROM core_files "
            "WHERE file_name = %s ORDER BY file_id LIMIT 1;",
            (dosya_adi,)).fetchone()
    if satir is None:
        print(f"  HATA: core_files'ta '{dosya_adi}' yok.")
        return 1
    fid, yol, tur = satir
    print(f"  file_id={fid}  tur={tur}\n  yol={yol}")
    if tur != "pdf":
        print("  Bu teshis yalniz PDF icin anlamli.")
        return 1

    # ------------------------------------------------------------------ KONTROL
    # DB'de BU dosyada kac ayrik-harf var? Bu sayi olmadan yeniden parse sonucu
    # YORUMLANAMAZ: parse temiz cikarsa iki ayri anlama gelir --
    #   (a) DB de temizdi  -> dosya secimi yanlisti, olcum bos dondu
    #   (b) DB bozuktu     -> depolanan korpus bugunku parse'la AYNI DEGIL
    # Ilk turda bu kontrolu koymadim; sonucu tek basina okunamaz hale getirdi.
    with db.connection() as conn:
        db_n = int(conn.execute(
            "SELECT coalesce(sum(regexp_count(chunk_text, %s)), 0) "
            "FROM core_chunks WHERE file_id = %s;",
            (_ALPHA + " [" + _TR + "] ", fid)).fetchone()[0])
        db_kar = int(conn.execute(
            "SELECT coalesce(sum(length(chunk_text)), 0) FROM core_chunks "
            "WHERE file_id = %s;", (fid,)).fetchone()[0])
    print(f"\n  KONTROL -- DEPOLANMIS chunk'larda ayrik-harf: {db_n:,} "
          f"({1000*db_n/(db_kar or 1):.2f}/1000, {db_kar:,} karakter)")

    from ragintel.ingestion.cleaning.cleaner import clean_document
    from ragintel.ingestion.parsing import docling_backend as dbk

    ing, ps = _parse_ayarlari(db)
    print(f"\n  ... URETIM ayarlariyla yeniden parse ediliyor "
          f"(pdf_backend={ps.pdf_backend}, tableformer={ing.tableformer_mode}, "
          f"sayfa={sayfa or 'tam'}) -- birkac dakika surebilir", flush=True)
    backend = dbk.DoclingBackend(
        figure_images=False,          # metin teshisi; gorsel uretmek gereksiz maliyet
        figure_image_scale=float(ing.figure_image_scale),
        pdf_backend=ps.pdf_backend,
        tableformer_mode=str(ing.tableformer_mode),
        parse_num_threads=int(ing.parse_num_threads),
    )
    conv = backend._converter(False)
    try:
        res = conv.convert(yol, page_range=(1, sayfa)) if sayfa else conv.convert(yol)
    except TypeError:
        print("    (page_range desteklenmiyor -> tam dosya)", flush=True)
        res = conv.convert(yol)
    parsed = dbk._map_document(res.document, ocr=False)
    ham = parsed.body_text
    temiz = clean_document(parsed).cleaned_text
    print(f"  parse: {len(ham):,} karakter / {len(parsed.pages)} sayfa   "
          f"clean: {len(temiz):,} karakter")

    for etiket, metin in (("HAM PARSE CIKTISI", ham), ("CLEAN SONRASI", temiz)):
        print("\n" + "-" * 100)
        print(f"  {etiket}: ayrik-harf cevresindeki BOSLUK SAYILARI")
        print("-" * 100)
        kova: dict[tuple[int, int], int] = {}
        for m in _HAM.finditer(metin):
            anahtar = (len(m.group(2)), len(m.group(4)))
            kova[anahtar] = kova.get(anahtar, 0) + 1
        if not kova:
            print("    (ayrik-harf bulunmadi)")
            continue
        toplam = sum(kova.values())
        print(f"    {'sol bosluk':>11} {'sag bosluk':>11} {'adet':>10} {'pay':>8}")
        for (sol, sag), n in sorted(kova.items(), key=lambda x: -x[1])[:10]:
            print(f"    {sol:>11} {sag:>11} {n:>10,} {100*n/toplam:>7.1f}%")
        print(f"    {'TOPLAM':>23} {toplam:>10,}")

    print("\n" + "-" * 100)
    print("  HAM PARSE'tan ORNEKLER (orta nokta = bosluk)")
    print("-" * 100)
    for i, m in enumerate(_HAM.finditer(ham)):
        if i >= 14:
            break
        bas, son = max(0, m.start() - 26), min(len(ham), m.end() + 26)
        print(f"    {_gorunur(ham[bas:son])}")

    print("\n" + "=" * 100)
    print("  KARAR NOTU")
    print("=" * 100)
    ham_n = sum(1 for _ in _HAM.finditer(ham))
    # Sayfa siniri varsa MUTLAK sayilar kiyaslanamaz (parse dosyanin bir dilimi,
    # DB tamami). Olcut 1000 karakter basina YOGUNLUK olur; esikler de oyle.
    db_o, ham_o = 1000 * db_n / (db_kar or 1), 1000 * ham_n / (len(ham) or 1)
    print(f"  DEPOLANMIS: {db_n:,} ({db_o:.2f}/1000)     "
          f"YENIDEN PARSE: {ham_n:,} ({ham_o:.2f}/1000)"
          + (f"   [yalniz ilk {sayfa} sayfa -> yogunluk kiyaslanir]" if sayfa else ""))
    if sayfa:
        bayat, ureyor = (db_o > max(1.0, ham_o * 5)), (ham_o > 1.0)
    else:
        bayat, ureyor = (db_n > max(20, ham_n * 5)), (ham_n > 20)
    if bayat:
        print("  -> Depolanan korpus bugunku parse'la AYNI DEGIL. Ayrik-harf bir")
        print("     PIPELINE kusuru degil, ESKI CIKTININ kalintisi; adim 5 icin")
        print("     zaten bekleyen reprocess onu kendiliginden temizler. Onarim")
        print("     kodu YAZILMAMALI -- yazilsa olmayan bir kusuru kovalardi.")
    elif ureyor:
        print("  -> Kusur bugunku parse'ta da UREYOR. Sag-bosluk kovasinda 1'in")
        print("     yaninda 2 varsa kelime siniri bilgisi PDF'ten geliyor ve onu")
        print("     cleaner._INLINE_WS yok ediyor -> onarim parse ciktisinda,")
        print("     temizlemeden ONCE mumkun. Kovanin tamami 1 ise bilgi PDF'te")
        print("     zaten yok -> geriye sezgisel (S4) veya sozluk yolu kalir.")
    else:
        print("  -> Iki taraf da temiz: bu DOSYA ayrik-harf tasimiyor, secim")
        print("     yanlisti. Asagidaki listeden bir dosyayla tekrarlanmali.")
    if not bayat:
        # Dogru hedefi ELDE aramak yerine burada verelim -- ayni kosumda.
        print("\n  --- ayrik-harf YOGUNLUGU en yuksek 12 dosya (yeniden hedef icin) ---")
        with db.connection() as conn:
            satirlar = conn.execute(
                "SELECT f.file_name, sum(regexp_count(c.chunk_text, %s)) AS n, "
                "       sum(length(c.chunk_text)) AS kar "
                "FROM core_chunks c JOIN core_files f USING (file_id) "
                "GROUP BY f.file_name HAVING sum(regexp_count(c.chunk_text, %s)) > 0 "
                "ORDER BY n DESC LIMIT 12;",
                (_ALPHA + " [" + _TR + "] ", _ALPHA + " [" + _TR + "] ")).fetchall()
        for ad, n, kar in satirlar:
            print(f"      {ad[:46]:<46} {int(n):>8,}  ({1000*int(n)/max(1,int(kar)):.1f}/1000)")
    return 0


# =============================================================== BOLUM C =====
# Diyakritigi OLMAYAN Turkce islev sozcukleri. Yerine-gecmis-harf bozulmasindan
# ETKILENMEZLER: bozuk metinde ~0, dogru cozulmus metinde yuksek cikarlar. Bu
# yuzden "hangi cozum dogru" sorusunun olcutu diyakritik yogunlugu DEGIL budur.
_ISLEV = ["ve", "bir", "bu", "ile", "olan", "olarak", "daha", "gibi",
          "ancak", "veya", "kadar", "sonra", "icin", "ise"]
_ISLEV_RE = re.compile(r"\b(?:" + "|".join(_ISLEV) + r")\b", re.IGNORECASE)

# Bolum A S2/S3'te GOZLENEN aile-A imzasi (tahmin degil, envanterden).
_IMZA = _c(0x00D5, 0x00FA, 0x00F7, 0x00F8, 0x00F9, 0x0D88,
           0x00BD, 0x00BE, 0x00BF, 0x00C0, 0x00C1,
           0x203A, 0x2039, 0x00A4)

# ASCII araliginda kaydirmanin gecerli oldugu pencere: 0x21..0x60 kaynak
# karakterleri 0x3E..0x7D'ye tasinir (A-Z <- 0x24..0x3D, a-z <- 0x44..0x5D).
_KAYDIR_ALT, _KAYDIR_UST = 0x21, 0x60


def _kaydir(metin: str, n: int, alt: int = _KAYDIR_ALT, ust: int = _KAYDIR_UST,
            *, bosluk_koru: bool = True) -> str:
    """Verilen pencereyi kaydirir; ozel harf TABLOSU UYGULAMAZ.

    Tablo bilerek yok: amac kaydirmanin kendisini VERIDEN dogrulamak. Islev
    sozcuklerinin hepsi diyakritiksizdir, yani dogru kaydirmada tablo olmadan
    da ortaya cikarlar. Tabloyu once uygulasaydik olcut kendi tahminimizi
    dogrulardi -- bu turda iki kez yanildigim yer tam olarak orasi.

    bosluk_koru: GERCEK bosluk/satirsonu kaydirilmaz. Aile-A metninde iki tur
    bosluk bir arada bulunuyor -- docling'in span aralarina koydugu gercek
    U+0020 ile, kodlanmis olan (0x20-n). Ilkini kaydirmak onu '=' yapardi.
    """
    korunan = {0x09, 0x0A, 0x0D} | ({0x20} if bosluk_koru else set())
    out = []
    for ch in metin:
        k = ord(ch)
        out.append(chr(k + n) if (alt <= k <= ust and k not in korunan) else ch)
    return "".join(out)


def _olcut(metin: str) -> dict:
    import unicodedata
    n = len(metin) or 1
    ctrl = sum(1 for ch in metin
               if unicodedata.category(ch).startswith("C") and ch not in "\t\n\r")
    return {
        "kar": len(metin),
        "islev": 1000 * len(_ISLEV_RE.findall(metin)) / n,
        "tr": 1000 * sum(metin.count(c) for c in _TR) / n,
        "imza": 1000 * sum(metin.count(c) for c in _IMZA) / n,
        "rakam": 1000 * sum(ch.isdigit() for ch in metin) / n,
        "ctrl": 1000 * ctrl / n,
        # Pencere secimi icin SART: islev/1k pencereleri ayirt EDEMEZ (Python \b
        # kontrol karakterini zaten sinir sayar, dar pencerede de kelimeler
        # bolunur). Ayrimi bosluk sayisi yapar -- pencereyi 0x20'ye kadar
        # genisletmek kodlanmis bosluklari geri getirir, 0x20'yi de kaydirmak
        # ise GERCEK bosluklari yok eder.
        "bosluk": 1000 * metin.count(" ") / n,
    }


def _basli(bas: str) -> None:
    print(f"\n  {'kaynak':<22} {'karakter':>10} {'islev/1k':>9} {'tr/1k':>8} "
          f"{'imza/1k':>8} {'rakam/1k':>9} {'ctrl/1k':>8} {'bosluk/1k':>10}   {bas}")


def _satir(ad: str, o: dict, ek: str = "") -> None:
    print(f"  {ad:<22} {o['kar']:>10,} {o['islev']:>9.2f} {o['tr']:>8.2f} "
          f"{o['imza']:>8.2f} {o['rakam']:>9.2f} {o['ctrl']:>8.2f} "
          f"{o['bosluk']:>10.2f}   {ek}")


def bolum_c(db, dosya_adi: str, sayfa: int, ocr: bool) -> int:
    print("=" * 100)
    print(f"BOLUM C  YERINE-GECMIS HARF TESHISI -- {dosya_adi}")
    print("=" * 100)
    with db.connection() as conn:
        satir = conn.execute(
            "SELECT file_id, source_path, file_type FROM core_files "
            "WHERE file_name = %s ORDER BY file_id LIMIT 1;",
            (dosya_adi,)).fetchone()
    if satir is None:
        print(f"  HATA: core_files'ta '{dosya_adi}' yok.")
        return 1
    fid, yol, tur = satir
    if tur != "pdf":
        print("  Bu teshis yalniz PDF icin anlamli.")
        return 1
    print(f"  file_id={fid}  yol={yol}")
    print(f"  sayfa siniri: {sayfa or 'YOK (tam dosya)'}   OCR: {'ACIK' if ocr else 'kapali'}")

    from ragintel.ingestion.cleaning.cleaner import clean_document
    from ragintel.ingestion.parsing import docling_backend as dbk

    ing, ps = _parse_ayarlari(db)

    def _parse(be_adi: str):
        be = dbk.DoclingBackend(
            figure_images=False,          # teshis metin uzerine; gorsel maliyeti gereksiz
            figure_image_scale=float(ing.figure_image_scale),
            pdf_backend=be_adi,
            tableformer_mode=str(ing.tableformer_mode),
            parse_num_threads=int(ing.parse_num_threads),
        )
        conv = be._converter(ocr)         # uretim converter'inin AYNISI
        try:
            res = conv.convert(yol, page_range=(1, sayfa)) if sayfa else conv.convert(yol)
        except TypeError:
            # docling surumu page_range bilmiyorsa tam dosya parse edilir.
            print("    (page_range desteklenmiyor -> tam dosya)", flush=True)
            res = conv.convert(yol)
        return dbk._map_document(res.document, ocr=ocr)

    # ------------------------------------------------------------------ C0/C1
    print("\n" + "-" * 100)
    print("C1 ALT-PARSER KARSILASTIRMASI -- baska bir backend dogru cozuyor mu?")
    print("-" * 100)
    print("  islev/1k YUKSEK + imza/1k ~0 olan kaynak DOGRU cozmustur.")
    print("  Uretim satiri referanstir; digerleri ondan iyi degilse config cozum degildir.")

    # URETIM alt-parseri config grubunda DEGIL, ParsingSettings'te yasar
    # (adapter.py:103). Grup'tan okumaya calismak yanlis satiri "URETIM" diye
    # etiketler ve karsilastirmanin referansini bozardi.
    uretim_adi = str(ps.pdf_backend or "pypdfium2")
    adaylar = [uretim_adi] + [b for b in ("dlparse", "dlparse_v2", "pypdfium2")
                              if b != uretim_adi]
    sonuc: dict[str, object] = {}
    _basli("(ham parse ciktisi)")
    for be_adi in adaylar:
        try:
            p = _parse(be_adi)
        except Exception as exc:                          # noqa: BLE001 - teshis araci
            print(f"  {be_adi:<22} HATA: {type(exc).__name__}: {str(exc)[:52]}")
            continue
        sonuc[be_adi] = p                                 # ParsedDocument saklanir:
        _satir(be_adi, _olcut(p.body_text),               # C0 clean icin gerekli,
               "URETIM" if be_adi == uretim_adi else "")  # ikinci parse'a gerek kalmaz
    if not sonuc:
        print("  Hicbir backend parse edemedi -- teshis burada duruyor.")
        return 1

    parsed_uretim = sonuc.get(uretim_adi) or next(iter(sonuc.values()))
    ham = parsed_uretim.body_text

    # ------------------------------------------------------------------ C0
    print("\n" + "-" * 100)
    print("C0 TEMIZLEME KAYBI -- clean ONCESI vs SONRASI (DB yalniz SONRASINI gorur)")
    print("-" * 100)
    try:
        temiz_metin = clean_document(parsed_uretim).cleaned_text
    except Exception as exc:                              # noqa: BLE001
        print(f"  clean asamasi calistirilamadi: {type(exc).__name__}: {exc}")
        temiz_metin = None
    if temiz_metin is not None:
        _basli("(ayni dosya)")
        _satir("HAM parse", _olcut(ham))
        _satir("CLEAN sonrasi", _olcut(temiz_metin))
        import unicodedata
        yok = {}
        for ch in ham:
            if unicodedata.category(ch).startswith("C") and ch not in "\t\n\r":
                yok[ch] = yok.get(ch, 0) + 1
        print("\n  HAM parse'taki kontrol karakterleri (clean bunlari SILER):")
        if not yok:
            print("    (yok) -> kaydirma C0/C1'e dusmuyor, rakam/harf kaybi bu yoldan DEGIL")
        for ch, n in sorted(yok.items(), key=lambda x: -x[1])[:12]:
            print(f"    {_ad(ch):<46} {n:>8,}")

    # ------------------------------------------------------------------ C2
    print("\n" + "-" * 100)
    print("C2 KAYDIRMA ARAMASI -- miktar VERIDEN bulunur (1..63), tahminden degil")
    print("-" * 100)
    taban = _olcut(ham)["islev"]
    puanlar = [(_olcut(_kaydir(ham, n))["islev"], n) for n in range(1, 64)]
    puanlar.sort(reverse=True)
    print(f"  kaydirmasiz islev/1k: {taban:.2f}\n")
    print(f"  {'kaydirma':>9} {'islev/1k':>9} {'kazanc':>9}")
    for puan, n in puanlar[:5]:
        print(f"  {n:>4} (0x{n:02X}) {puan:>9.2f} {puan - taban:>+9.2f}")
    en_iyi_puan, en_iyi = puanlar[0]
    print()
    if en_iyi_puan < max(2.0, taban * 3):
        print("  HUKUM: tek-degerli bir ASCII kaydirmasi bu dosyayi ACIKLAMIYOR.")
        print("         Bozulma daha karmasik (font-basina glif tablosu) -> mekanik")
        print("         onarim yerine backend/OCR yolu tercih edilmeli.")
        return 0

    print(f"  HUKUM: +0x{en_iyi:02X} kaydirmasi metni ACIYOR "
          f"({taban:.2f} -> {en_iyi_puan:.2f}/1k).")

    # ---------------------------------------------------------------- C2b
    print("\n" + "-" * 100)
    print("C2b PENCERE -- kaydirma ASCII'nin ALTINA da mi iniyor?")
    print("-" * 100)
    print("  Hedef yazdirilabilir ASCII 0x20..0x7E ise kaynak penceresi")
    print(f"  0x{0x20-en_iyi:02X}..0x{0x7E-en_iyi:02X} olmali -- yani BOSLUK ve RAKAMLAR C0'a duser.")
    print("  Dar pencere (0x21..0x60) onlari kacirir; C0'daki kontrol karakteri")
    print("  yigilmasi tam da bunu isaret ediyor. Hangisinin dogru oldugunu")
    print("  yine olcum soyler.\n")
    pencereler = [
        ("dar 0x21..0x60", _KAYDIR_ALT, _KAYDIR_UST, True),
        (f"genis 0x{0x20-en_iyi:02X}..0x{0x7E-en_iyi:02X}", 0x20 - en_iyi, 0x7E - en_iyi, True),
        ("genis + bosluk da", 0x20 - en_iyi, 0x7E - en_iyi, False),
    ]
    _basli(f"(+0x{en_iyi:02X})")
    adaylar_p = []
    for etiket, alt, ust, bk in pencereler:
        c = _kaydir(ham, en_iyi, alt, ust, bosluk_koru=bk)
        o = _olcut(c)
        _satir(etiket, o)
        adaylar_p.append((etiket, o, c, (alt, ust, bk)))
    # SECIM KURALI (acikca yazili, cunku islev/1k burada ayirt edemez):
    #   1) ctrl/1k en dusuk olan  -> kodlanmis karakterleri gercekten geri getiren
    #   2) esitlikte bosluk/1k en yuksek olan -> gercek bosluklari bozmayan
    etiket, o, cozulmus, pencere = max(adaylar_p,
                                       key=lambda t: (-round(t[1]["ctrl"], 2),
                                                      t[1]["bosluk"]))
    print(f"\n  KAZANAN pencere: {etiket}   ctrl/1k={o['ctrl']:.2f} "
          f"bosluk/1k={o['bosluk']:.2f} rakam/1k={o['rakam']:.2f}")
    print("  (secim kurali: once en dusuk ctrl/1k = kodlanmis karakteri geri")
    print("   getiren; esitlikte en yuksek bosluk/1k = gercek boslugu bozmayan)")

    print("\n  --- cozulmus ilk satirlar (ozel harf tablosu UYGULANMADI) ---")
    for sat in [s for s in cozulmus.splitlines() if s.strip()][:12]:
        print(f"      {sat.strip()[:92]}")

    # ---------------------------------------------------------------- C3
    print("\n" + "-" * 100)
    print("C3 ARTIK KARAKTERLER -- onarim TABLOSU tam olarak bunlardir")
    print("-" * 100)
    print("  Kaydirmadan sonra hala ASCII+Turkce disinda kalan her karakter, bir")
    print("  Turkce harfin yerine gecmis demektir. Baglamdan hangisi oldugu okunur.\n")
    artik: dict[str, int] = {}
    for ch in cozulmus:
        if ch not in _BEKLENEN:
            artik[ch] = artik.get(ch, 0) + 1
    if not artik:
        print("    (artik yok -- kaydirma tek basina yetiyor)")
    for ch, n in sorted(artik.items(), key=lambda x: -x[1])[:20]:
        print(f"    {_ad(ch):<46} {n:>7,}")
        # Seyrek karakterde tek baglam yetmez; 4 tane basilir.
        kac = 2 if n > 50 else 4
        bulundu = 0
        for m in re.finditer(re.escape(ch), cozulmus):
            bas, son = max(0, m.start() - 30), min(len(cozulmus), m.end() + 30)
            print(f"        {_gorunur(cozulmus[bas:son])}")
            bulundu += 1
            if bulundu >= kac:
                break

    # ---------------------------------------------------------------- C4
    print("\n" + "-" * 100)
    print("C4 EN SIK TOKEN'LAR -- MESRU ASCII'ye dusen yerine-gecmeler")
    print("-" * 100)
    print("  C3 yalniz ASCII+Turkce DISINDA kalanlari gorur. Ama bazi yerine-")
    print("  gecmeler gecerli bir ASCII harfine dusuyor ve C3'te GORUNMEZ:")
    print("  ilk kosumda 'geoen' (=gecen), 'hmit hnsal' (=Umit Unsal),")
    print("  'gzaktan' (=Ozaktan) boyleydi. Token listesi bunlari aciga cikarir --")
    print("  Turkce olmayan bir token, ASCII'ye dusmus bir yerine-gecmedir.\n")
    from collections import Counter
    tok = Counter(t for t in re.findall(r"[^\W\d_]{3,}", cozulmus) if t)
    satir_tok = []
    for kelime, n in tok.most_common(48):
        satir_tok.append(f"{kelime[:22]}:{n}")
    for i in range(0, len(satir_tok), 4):
        print("    " + "  ".join(f"{s:<26}" for s in satir_tok[i:i + 4]))

    # ---------------------------------------------------------------- C5
    print("\n" + "-" * 100)
    print("C5 CAKISMA GERCEK MI? -- ayni 'o' hem 'o' hem 'c' olabilir mi?")
    print("-" * 100)
    print("  C4 ayni belgede hem 'olarak/olan/Yorum' (o = o, DOGRU) hem 'ioin/")
    print("  ioinde/geoen' (o = c, YERINE-GECMIS) gosterdi. Bu bir CAKISMA ise")
    print("  kod-noktasi tablosu imkansizdir. Ama olmayabilir: cozucu 1:1 konum")
    print("  korur, yani her cozulmus karakterin ham karsiligi bilinir ve")
    print("  cozulmus 'o' IKI ayri kaynaktan gelebilir --")
    print(f"    (a) pencere ICI ham 0x{0x6F-en_iyi:02X}  -> KODLANMIS akis (kaydirildi)")
    print("    (b) pencere DISI ham 0x6F  -> DUZ akis (hic dokunulmadi)")
    print("  Ikisi ayri akissa cakisma YOKTUR: tablo ham kod noktasi uzerinde")
    print("  kurulur, duz metin etkilenmez ve onarim KAYIPSIZ olur.\n")

    alt_p, ust_p, bk_p = pencere
    korunan_p = {0x09, 0x0A, 0x0D} | ({0x20} if bk_p else set())
    # _kaydir 1:1'dir -> uzunluklar esit, indisler ortusur (asagisi buna dayanir).
    kodlu = [alt_p <= ord(ch) <= ust_p and ord(ch) not in korunan_p for ch in ham]

    # Akislari AYIRIRKEN silmiyoruz, BOSLUKLA dolduruyoruz: silseydik iki akisin
    # kelimeleri birbirine yapisir ve islev/1k uydurma sonuc verirdi.
    akis_k = "".join(cozulmus[i] if kodlu[i] else " " for i in range(len(ham)))
    akis_d = "".join(" " if kodlu[i] else ham[i] for i in range(len(ham)))
    n_k = sum(kodlu)
    print(f"  {'akis':<22} {'karakter':>10} {'pay':>7} {'islev/1k':>9}   (bosluklar haric pay)")
    for ad_a, metin_a, n_a in (("KODLANMIS", akis_k, n_k),
                               ("DUZ (dokunulmamis)", akis_d, len(ham) - n_k)):
        # islev/1k'yi kendi akisinin uzunluguna gore olc, tum dosyaya gore degil.
        pay_o = 1000 * len(_ISLEV_RE.findall(metin_a)) / max(1, n_a)
        print(f"  {ad_a:<22} {n_a:>10,} {100*n_a/max(1,len(ham)):>6.1f}% {pay_o:>9.2f}")

    print("\n  --- teshis token'lari: harfleri hangi akistan geliyor? ---")
    dogru_grup = ["olarak", "olan", "kuruldu", "Kurulu", "Yorum", "Copyright"]
    bozuk_grup = ["ioin", "ioinde", "geoen", "oal" + _c(0x00D5), "gzaktan", "hmit"]
    print(f"    {'token':<16} {'adet':>6} {'kodlanmis':>10}   ham hali (ilk gecis)")
    paylar: dict[str, list[float]] = {"dogru": [], "bozuk": []}
    for grup, tokenlar in (("dogru", dogru_grup), ("bozuk", bozuk_grup)):
        for t in tokenlar:
            yerler = list(re.finditer(re.escape(t), cozulmus))
            if not yerler:
                print(f"    {t[:16]:<16} {0:>6}          -   (bu dosyada gecmiyor)")
                continue
            oran = sum(sum(kodlu[m.start():m.end()]) / len(t) for m in yerler) / len(yerler)
            paylar[grup].append(oran)
            ilk = yerler[0]
            print(f"    {t[:16]:<16} {len(yerler):>6} {100*oran:>9.0f}%   "
                  f"{_gorunur(ham[ilk.start():ilk.end()])}")

    print("\n  HUKUM:")
    d = sum(paylar["dogru"]) / len(paylar["dogru"]) if paylar["dogru"] else None
    b = sum(paylar["bozuk"]) / len(paylar["bozuk"]) if paylar["bozuk"] else None
    if d is None or b is None:
        print("    Iki gruptan biri bu dosyada gecmiyor -> hukum verilemez.")
    elif b > 0.9 and d < 0.1:
        print("    IKI AKIS. Dogru cozulen kelimeler DUZ metinden geliyor, yerine-")
        print("    gecmeler KODLANMIS akistan. Cakisma YOK -> tablo ham kod noktasi")
        print("    uzerinde kurulabilir ve onarim KAYIPSIZ olur. Sonraki adim: her")
        print("    pencere-ici ham kod noktasinin hangi Turkce harfe karsilik")
        print("    geldigini VERIDEN cikarmak (tahminle degil).")
    elif b > 0.9 and d > 0.9:
        print("    GERCEK CAKISMA. Iki kullanim da AYNI kodlanmis akisdan geliyor:")
        print("    tek bir ham kod noktasi hem 'o' hem 'c' demek. Kod-noktasi")
        print("    tablosu bunu COZEMEZ -- ayrim yalniz font/run bilgisinde var ve")
        print("    docling metin ciktisi onu tasimiyor. Mekanik onarim burada biter;")
        print("    geriye force_full_page_ocr veya sozluk-tabanli duzeltme kalir.")
    else:
        print(f"    KARARSIZ (dogru={100*d:.0f}% kodlanmis, bozuk={100*b:.0f}%).")
        print("    Yukaridaki 'ham hali' sutunu elle okunmali.")

    print("\n" + "=" * 100)
    print("  KARAR NOTU")
    print("=" * 100)
    print("  C1'de uretimden IYI bir backend varsa  -> cozum CONFIG (parse.pdf_backend)")
    print("     + adim 5'in bekleyen reprocess'i. Kod yazilmaz.")
    print("  Yoksa ve C2 kaydirmayi buluyorsa       -> cozum parse SONRASI, clean ONCESI")
    print("     bir cozucu (kaydirma + kucuk harf tablosu), aile imzasiyla tetiklenen.")
    print("  Yoksa ve C2 de bulmuyorsa              -> geriye OCR kalir (asagiya bak).")
    print("\n  OCR HAKKINDA -- OLCULDU (2026-08-09), yol KAPALI:")
    print("  `--ocr` (do_ocr=True) bu ailede HICBIR SEY DEGISTIRMEDI: uretim satiri")
    print("  OCR'siz kosumla bit-bit ayni cikti (21,528 karakter, islev 0.14,")
    print("  imza 60.62, ctrl 156.59). RapidOCR gercekten yuklendi ve GPU'ya bagli")
    print("  kosdu -- yani hata degil, MIMARI: docling yalniz METIN KATMANI OLMAYAN")
    print("  bolgeleri OCR'lar. Bu PDF'lerin metin katmani VAR, bozuk ama var, o")
    print("  yuzden OCR hic tetiklenmiyor. Geriye tek OCR kolu force_full_page_ocr")
    print("  kalir; o ayri bir ayardir ve bu arac ONU KOSMAZ.")
    return 0


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--parse", metavar="DOSYA_ADI",
                    help="Bolum B: dosyayi yeniden parse edip bosluk sinyalini olcer")
    ap.add_argument("--backend", metavar="DOSYA_ADI",
                    help="Bolum C: alt-parser karsilastirmasi + kaydirma aramasi")
    # Varsayilan bilerek bolume gore FARKLI (asagida cozuluyor): C uc backend
    # kosar -> tam kitap dakikalar surer, 12 sayfa yeter. B tek kosumdur ve
    # sonucu DEPOLANMIS sayimla kiyaslanir -> varsayilani tam dosya olmali,
    # yoksa onceki kosumlarla kiyaslanamaz hale gelir.
    ap.add_argument("--sayfa", type=int, default=None, metavar="N",
                    help="Yalniz ilk N sayfa (0 = tam dosya). "
                         "Varsayilan: Bolum C=12, Bolum B=tam dosya.")
    ap.add_argument("--ocr", action="store_true",
                    help="Bolum C: OCR acik parse et (yavas; son care yolunu olcer)")
    a = ap.parse_args()

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database

    db = Database(DbSettings()).open()
    try:
        if a.backend:
            return bolum_c(db, a.backend, 12 if a.sayfa is None else a.sayfa, a.ocr)
        return bolum_b(db, a.parse, a.sayfa or 0) if a.parse else bolum_a(db)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

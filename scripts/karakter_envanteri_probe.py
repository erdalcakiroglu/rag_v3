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

BOLUM B (--parse <dosya_adi>): kaynak teshisi.
  Dosyayi URETIM ayarlariyla yeniden parse eder, HAM parse ciktisinda ayrik-harf
  cevresindeki bosluk sayilarini sayar, sonra ayni metni clean_document'ten
  gecirip tekrar sayar. Hicbir sey yazmaz.

KOSUM (H200, venv + .env.h200 yuklu):
    python scripts/karakter_envanteri_probe.py
    python scripts/karakter_envanteri_probe.py --parse 261_2.pdf
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
    """Bosluklari GORUNUR kilar -- bu probun butun meselesi bosluk sayisi."""
    return s.replace(" ", "\u00b7").replace("\n", "\\n").replace("\t", "\\t")


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
    return 0


# =============================================================== BOLUM B =====
# Sondaki karakter ILERI-BAKISTIR (tuketilmez): "bankac i l i k" gibi ardisik
# ayrik harflerin IKINCISI de sayilabilsin diye.
_HAM = re.compile("([" + _KUCUK + _BUYUK + "])( +)([" + _TR + "])( +)(?=\\S)")


def bolum_b(db, dosya_adi: str) -> int:
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

    from ragintel.config.loader import load_config
    from ragintel.config.settings import ParsingSettings
    from ragintel.database.config_store import make_db_reader
    from ragintel.ingestion.cleaning.cleaner import clean_document
    from ragintel.ingestion.parsing.docling_backend import DoclingBackend

    cfg = load_config(db_reader=make_db_reader(db))
    ing = cfg.ingestion
    print(f"\n  ... URETIM ayarlariyla yeniden parse ediliyor "
          f"(tableformer={ing.tableformer_mode}) -- birkac dakika surebilir",
          flush=True)
    backend = DoclingBackend(
        figure_images=bool(ing.figure_images),
        figure_image_scale=float(ing.figure_image_scale),
        pdf_backend=ParsingSettings().pdf_backend,
        tableformer_mode=str(ing.tableformer_mode),
        parse_num_threads=int(ing.parse_num_threads),
    )
    parsed = backend.parse(yol, "pdf")
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
    print("  HAM parse'ta sag-bosluk kovasi 1'in YANINDA 2 (veya daha fazla) da")
    print("  iceriyorsa: kelime siniri bilgisi PDF'ten GELIYOR ve onu temizleme")
    print("  asamasi (cleaner._INLINE_WS) yok ediyor demektir -> onarim MUMKUN,")
    print("  yeri de parse ciktisidir (temizlemeden ONCE).")
    print("  Kovanin tamami 1 ise: bilgi PDF'te ZATEN YOK -> mekanik onarim")
    print("  imkansiz, ancak sezgisel (S4) veya sozluk tabanli yol kalir.")
    return 0


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--parse", metavar="DOSYA_ADI",
                    help="Bolum B: dosyayi yeniden parse edip bosluk sinyalini olcer")
    a = ap.parse_args()

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database

    db = Database(DbSettings()).open()
    try:
        return bolum_b(db, a.parse) if a.parse else bolum_a(db)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Karakter/kodlama bozulmasi taramasi -- SALT-OKUMA.

NEDEN: parse asamasinin `garbage_ratio` olcutu korpus genelinde temiz gorunuyor
(maks 0.0125). Ama o olcut yalniz Unicode kategori C* (kontrol/atanmamis) ve
U+FFFD sayiyor. Turkce PDF'lerde gozlenen bozulmanin TAMAMI ise yazdirilabilir
karakterlerden olusur:

    birli<U+00A4>i     -> birligi        (CP1254 / Type1 font eslemesi artigi)
    <U+2039>stanbul    -> Istanbul
    bankac i l i k     -> bankacilik     (kelime ici harf kopmasi)
    <U+00C3><U+00BC>   -> u-umlaut       (UTF-8'in latin1 olarak okunmasi)
    3<U+2044>4         -> 3/4            (tipografik kesir)

Yani mevcut olcut bu kusura KOR; "temiz" raporu bir guvence degil, OLCULMEMIS
bir alandir. Bu probe ona bakan AYRI bir enstrumandir.

NEDEN ONEMLI: bozulmus metin sessizce gomulur. "bankac i l i k" iceren bir chunk
"bankacilik" sorgusuyla ESLESMEZ -- dosya COMPLETED, kalite skoru 99, ama icerigi
erisilemez durumdadir. Bu, parse hatasindan daha sinsi bir kusurdur.

KAYNAK TUMUYLE ASCII'DIR. Desenler ve denek dizeleri kod-noktasindan (_c fonksiyonu)
kurulur, kaynaga gomulmez. Gerekce: gorunmez karakter (soft hyphen, ZWSP, BOM)
kaynak dosyada duzenleyiciden duzenleyiciye sessizce kaybolur; desen o zaman
hicbir sey bulmadan "temiz" der -- yani olcum araci sessizce korelir.
[[olcum-zemini-dersleri]]

NE OLCER (hicbir sey degistirmez -- yalniz SELECT):
  S1 OLCUM-ARACI DOGRULAMASI -- desenler canli motorda gercekten calisiyor mu?
     Her desen bilinen-BOZUK dizede eslesmeli VE bilinen-TEMIZ dizede
     eslesMEmeli. Ikincisi olmadan "her seyi yakalayan" bozuk bir desen de
     gecerdi. S1 kirmizi ise S2-S6 okunmaz.
  S2 Korpus geneli desen sayimi + kac dosyayi etkiliyor
  S3 Desen basina en kotu 10 dosya (1000 karakter basina yogunluk)
  S4 ORNEKLER -- gercek metin parcalari (KARAR BUNLARA BAKARAK VERILIR)
  S5 garbage_ratio KIYASI -- bu dosyalar icin mevcut olcut ne diyordu?
  S6 Turkce diyakritik yogunlugu -- ASCII'lesmis ("bankacilik") dosyalar

KOSUM (H200, venv + .env.h200 yuklu):
    python scripts/karakter_bozulmasi_probe.py
"""

from __future__ import annotations

import sys


def _c(*kod_noktalari: int) -> str:
    """Kod noktalarindan dize kurar. Kaynagin ASCII kalmasi icin TEK yol budur."""
    return "".join(chr(k) for k in kod_noktalari)


# --- Karakter kumeleri -------------------------------------------------------
# Turkce diyakritikler: i-noktasiz, c-cedilla, g-breve, o-umlaut, s-cedilla,
# u-umlaut ve buyuk harfleri.
_TR = _c(0x131, 0x00E7, 0x011F, 0x00F6, 0x015F, 0x00FC,
         0x0130, 0x00C7, 0x011E, 0x00D6, 0x015E, 0x00DC)

# Turkce harfleri [[:alpha:]] sinifina ELLE ekliyoruz: PG'nin alpha sinifi
# lc_ctype'a baglidir ve C locale'de 'i-noktasiz'i harf saymayabilir.
_ALPHA = "[[:alpha:]" + _TR + "]"

# UTF-8'in latin1 olarak okunmasi: Turkce harflerin OLUSAN ikilileri.
# Aralik ([X-Y]) KULLANMIYORUZ -- PG'de bracket araliklari collation sirasina
# gore cozulur, kod noktasina gore degil; acik alternasyon collation'dan
# bagimsizdir ve yalniz Turkce'ye ozgu ikilileri sayar (yanlis pozitif azalir).
_MOJIBAKE = "|".join([
    _c(0x00C3, 0x00BC),   # u-umlaut   (C3 BC)
    _c(0x00C3, 0x00B6),   # o-umlaut   (C3 B6)
    _c(0x00C3, 0x00A7),   # c-cedilla  (C3 A7)
    _c(0x00C3, 0x009C),   # U-umlaut   (C3 9C)
    _c(0x00C3, 0x0096),   # O-umlaut   (C3 96)
    _c(0x00C3, 0x0087),   # C-cedilla  (C3 87)
    _c(0x00C4, 0x00B1),   # i-noktasiz (C4 B1)
    _c(0x00C4, 0x00B0),   # I-noktali  (C4 B0)
    _c(0x00C4, 0x009F),   # g-breve    (C4 9F)
    _c(0x00C4, 0x009E),   # G-breve    (C4 9E)
    _c(0x00C5, 0x009F),   # s-cedilla  (C5 9F)
    _c(0x00C5, 0x009E),   # S-cedilla  (C5 9E)
])

# ONEMLI: mojibake'in IKINCI karakteri 0x80-0xBF araligindadir; bu aralik
# "kesir" (0xBC ceyrek) ve "gorunmez" (0xAD soft hyphen) kumeleriyle CAKISIR.
# Onlem alinmazsa tek bir mojibake'li 'u-umlaut' hem mojibake hem kesir olarak
# sayilir ve iki olcum de sisirilir. Bu yuzden o iki desene, onunde bir mojibake
# ONCU baytI olmamasi kosulu (negatif lookbehind) eklenir. Kosul S1'de sinanir:
# ikisinin de NEGATIF denegi bir mojibake dizesidir.
_ONCU = (0x00C3, 0x00C4, 0x00C5)
_MOJI_DEGIL = "".join(f"(?<!{_c(k)})" for k in _ONCU)

# (anahtar, aciklama, desen). Sayim PG tarafinda kosar -- 54 MB metni agdan
# cekmemek icin.
DESENLER: list[tuple[str, str, str]] = [
    ("mojibake", "UTF-8 latin1 okunmus (C3 BC -> u-umlaut)",
     _MOJIBAKE),
    ("eski_font", "PDF font eslemesi bozuk (currency-sign / tek-tirnak / fi-fl ligaturu)",
     "[" + _c(0x00A4, 0x2039, 0x203A, 0xFB01, 0xFB02, 0x00FE, 0x00DE) + "]"),
    ("ayrik_harf", "kelime ici kopmus harf (bankac i l i k)",
     _ALPHA + " [" + _TR + "] " + _ALPHA),
    ("fffd", "U+FFFD degistirme karakteri",
     _c(0xFFFD)),
    ("gorunmez", "gorunmez karakter (soft hyphen / ZWSP / ZWNJ / ZWJ / BOM)",
     _MOJI_DEGIL + "[" + _c(0x00AD, 0x200B, 0x200C, 0x200D, 0xFEFF) + "]"),
    ("kesir", "tipografik kesir (1/4 1/2 3/4 1/3 2/3, fraction slash)",
     _MOJI_DEGIL + "[" + _c(0x00BC, 0x00BD, 0x00BE, 0x2153, 0x2154, 0x2044) + "]"),
]

# Deseni gercekten sinayan dizeler: araci korpustan ONCE dogrularz.
# (desen anahtari -> (eslesmesi GEREKEN, eslesmeMEsi GEREKEN))
DENEK: dict[str, tuple[str, str]] = {
    # "Turkiye Is Bankasi": mojibake'li hali vs temiz hali
    "mojibake": ("T" + _c(0x00C3, 0x00BC) + "rkiye "
                 + _c(0x00C4, 0x00B0) + _c(0x00C5, 0x009F) + " Bankas"
                 + _c(0x00C4, 0x00B1),
                 "T" + _c(0x00FC) + "rkiye " + _c(0x0130, 0x015F) + " Bankas"
                 + _c(0x0131)),
    "eski_font": ("birli" + _c(0x00A4) + "i " + _c(0x2039) + "stanbul",
                  "birli" + _c(0x011F) + "i " + _c(0x0130) + "stanbul"),
    "ayrik_harf": ("bankac " + _c(0x0131) + " l " + _c(0x0131) + " k sekt"
                   + _c(0x00F6) + "r" + _c(0x00FC),
                   "bankac" + _c(0x0131) + "l" + _c(0x0131) + "k sekt"
                   + _c(0x00F6) + "r" + _c(0x00FC)),
    "fffd": ("bozuk " + _c(0xFFFD) + " karakter",
             "bozuk karakter"),
    # NEGATIF, mojibake'li 'i-akut'tur (C3 AD): 0xAD burada soft hyphen DEGIL,
    # bir devam baytidir. Lookbehind calismazsa bu satir kirmizi yanar.
    "gorunmez": ("yumu" + _c(0x00AD) + _c(0x015F) + "ak tire",
                 "T" + _c(0x00C3, 0x00AD) + "rkiye"),
    # NEGATIF, mojibake'li 'u-umlaut'tur (C3 BC): 0xBC burada ceyrek DEGIL.
    "kesir": ("oran 3" + _c(0x2044) + "4 kadar",
              "T" + _c(0x00C3, 0x00BC) + "rkiye"),
}

_SQL_DOSYA = """
SELECT f.file_id, f.file_name, f.language, f.quality_score
FROM core_files f WHERE f.status = 'COMPLETED';
"""

_SQL_PARSE = """
SELECT m.file_id, m.detail
FROM metrics_ingestion m
JOIN core_files f ON f.file_id = m.file_id AND f.status = 'COMPLETED'
WHERE m.step = 'parse'
ORDER BY m.file_id, m.metric_id;
"""


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _dagilim(vals: list[float]) -> str:
    if not vals:
        return "(bos)"
    s = sorted(vals)
    p = lambda q: s[min(len(s) - 1, int(len(s) * q))]          # noqa: E731
    return (f"min {s[0]:7.3f} | p05 {p(0.05):7.3f} | p50 {p(0.50):7.3f} "
            f"| ort {sum(s)/len(s):7.3f} | p95 {p(0.95):7.3f} | max {s[-1]:7.3f}")


def main() -> int:
    _force_utf8()

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database

    db = Database(DbSettings()).open()
    try:
        # -------------------------------------------------------------- S1
        print("=" * 100)
        print("S1 OLCUM-ARACI DOGRULAMASI -- desenler canli motorda calisiyor mu?")
        print("=" * 100)
        with db.connection() as conn:
            try:
                conn.execute("SELECT regexp_count('aa', 'a');").fetchone()
            except Exception as exc:
                print(f"  [X] regexp_count YOK ({type(exc).__name__}: {exc})")
                print("      PostgreSQL 15+ gerekiyor. Probe kosamaz.")
                return 2
            hatali = 0
            for anahtar, aciklama, desen in DESENLER:
                pozitif, negatif = DENEK[anahtar]
                p = conn.execute("SELECT regexp_count(%s, %s);",
                                 (pozitif, desen)).fetchone()[0]
                n = conn.execute("SELECT regexp_count(%s, %s);",
                                 (negatif, desen)).fetchone()[0]
                ok = p > 0 and n == 0
                hatali += 0 if ok else 1
                print(f"  [{'OK' if ok else ' X'}] {anahtar:<12} "
                      f"bozuk-denek={p} temiz-denek={n}   {aciklama}")
            if hatali:
                print(f"\n  UYARI: {hatali} desen kendi denek dizesinde CALISMIYOR"
                      " -> S2-S6 OKUNMAZ, cikiliyor.")
                return 1
            print("\n  [OK] tum desenler dogrulandi -> asagidaki sayilar guvenilir.")

        # Sayim tek gecliste: desen basina bir sum() sutunu + Turkce yogunlugu.
        sutunlar = ", ".join(f"sum(regexp_count(c.chunk_text, %s)) AS d{i}"
                             for i in range(len(DESENLER)))
        sql = ("SELECT c.file_id, sum(length(c.chunk_text)) AS kar, "
               f"{sutunlar}, "
               "sum(regexp_count(c.chunk_text, %s)) AS tr "
               "FROM core_chunks c GROUP BY c.file_id;")
        params = tuple(d[2] for d in DESENLER) + ("[" + _TR + "]",)

        with db.connection() as conn:
            cur = conn.cursor()
            print("\n  ... korpus taraniyor (~50k chunk / ~54 MB, sayim PG tarafinda)",
                  flush=True)
            sayim = {r[0]: r[1:] for r in cur.execute(sql, params).fetchall()}
            dosyalar = {r[0]: (r[1], r[2], r[3])
                        for r in cur.execute(_SQL_DOSYA).fetchall()}
            parse_det: dict[int, dict] = {}
            for fid, detail in cur.execute(_SQL_PARSE).fetchall():
                d = detail or {}
                # OCR fallback varsa KABUL EDILEN deneme (qc.py ile ayni kural)
                onceki = parse_det.get(fid)
                if onceki is None or d.get("attempt", 1) >= onceki.get("attempt", 1):
                    parse_det[fid] = d

        n_desen = len(DESENLER)
        satirlar = []
        for fid, satir in sayim.items():
            kar, rest = satir[0], satir[1:]
            if fid not in dosyalar or not kar:
                continue
            ad, dil, skor = dosyalar[fid]
            sayilar = [int(x or 0) for x in rest[:n_desen]]
            satirlar.append({
                "fid": fid, "ad": ad, "dil": dil,
                "skor": float(skor) if skor is not None else None,
                "kar": int(kar), "tr": int(rest[n_desen] or 0),
                "s": dict(zip([d[0] for d in DESENLER], sayilar)),
                "toplam": sum(sayilar),
            })
        if not satirlar:
            print("\n  UYARI: taranacak chunk bulunamadi.")
            return 1

        # -------------------------------------------------------------- S2
        print("\n" + "=" * 100)
        print("S2 KORPUS GENELI")
        print("=" * 100)
        t_kar = sum(r["kar"] for r in satirlar)
        print(f"  taranan dosya: {len(satirlar)}   toplam karakter: {t_kar:,}")
        print(f"\n  {'desen':<12} {'eslesme':>10} {'/1000 kar':>10} "
              f"{'etkilenen dosya':>18}   aciklama")
        for anahtar, aciklama, _d in DESENLER:
            top = sum(r["s"][anahtar] for r in satirlar)
            dos = sum(1 for r in satirlar if r["s"][anahtar])
            print(f"  {anahtar:<12} {top:>10,} {1000*top/t_kar:>10.4f} "
                  f"{dos:>9} (%{100*dos/len(satirlar):5.1f})   {aciklama}")
        etkilenen = [r for r in satirlar if r["toplam"]]
        print(f"\n  en az bir deseni olan dosya: {len(etkilenen)}/{len(satirlar)}"
              f"  (%{100*len(etkilenen)/len(satirlar):.1f})")

        # -------------------------------------------------------------- S3
        print("\n" + "=" * 100)
        print("S3 DESEN BASINA EN KOTU 10 DOSYA (1000 karakter basina yogunluk)")
        print("=" * 100)
        for anahtar, aciklama, _d in DESENLER:
            var = [r for r in satirlar if r["s"][anahtar]]
            if not var:
                print(f"\n  --- {anahtar} --- hicbir dosyada yok (temiz)")
                continue
            print(f"\n  --- {anahtar} ({aciklama}) -- {len(var)} dosya ---")
            for r in sorted(var, key=lambda r: -r["s"][anahtar] / r["kar"])[:10]:
                skor = f"{r['skor']:6.2f}" if r["skor"] is not None else "     -"
                print(f"    {r['ad'][:44]:<44} skor={skor} dil={str(r['dil'])[:3]:<3} "
                      f"adet={r['s'][anahtar]:>6} "
                      f"yog={1000*r['s'][anahtar]/r['kar']:>8.3f}")

        # -------------------------------------------------------------- S4
        print("\n" + "=" * 100)
        print("S4 ORNEKLER -- gercek metin parcalari (KARAR BUNLARA BAKARAK VERILIR)")
        print("=" * 100)
        print("  Not: bazi desenler MESRU olabilir -- fi ligaturu gercekten 'fi'")
        print("  demek olabilir, kesir isareti gercek bir orani gosteriyor olabilir.")
        print("  Sayi degil, bu ornekler karar verdirir.")
        with db.connection() as conn:
            for anahtar, aciklama, desen in DESENLER:
                print(f"\n  --- {anahtar} ({aciklama}) ---")
                ornekler = conn.execute(
                    "SELECT f.file_name, substring(c.chunk_text from %s) "
                    "FROM core_chunks c JOIN core_files f USING (file_id) "
                    "WHERE c.chunk_text ~ %s LIMIT 8;",
                    (f".{{0,34}}(?:{desen}).{{0,34}}", desen),
                ).fetchall()
                if not ornekler:
                    print("    (yok)")
                for ad, parca in ornekler:
                    tek = " ".join((parca or "").split())
                    print(f"    {ad[:30]:<30} ...{tek}...")

        # -------------------------------------------------------------- S5
        print("\n" + "=" * 100)
        print("S5 garbage_ratio KIYASI -- mevcut olcut bu dosyalar icin ne diyordu?")
        print("=" * 100)
        print(f"  {'dosya':<40} {'desen':>7} {'/1000':>8} "
              f"{'garbage_ratio':>14} {'parse_score':>12}")
        for r in sorted(satirlar, key=lambda r: -r["toplam"] / r["kar"])[:15]:
            d = parse_det.get(r["fid"], {})
            gr, ps = d.get("garbage_ratio"), d.get("parse_score")
            print(f"  {r['ad'][:40]:<40} {r['toplam']:>7} "
                  f"{1000*r['toplam']/r['kar']:>8.3f} "
                  f"{(f'{float(gr):.6f}' if gr is not None else '-'):>14} "
                  f"{(f'{float(ps):.2f}' if ps is not None else '-'):>12}")
        grler = [float(d["garbage_ratio"]) for d in parse_det.values()
                 if d.get("garbage_ratio") is not None]
        if grler:
            print("\n  korpus garbage_ratio x1000: "
                  f"{_dagilim([1000 * g for g in grler])}")
        print("  -> Yuksek desen yogunluguna karsilik garbage_ratio ~0 ise, mevcut")
        print("     olcutun bu kusura KOR oldugu OLCULMUS olur (iddia degil, veri).")

        # -------------------------------------------------------------- S6
        print("\n" + "=" * 100)
        print("S6 TURKCE DIYAKRITIK YOGUNLUGU -- ASCII'lesmis dosyalar")
        print("=" * 100)
        yog = [1000 * r["tr"] / r["kar"] for r in satirlar]
        print(f"  Turkce diyakritik / 1000 karakter: {_dagilim(yog)}")
        medyan = sorted(yog)[len(yog) // 2]
        print(f"\n  Turkce metinde bu yogunluk yuksektir (korpus medyani {medyan:.1f}).")
        print("  Cok dusuk olanlar ya Turkce DEGILDIR ya da diyakritigi DUSMUSTUR")
        print("  ('bankacilik' gibi) -- ikincisi sorguyla eslesmeyi bozar.\n")
        print(f"  {'dosya':<44} {'dil':>4} {'tr/1000':>9} {'karakter':>10} {'skor':>7}")
        for r in sorted(satirlar, key=lambda r: r["tr"] / r["kar"])[:15]:
            skor = f"{r['skor']:7.2f}" if r["skor"] is not None else "      -"
            print(f"  {r['ad'][:44]:<44} {str(r['dil'])[:4]:>4} "
                  f"{1000*r['tr']/r['kar']:>9.2f} {r['kar']:>10,} {skor}")
        dusuk = [r for r in satirlar if 1000 * r["tr"] / r["kar"] < 5]
        tr_dusuk = [r for r in dusuk if (r["dil"] or "").lower().startswith("tr")]
        print(f"\n  yogunlugu 5/1000 ALTINDA olan dosya: {len(dusuk)}/{len(satirlar)}")
        print(f"    bunlardan dili 'tr' olan            : {len(tr_dusuk)}  <- supheli")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

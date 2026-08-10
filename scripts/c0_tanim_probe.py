"""C0 TANIM PROBU -- iki probun "C0" kelimesinden ayni seyi anlayip anlamadigi.

NEDEN: iki olcum CELISIYOR.
  * `glif_kalinti_probe` (DB sayimi, 2026-08-10): 760 chunk / 57 dosya / 531
    sayfa C0 tasiyor. Olcut: E'[\\x01-\\x08\\x0B\\x0C\\x0E-\\x1F]' -- yani
    \\x0B (dikey sekme) ve \\x0C (sayfa ayraci) DAHIL.
  * `glif_esik_probe` (parse ani, ayni gun): "bilinen bozuk" 4 dosyanin ucu
    (Mustafa_Celik-293_2, Bankacilik_Terminolojisi_2, 261_2) parse aninda
    SIFIR C0 uretiyor. Olcut: `glyph_repair.c0_yogunlugu` -- \\x0B/\\x0C
    MESRU_KONTROL sayilip HARIC tutuluyor.

ILK HIPOTEZ (\\x0B/\\x0C sismesi) CURUDU -- 2026-08-10 kosumu, Bolum B:
GENIS ve DAR sayim BIREBIR AYNI (760 chunk / 57 dosya, fark 0). Sayfa ayraci
sayimin %1.2'si ve tek basina hicbir dosyayi listeye sokmuyor. Celiskinin
kaynagi tanim farki DEGIL.

Ayni kosum iki YENI seyi gosterdi ve prob bu yuzden genisletildi:

  1) KOD NOKTALARI KAYDIRMAYI KANITLIYOR. Gozlenen 24 kod noktasinin 23'u tam
     olarak `basilabilir - 0x1D`: 0x03=bosluk, 0x11='.', 0x0F=',', 0x13..0x1C
     = ON RAKAMIN ONU, 0x05='"', 0x10='-', 0x12='/'. Bu aile-A'nin imzasi.
  2) TEK ISTISNA 0x02 (STX) -- ve TAM DA en yaygin olan o: 1155 kez, 529 chunk,
     **56 dosya** (57'nin 56'si). 0x02+0x1D = 0x1F, basilamaz; kaydirmayla
     ACIKLANMIYOR. Yani "57 dosya" rakamini asil sisiren buysa, gercekten
     bozuk dosya sayisi bir buyukluk mertebesi kucuk olabilir.

PARSE-ANI CELISKISININ ACIKLAMASI (koddan, olcumle DOGRULANACAK): `Page.text`
yalnizca `text_blocks`tir; tablolar ParsedDocument'ta AYRI alandir ve
`cleaner.py:121` onlari temizlikten MUAF tutar. `_strip_junk` ise duzyazidaki
tum kategori-C karakterlerini SILER. O halde DB'de hayatta kalan C0 buyuk
olasilikla TABLO metnidir -- ve `glif_esik_probe` sayfa DUZYAZISINI olctugu
icin onu goremez. Iki olcum celismiyor, FARKLI SEYE bakiyor.

BU DOGRUYSA URETIM KODUNDA GERCEK BIR KOR NOKTA VAR: `bozuk_sayfalar()` her
iki kolda da `p.text` okur. Duzyazisi temiz ama TABLOSU bozuk bir sayfa
tespit edilemez. Tespit edilse onarim calisirdi (`birlestir` tablolari
sayfasiyla tasiyor) -- eksik olan yalnizca TESPIT.

NE OLCER (alti bolum, hepsi SALT-OKUMA, tek SELECT turu, parse YOK):
  A) Sayimi olusturan kod noktalarinin dokumu -- hangi karakter, kac kez.
  B) Uretim tanimiyla (\\x0B/\\x0C haric) yeniden sayim: chunk/dosya.
  C) Dosya bazinda ayrisma (ilk hipotezin kaydi -- artik bos cikmasi beklenir).
  D) SINIFLANDIRMA: kaydirmayla uyan C0 (0x03-0x08, 0x0E-0x1F) tasiyan dosyalar
     ile YALNIZ 0x01/0x02 tasiyanlar. Ikincisi aile-A degildir.
  E) TABLO MU DUZYAZI MI: `core_chunks.table_id` NULL degilse chunk tablo
     kokenlidir (M-2b). Kor nokta hipotezinin dogrudan sinavi.
  F) 0x02'nin ve kaydirmanin BAGLAMI: cevresindeki metin repr() ile basilir.
     "Sayi" bir karakterin ne oldugunu soylemez; metne bakmadan hukum yok.

NE OLCMEZ: esigin dogru olup olmadigini. Esik 639 sayfalik TEMIZ ornekleme
karsi olculdu (0.5, yanlis pozitif sifir) ve bu sorunun cevabi onu
DEGISTIRMEZ -- yalnizca yeni kolun KAC DOSYAYI onaracagini degistirir.

UYARI (vekil olcum): `core_chunks.chunk_text` TEMIZLIK SONRASI metindir; bu
sayim duzyazi bozulmasi icin ALT SINIRDIR. Parse anindaki duzyaziyi yalniz
`glif_esik_probe` gorur, tabloyu ise yalniz bu prob.

Kullanim:
  cd /opt/ragintel && python scripts/c0_tanim_probe.py
Konteynerde:
  docker cp scripts/c0_tanim_probe.py ragintel-api:/app/p.py
  docker exec ragintel-api python /app/p.py
"""

from __future__ import annotations

import argparse
import json
import sys

# Iki tanim da KOPYALANMAZ, tek yerde tutulur; kopya suruklenirse prob tam da
# olcmeye calistigi hatayi kendi icinde uretir.
GENIS = r"[\\x01-\\x08\\x0B\\x0C\\x0E-\\x1F]"   # glif_kalinti_probe / DB sayimi
DAR = r"[\\x01-\\x08\\x0E-\\x1F]"               # glyph_repair.c0_yogunlugu (uretim)

# Kaydirma penceresi ARITMETIKTEN cikar, tahminden degil: basilabilir aralik
# 0x20..0x7E, kaydirma -0x1D -> 0x03..0x61. Bunun C0'a dusen parcasi 0x03-0x1F.
# Geriye kalan tek C0 0x01 ve 0x02'dir; +0x1D ile 0x1E/0x1F verirler, yani
# basilabilir bir karakterden GELEMEZLER -> aile-A ile aciklanmazlar.
KAYDIRMA = r"[\\x03-\\x08\\x0E-\\x1F]"
UYMAYAN = r"[\\x01\\x02]"

# Adlari kod noktasindan okunabilir yapmak icin -- tahmin degil, standart C0.
AD = {
    1: "SOH", 2: "STX", 3: "ETX (kaydirilmis BOSLUK)", 4: "EOT", 5: "ENQ",
    6: "ACK", 7: "BEL", 8: "BS", 11: "VT  (dikey sekme -- MESRU)",
    12: "FF  (sayfa ayraci -- MESRU)", 14: "SO", 15: "SI", 16: "DLE",
    17: "DC1", 18: "DC2", 19: "DC3", 20: "DC4", 21: "NAK", 22: "SYN",
    23: "ETB", 24: "CAN", 25: "EM", 26: "SUB", 27: "ESC", 28: "FS",
    29: "GS", 30: "RS", 31: "US",
}


def _force_utf8() -> None:
    for akis in (sys.stdout, sys.stderr):
        yeniden = getattr(akis, "reconfigure", None)
        if callable(yeniden):
            try:
                yeniden(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _clip(text: str | None, n: int = 200) -> str:
    if not text:
        return ""
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


# --- SALT-OKUMA sorgular -----------------------------------------------------

_SQL_DOKUM = f"""
WITH b AS (
    SELECT file_id, chunk_id, chunk_text
    FROM core_chunks
    WHERE chunk_text ~ E'{GENIS}'
)
SELECT ascii(ch)                      AS kod,
       count(*)                       AS kez,
       count(DISTINCT b.chunk_id)     AS chunk,
       count(DISTINCT b.file_id)      AS dosya
FROM b,
     LATERAL regexp_matches(b.chunk_text, E'{GENIS}', 'g') AS m(arr),
     LATERAL unnest(m.arr) AS ch
GROUP BY 1
ORDER BY kez DESC;
"""

_SQL_SAYIM = f"""
SELECT count(*)                                              AS toplam_chunk,
       count(DISTINCT file_id)                               AS toplam_dosya,
       count(*) FILTER (WHERE g)                             AS genis_chunk,
       count(DISTINCT file_id) FILTER (WHERE g)              AS genis_dosya,
       count(*) FILTER (WHERE d)                             AS dar_chunk,
       count(DISTINCT file_id) FILTER (WHERE d)              AS dar_dosya
FROM (
    SELECT file_id,
           chunk_text ~ E'{GENIS}' AS g,
           chunk_text ~ E'{DAR}'   AS d
    FROM core_chunks
) s;
"""

# Dosya bazinda ayrisma. "yalniz_mesru" = genis olcut sayiyor ama dar olcut
# saymiyor -> dosya YANLIS ALARM; sayfa ayracindan baska bir sey tasimiyor.
_SQL_DOSYA = f"""
WITH b AS (
    SELECT file_id, page_number,
           chunk_text ~ E'{GENIS}' AS g,
           chunk_text ~ E'{DAR}'   AS d
    FROM core_chunks
), a AS (
    SELECT file_id,
           count(*)                                            AS chunk,
           count(*) FILTER (WHERE g)                           AS genis_chunk,
           count(*) FILTER (WHERE d)                           AS dar_chunk,
           count(DISTINCT page_number) FILTER (WHERE g)        AS genis_sayfa,
           count(DISTINCT page_number) FILTER (WHERE d)        AS dar_sayfa
    FROM b GROUP BY file_id
), q AS (
    SELECT file_id, string_agg(DISTINCT finding, ',') AS bulgular
    FROM qc_findings WHERE finding LIKE 'encoding%%' GROUP BY file_id
)
SELECT f.file_name, f.status, a.chunk, a.genis_chunk, a.dar_chunk,
       a.genis_sayfa, a.dar_sayfa, q.bulgular
FROM a JOIN core_files f USING (file_id)
LEFT JOIN q USING (file_id)
WHERE a.genis_chunk > 0
ORDER BY a.dar_chunk DESC, a.genis_chunk DESC, f.file_name;
"""


# Sinif dosya basina: kaydirmayla uyan C0 var mi, yalniz 0x01/0x02 mi?
# `table_id` (M-2b) chunk'in tablo kokenli olup olmadigini soyler -- tetigin
# kor noktasi hipotezinin dogrudan sinavi.
_SQL_SINIF = f"""
WITH b AS (
    SELECT file_id, table_id,
           chunk_text ~ E'{KAYDIRMA}' AS kay,
           chunk_text ~ E'{UYMAYAN}'  AS uym,
           chunk_text ~ E'{DAR}'      AS dar
    FROM core_chunks
), a AS (
    SELECT file_id,
           count(*) FILTER (WHERE dar)                                AS dar_chunk,
           count(*) FILTER (WHERE kay)                                AS kay_chunk,
           count(*) FILTER (WHERE uym)                                AS uym_chunk,
           count(*) FILTER (WHERE dar AND table_id IS NOT NULL)       AS tablo_chunk,
           count(*) FILTER (WHERE dar AND table_id IS NULL)           AS duzyazi_chunk
    FROM b GROUP BY file_id
), q AS (
    SELECT file_id, string_agg(DISTINCT finding, ',') AS bulgular
    FROM qc_findings WHERE finding LIKE 'encoding%%' GROUP BY file_id
)
SELECT f.file_name, a.dar_chunk, a.kay_chunk, a.uym_chunk,
       a.tablo_chunk, a.duzyazi_chunk, q.bulgular
FROM a JOIN core_files f USING (file_id)
LEFT JOIN q USING (file_id)
WHERE a.dar_chunk > 0
ORDER BY a.kay_chunk DESC, a.dar_chunk DESC, f.file_name;
"""

# Tablo/duzyazi kirilimi korpus geneli.
_SQL_TABLO = f"""
SELECT count(*) FILTER (WHERE dar)                              AS dar_chunk,
       count(*) FILTER (WHERE dar AND tb)                       AS dar_tablo,
       count(*) FILTER (WHERE dar AND NOT tb)                   AS dar_duzyazi,
       count(*) FILTER (WHERE tb)                               AS tum_tablo,
       count(*)                                                 AS tum_chunk,
       count(*) FILTER (WHERE kay)                              AS kay_chunk,
       count(*) FILTER (WHERE kay AND tb)                       AS kay_tablo,
       count(DISTINCT file_id) FILTER (WHERE kay)               AS kay_dosya,
       count(*) FILTER (WHERE uym AND NOT kay)                  AS yalniz_uym,
       count(DISTINCT file_id) FILTER (WHERE uym AND NOT kay)   AS yalniz_uym_dosya
FROM (
    SELECT file_id, table_id IS NOT NULL AS tb,
           chunk_text ~ E'{DAR}'      AS dar,
           chunk_text ~ E'{KAYDIRMA}' AS kay,
           chunk_text ~ E'{UYMAYAN}'  AS uym
    FROM core_chunks
) s;
"""

# BAGLAM: karakterin ne oldugunu sayi degil METIN soyler. Ilk gecisin
# cevresinden bir pencere kesilir; kontrol karakterleri Python'da repr() ile
# gorunur kilinir.
_SQL_ORNEK = """
SELECT f.file_name, c.chunk_index, c.page_number,
       (c.table_id IS NOT NULL) AS tablo,
       substr(c.chunk_text,
              greatest(1, strpos(c.chunk_text, chr(%(kod)s)) - 45), 110) AS baglam
FROM core_chunks c JOIN core_files f USING (file_id)
WHERE strpos(c.chunk_text, chr(%(kod)s)) > 0
ORDER BY f.file_name, c.chunk_index
LIMIT %(n)s;
"""


def _tara(conn, *, ornek: int) -> dict:
    dokum = conn.execute(_SQL_DOKUM).fetchall()
    sayim = conn.execute(_SQL_SAYIM).fetchone()
    dosyalar = conn.execute(_SQL_DOSYA).fetchall()
    sinif = conn.execute(_SQL_SINIF).fetchall()
    tablo = conn.execute(_SQL_TABLO).fetchone()
    ornekler = {}
    for ad, kod in (("0x02", 2), ("0x03", 3)):
        ornekler[ad] = [
            {"file_name": fn, "chunk_index": ci, "page_number": pn,
             "tablo": tb, "baglam": bg}
            for fn, ci, pn, tb, bg in
            conn.execute(_SQL_ORNEK, {"kod": kod, "n": ornek}).fetchall()
        ]
    return {
        "sinif": [{"file_name": fn, "dar_chunk": dc, "kay_chunk": kc,
                   "uym_chunk": uc, "tablo_chunk": tc, "duzyazi_chunk": zc,
                   "bulgular": bg}
                  for fn, dc, kc, uc, tc, zc, bg in sinif],
        "tablo": {"dar_chunk": tablo[0], "dar_tablo": tablo[1],
                  "dar_duzyazi": tablo[2], "tum_tablo": tablo[3],
                  "tum_chunk": tablo[4], "kay_chunk": tablo[5],
                  "kay_tablo": tablo[6], "kay_dosya": tablo[7],
                  "yalniz_uym": tablo[8], "yalniz_uym_dosya": tablo[9]},
        "ornekler": ornekler,
        "dokum": [{"kod": k, "ad": AD.get(k, "?"), "kez": kez,
                   "chunk": ch, "dosya": d} for k, kez, ch, d in dokum],
        "sayim": {"toplam_chunk": sayim[0], "toplam_dosya": sayim[1],
                  "genis_chunk": sayim[2], "genis_dosya": sayim[3],
                  "dar_chunk": sayim[4], "dar_dosya": sayim[5]},
        "dosyalar": [{"file_name": fn, "status": st, "chunk": ch,
                      "genis_chunk": gc, "dar_chunk": dc,
                      "genis_sayfa": gs, "dar_sayfa": ds, "bulgular": bg}
                     for fn, st, ch, gc, dc, gs, ds, bg in dosyalar],
    }


def _print_human(veri: dict, *, dosya_limit: int) -> None:
    print("=" * 100)
    print("C0 TANIM PROBU  --  760/57'nin kaci GERCEKTEN aile-A? tablo mu duzyazi mi?")
    print("=" * 100)

    # -- A -------------------------------------------------------------------
    print()
    print("-" * 100)
    print("BOLUM A · SAYIMI HANGI KARAKTERLER OLUSTURUYOR?")
    print("-" * 100)
    d = veri["dokum"]
    if not d:
        print("  Hicbir kod noktasi bulunamadi -- genis olcut de bos donuyor.")
    else:
        print(f"  {'kod':>4}  {'karakter':<30} {'kez':>10} {'chunk':>7} {'dosya':>6}")
        for x in d:
            print(f"  0x{x['kod']:02X}  {x['ad']:<30} {x['kez']:>10} "
                  f"{x['chunk']:>7} {x['dosya']:>6}")
        mesru = sum(x["kez"] for x in d if x["kod"] in (11, 12))
        toplam = sum(x["kez"] for x in d) or 1
        print()
        print(f"  \\x0B + \\x0C payi: {mesru} / {toplam}  (%{100.0 * mesru / toplam:.1f})")
        print("  Bu iki karakter her PDF'te MESRU olarak bulunur; uretim olcutu")
        print("  (glyph_repair.MESRU_KONTROL) onlari bozulma saymaz.")

    # -- B -------------------------------------------------------------------
    s = veri["sayim"]
    print()
    print("-" * 100)
    print("BOLUM B · IKI TANIMLA YENIDEN SAYIM")
    print("-" * 100)
    print(f"  korpus                     : {s['toplam_chunk']:>7} chunk  "
          f"{s['toplam_dosya']:>5} dosya")
    print(f"  GENIS (\\x0B\\x0C DAHIL)      : {s['genis_chunk']:>7} chunk  "
          f"{s['genis_dosya']:>5} dosya   <- glif_kalinti_probe boyle saydi")
    print(f"  DAR   (uretim tanimi)      : {s['dar_chunk']:>7} chunk  "
          f"{s['dar_dosya']:>5} dosya   <- glyph_repair.c0_yogunlugu boyle sayar")
    fark_c = s["genis_chunk"] - s["dar_chunk"]
    fark_d = s["genis_dosya"] - s["dar_dosya"]
    print(f"  FARK (yanlis alarm)        : {fark_c:>7} chunk  {fark_d:>5} dosya")
    print()
    if s["dar_chunk"] == 0:
        print("  HUKUM: sayimin TAMAMI sayfa ayracindan geliyor. 760/57 rakami")
        print("  gecersiz; korpusta temizlik-sonrasi C0 bozulmasi YOK.")
    elif fark_c > 0:
        pay = 100.0 * fark_c / (s["genis_chunk"] or 1)
        print(f"  HUKUM: sayimin %{pay:.1f}'i yanlis alarmdi. Yeni kolun DB'de")
        print(f"  gorunen isi {s['dar_chunk']} chunk / {s['dar_dosya']} dosya.")
    else:
        print("  HUKUM: iki tanim ayni sonucu veriyor -- celiskinin kaynagi")
        print("  \\x0B/\\x0C DEGIL. Baska bir aciklama aranmali (temizlik akligi?).")
    print()
    print("  ALT SINIR UYARISI: chunk_text temizlik SONRASI metindir; cleaner")
    print("  duzyazidaki C0'i siler, tablolari muaf tutar. Buradaki DAR sayi")
    print("  parse anindaki gercegin ALTINDA kalir -- ustunde degil.")

    # -- C -------------------------------------------------------------------
    f = veri["dosyalar"]
    gercek = [x for x in f if x["dar_chunk"] > 0]
    alarm = [x for x in f if x["dar_chunk"] == 0]
    print()
    print("-" * 100)
    print("BOLUM C · DOSYA BAZINDA AYRISMA")
    print("-" * 100)
    print(f"  genis olcutun saydigi dosya : {len(f)}")
    print(f"  bunlardan GERCEK C0 tasiyan : {len(gercek)}")
    print(f"  yalniz \\x0B/\\x0C (alarm)     : {len(alarm)}")
    if gercek:
        print()
        print(f"  {'dosya':<48} {'durum':<10} {'chunk':>6} {'genis':>6} "
              f"{'dar':>5} {'g.sf':>5} {'d.sf':>5}  damga")
        for x in gercek[:dosya_limit]:
            print(f"  {_clip(x['file_name'], 48):<48} {str(x['status'])[:10]:<10} "
                  f"{x['chunk']:>6} {x['genis_chunk']:>6} {x['dar_chunk']:>5} "
                  f"{x['genis_sayfa']:>5} {x['dar_sayfa']:>5}  "
                  f"{_clip(x['bulgular'], 24)}")
        if len(gercek) > dosya_limit:
            print(f"  ... + {len(gercek) - dosya_limit} dosya daha (--dosya-limit)")
        print()
        print("  Bu liste yeni kolun (control_per_1k=0.5) reprocess'te onarmasi")
        print("  BEKLENEN dosya kumesidir -- damgasi 'encoding_repaired' olmayanlar.")
    if alarm:
        print()
        print(f"  YANLIS ALARM ({len(alarm)} dosya, ilk {min(len(alarm), 10)}):")
        for x in alarm[:10]:
            print(f"    {_clip(x['file_name'], 60):<60} genis={x['genis_chunk']}")

    # -- D -------------------------------------------------------------------
    sn = veri["sinif"]
    aile_a = [x for x in sn if x["kay_chunk"] > 0]
    sadece = [x for x in sn if x["kay_chunk"] == 0]
    print()
    print("-" * 100)
    print("BOLUM D · SINIFLANDIRMA -- hangi dosya GERCEKTEN aile-A?")
    print("-" * 100)
    print("  Olcut aritmetik: basilabilir(0x20-0x7E) - 0x1D -> 0x03-0x61. C0'a")
    print("  dusen parca 0x03-0x1F. 0x01/0x02 basilabilir bir karakterden GELEMEZ.")
    print()
    print(f"  C0 tasiyan dosya            : {len(sn)}")
    print(f"  KAYDIRMA izi tasiyan (aile-A): {len(aile_a)}")
    print(f"  yalniz 0x01/0x02 (aile-A DEGIL): {len(sadece)}")
    if sn:
        print()
        print(f"  {'dosya':<48} {'C0':>5} {'kaydir':>7} {'0x01/02':>8} "
              f"{'tablo':>6} {'duzyazi':>8}  damga")
        for x in sn[:dosya_limit]:
            print(f"  {_clip(x['file_name'], 48):<48} {x['dar_chunk']:>5} "
                  f"{x['kay_chunk']:>7} {x['uym_chunk']:>8} "
                  f"{x['tablo_chunk']:>6} {x['duzyazi_chunk']:>8}  "
                  f"{_clip(x['bulgular'], 22)}")
        if len(sn) > dosya_limit:
            print(f"  ... + {len(sn) - dosya_limit} dosya daha (--dosya-limit)")
    print()
    print("  Reprocess kapsami 'C0 tasiyan' degil 'KAYDIRMA izi tasiyan'")
    print("  sutunundan okunur -- ustteki liste ikisini karistirmaz.")

    # -- E -------------------------------------------------------------------
    t = veri["tablo"]
    print()
    print("-" * 100)
    print("BOLUM E · TABLO MU DUZYAZI MI? (tetigin kor noktasinin sinavi)")
    print("-" * 100)
    pay_t = 100.0 * t["tum_tablo"] / (t["tum_chunk"] or 1)
    print(f"  korpus            : {t['tum_chunk']:>6} chunk, bunun "
          f"{t['tum_tablo']} tanesi tablo kokenli (%{pay_t:.1f}) -- TABAN")
    print(f"  C0 tasiyan        : {t['dar_chunk']:>6} chunk   "
          f"tablo={t['dar_tablo']}   duzyazi={t['dar_duzyazi']}")
    print(f"  KAYDIRMA tasiyan  : {t['kay_chunk']:>6} chunk   "
          f"tablo={t['kay_tablo']}   dosya={t['kay_dosya']}")
    print(f"  yalniz 0x01/0x02  : {t['yalniz_uym']:>6} chunk   "
          f"dosya={t['yalniz_uym_dosya']}")
    print()
    if t["dar_chunk"]:
        pay = 100.0 * t["dar_tablo"] / t["dar_chunk"]
        print(f"  C0'in %{pay:.1f}'i TABLO chunk'inda. Taban %{pay_t:.1f} --")
        if pay > pay_t + 20:
            print("  belirgin sekilde YUKARI sapiyor: `_strip_junk` duzyaziyi")
            print("  temizliyor, tablolar muaf. `bozuk_sayfalar()` iki kolda da")
            print("  `p.text` okudugu icin TABLO bozuklugunu GORMEZ -> duzyazisi")
            print("  temiz, tablosu bozuk sayfa tespit disinda kalir.")
        else:
            print("  tabandan belirgin sapma YOK -> 'C0 tabloda birikiyor'")
            print("  hipotezi bu veriyle desteklenmiyor; kor nokta baska yerde.")

    # -- F -------------------------------------------------------------------
    print()
    print("-" * 100)
    print("BOLUM F · BAGLAM -- karakterin ne oldugunu sayi degil METIN soyler")
    print("-" * 100)
    for ad, kayit in veri["ornekler"].items():
        beklenen = ("bosluk (0x20-0x1D) -> kaydirmanin imzasi" if ad == "0x03"
                    else "kaydirmayla ACIKLANMIYOR -- ne oldugu bilinmiyor")
        print()
        print(f"  {ad}  ({beklenen})")
        if not kayit:
            print("    ornek yok.")
            continue
        for o in kayit:
            print(f"    {_clip(o['file_name'], 44):<44} chunk={o['chunk_index']:<5} "
                  f"s.{o['page_number'] or 0:<5} tablo={'EVET' if o['tablo'] else 'hayir'}")
            print(f"      {_clip(repr(o['baglam']), 150)}")


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dosya-limit", type=int, default=60,
                    help="Bolum C ve D'de basilacak dosya sayisi")
    ap.add_argument("--ornek", type=int, default=8,
                    help="Bolum F'de kod noktasi basina baglam ornegi")
    ap.add_argument("--json", action="store_true", help="Ham JSON bas")
    args = ap.parse_args()

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database

    db = Database(DbSettings()).open()
    try:
        with db.connection() as conn:
            veri = _tara(conn, ornek=args.ornek)
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()

    if args.json:
        print(json.dumps(veri, ensure_ascii=False, indent=2, default=str))
    else:
        _print_human(veri, dosya_limit=args.dosya_limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

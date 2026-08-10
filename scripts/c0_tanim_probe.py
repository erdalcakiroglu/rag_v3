"""C0 TANIM PROBU -- iki probun "C0" kelimesinden ayni seyi anlayip anlamadigi.

NEDEN: iki olcum CELISIYOR.
  * `glif_kalinti_probe` (DB sayimi, 2026-08-10): 760 chunk / 57 dosya / 531
    sayfa C0 tasiyor. Olcut: E'[\\x01-\\x08\\x0B\\x0C\\x0E-\\x1F]' -- yani
    \\x0B (dikey sekme) ve \\x0C (sayfa ayraci) DAHIL.
  * `glif_esik_probe` (parse ani, ayni gun): "bilinen bozuk" 4 dosyanin ucu
    (Mustafa_Celik-293_2, Bankacilik_Terminolojisi_2, 261_2) parse aninda
    SIFIR C0 uretiyor. Olcut: `glyph_repair.c0_yogunlugu` -- \\x0B/\\x0C
    MESRU_KONTROL sayilip HARIC tutuluyor.

HIPOTEZ: celiski korpusta degil, TANIMDA. Iki prob ayni kelimeyi iki farkli
kumeye kullaninca sahte bir celiski uretti. Dogruysa 760/57 rakami siradan
sayfa ayraclariyla SISIRILMIS demektir.

NE OLCER (uc bolum, hepsi SALT-OKUMA, tek SELECT turu, parse YOK):
  A) Sayimi olusturan kod noktalarinin dokumu -- hangi karakter, kac kez.
  B) Uretim tanimiyla (\\x0B/\\x0C haric) yeniden sayim: chunk/dosya/sayfa.
  C) Dosya bazinda ayrisma: yalniz \\x0B/\\x0C yuzunden sayilan dosyalar
     (YANLIS ALARM) ile gercek C0 tasiyanlar.

NE OLCMEZ: esigin dogru olup olmadigini. Esik zaten veriden secildi (0.5,
glif_esik_probe) ve bu sorunun cevabi onu DEGISTIRMEZ -- yalnizca yeni kolun
KAC DOSYAYI onaracagini degistirir. Uretim kodu bu proba bagli degildir.

UYARI (vekil olcum): `core_chunks.chunk_text` TEMIZLIK SONRASI metindir.
`cleaning/cleaner.py:_strip_junk` kategori-C karakterlerini siler, ama
`cleaner.py:121` tablolari muaf tutar ("İP-2 kazanir -- dokunulmaz"). Yani
DB tarafindaki C0 sayimi TABLO yoluna yanlidir ve ALT SINIRDIR: duzyazidaki
bozulma temizlikte aklanmis olabilir. Parse anindaki gercegi yalniz
`glif_esik_probe` gorur.

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


def _tara(conn) -> dict:
    dokum = conn.execute(_SQL_DOKUM).fetchall()
    sayim = conn.execute(_SQL_SAYIM).fetchone()
    dosyalar = conn.execute(_SQL_DOSYA).fetchall()
    return {
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
    print("C0 TANIM PROBU  --  760/57 rakami gercek mi, sayfa ayraciyla mi sisti?")
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


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dosya-limit", type=int, default=60,
                    help="Bolum C'de basilacak dosya sayisi")
    ap.add_argument("--json", action="store_true", help="Ham JSON bas")
    args = ap.parse_args()

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database

    db = Database(DbSettings()).open()
    try:
        with db.connection() as conn:
            veri = _tara(conn)
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

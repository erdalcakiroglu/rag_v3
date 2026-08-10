#!/usr/bin/env python
r"""Adım-4 EŞİK ÖLÇÜMÜ · glif tetiğine eklenecek C0 kolunun eşiği — SALT-OKUMA.

NEDEN BU PROB VAR: `glif_kalinti_probe.py` (2026-08-10) mevcut tetiğin bozuk
sayfaların **%86.8'ini** göremediğini ölçtü (531 bozuk sayfanın 461'i imza
eşiğinin altında, medyan imza yoğunluğu 0.00). Çözüm ikinci bir kol: C0 kontrol
karakteri yoğunluğu. Sebep, bozulmanın aritmetiğidir — kaydırma ASCII−29'dur ve
BOŞLUK (0x20) kaydığında 0x03 olur; yani kaydırılmış her metin parçası, içinde
boşluk geçtiği sürece C0 üretir. İmza karakteri ise ancak kaynakta Türkçe
diyakritik varsa üretilir. C0 kolunun kapsamı bu yüzden yapısal olarak geniştir.

AMA EŞİK TAHMİN EDİLEMEZ, ÖLÇÜLMELİDİR. `cleaning/cleaner.py:_strip_junk`
kategori-C karakterlerini SİLİYOR — bu fonksiyon boşuna yazılmamış, demek ki
temiz parse çıktısında da bir miktar C0 var (form feed, PUA, yön işaretleri).
Eşiği "sıfırdan büyük" yapmak o arka plan gürültüsünü bozukluk sayar ve temiz
sayfaları tam-sayfa OCR'a yollar. Bu prob eşiği VERİDEN seçtirir: bilinen bozuk
ve bilinen temiz dosyalar parse edilir, sayfa başına C0 yoğunluğu ölçülür ve
aday eşikler için yakalama/yanlış-pozitif tablosu basılır.

NEDEN DB'DEKİ chunk METNİ YETMİYOR: `core_chunks.chunk_text` temizlik
SONRASIDIR. Düzyazı bloklarının C0'ı `_strip_junk` tarafından silinmiştir;
DB'de C0 görünen 760 chunk'ın büyük kısmı TABLO metnidir, çünkü tablolar
temizlikten geçmez (`cleaner.py:121` — "tables=parsed.tables ... dokunulmaz").
Yani DB sayımı ALT SINIRDIR ve tablo yoluna yanlıdır. Tetik ise PARSE ANINDA,
temizlikten ÖNCE çalışır; ölçüm de orada yapılmalıdır. Bu prob dosyayı yeniden
parse ederek tetiğin gerçekten gördüğü metni ölçer.

ÖLÇÜM-ZEMİNİ / GÜVENLİK:
  • DB'ye YAZMAZ. Yalnız SELECT (dosya listesi + yol) ve `ParseAdapter.parse_path`
    kullanılır — o metot "saf dispatch (DB yok)" olarak belgelenmiştir; telemetri
    yazan `parse_file` YOLU KULLANILMAZ.
  • Config ÜRETİMDEN okunur (db_reader'lı `ParseAdapter(db=...)`): dev config'ten
    okunan backend/pdf_backend ayarı üretimin gördüğü metni vermez.
  • Parse PAHALIDIR (kitap boyu PDF'lerde dakikalar). Örneklem küçük tutulur ve
    her dosyanın süresi basılır; kırpma sessiz değildir.
  • Tam-sayfa OCR ÇAĞRILMAZ — ölçülen şey tetiğin GİRDİSİDİR, onarımın çıktısı değil.

Bare-metal (H200):
  cd /opt/ragintel && python scripts/glif_esik_probe.py
Konteynerde:
  docker cp scripts/glif_esik_probe.py ragintel-api:/app/p.py
  docker exec ragintel-api python /app/p.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from ragintel.ingestion.parsing.glyph_repair import imza_yogunlugu

# Aday eşikler (1000 karakter başına C0). Karar tablosu bunlar için basılır.
ADAY_ESIK = (0.5, 1.0, 2.0, 5.0, 10.0, 20.0)
MIN_KARAKTER = 200  # glyph_repair.bozuk_sayfalar varsayılanıyla aynı


def _force_utf8() -> None:
    for akis in (sys.stdout, sys.stderr):
        yeniden = getattr(akis, "reconfigure", None)
        if callable(yeniden):
            try:
                yeniden(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _clip(text: str | None, n: int = 60) -> str:
    if not text:
        return ""
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _c0_yogunluk(metin: str) -> tuple[float, int]:
    """1000 karakter başına C0 sayısı ve ham sayı.

    \\t \\n \\r DIŞARIDA: bunlar meşru düzen karakteridir. \\x0b (VT) ve \\x0c (FF)
    de DIŞARIDA — form feed'i bazı PDF çıkarıcılar sayfa ayracı olarak basar ve
    onu bozukluk saymak yapısal bir yanlış-pozitif olurdu. Kaydırılmış metnin
    imzası olan 0x03 (boşluk−29) zaten bu kümenin içindedir.
    """
    n = len(metin)
    if n == 0:
        return 0.0, 0
    k = sum(1 for c in metin if ord(c) < 0x20 and c not in "\t\n\r\x0b\x0c")
    return 1000.0 * k / n, k


# --- SALT-OKUMA sorgular -----------------------------------------------------

_KOSUL = r"chunk_text ~ E'[\\x01-\\x08\\x0B\\x0C\\x0E-\\x1F]'"

# BOZUK örneklem: DB'de C0 taşıyan chunk'ı olan dosyalar (en çok taşıyandan).
_SQL_BOZUK = f"""
WITH b AS (
    SELECT file_id, count(*) FILTER (WHERE {_KOSUL}) AS bozuk
    FROM core_chunks GROUP BY file_id
)
SELECT f.file_name, f.source_path, f.file_type, b.bozuk
FROM b JOIN core_files f USING (file_id)
WHERE b.bozuk > 0 AND f.file_type = 'pdf'
ORDER BY b.bozuk DESC
LIMIT %(n)s;
"""

# TEMİZ örneklem: hiç C0 chunk'ı OLMAYAN ve imza taşımayan dosyalar. Yanlış
# pozitif oranı ancak gerçekten temiz metinde ölçülebilir; "C0 yok" yetmez,
# imza da yok olmalı ki dosya kaydırılmış-ama-tablosuz olmasın.
_SQL_TEMIZ = f"""
WITH b AS (
    SELECT file_id,
           count(*) FILTER (WHERE {_KOSUL})                       AS bozuk,
           count(*) FILTER (WHERE chunk_text LIKE '%%Õ%%'
                               OR chunk_text LIKE '%%ú%%'
                               OR chunk_text LIKE '%%›%%')         AS imza,
           count(*)                                               AS chunk
    FROM core_chunks GROUP BY file_id
)
SELECT f.file_name, f.source_path, f.file_type, b.chunk
FROM b JOIN core_files f USING (file_id)
WHERE b.bozuk = 0 AND b.imza = 0 AND f.file_type = 'pdf' AND b.chunk BETWEEN 40 AND 400
ORDER BY b.chunk DESC
LIMIT %(n)s;
"""


def _sayfalari_olc(adapter, yol: str, tip: str) -> tuple[list[dict], str | None]:
    """Dosyayı parse eder ve sayfa başına yoğunlukları döndürür. DB'ye yazmaz."""
    try:
        doc = adapter.parse_path(yol, tip)
    except Exception as exc:                       # noqa: BLE001 -- prob durmaz
        return [], f"{type(exc).__name__}: {exc}"
    out = []
    for p in doc.pages:
        metin = p.text
        if len(metin) < MIN_KARAKTER:
            continue                                # tetik de bunları atlıyor
        yog, ham = _c0_yogunluk(metin)
        out.append({"sayfa": p.page_no, "karakter": len(metin),
                    "c0_yogunluk": round(yog, 3), "c0": ham,
                    "imza_yogunluk": round(imza_yogunlugu(metin), 2)})
    return out, None


def _kova(sayfalar: list[dict], esik: float) -> int:
    return sum(1 for s in sayfalar if s["c0_yogunluk"] >= esik)


def _print_grup(ad: str, kayitlar: list[dict]) -> None:
    print()
    print("-" * 100)
    print(f"{ad}")
    print("-" * 100)
    for k in kayitlar:
        if k["hata"]:
            print(f"  {_clip(k['file_name'], 46):<46} PARSE HATASI: {_clip(k['hata'], 40)}")
            continue
        s = k["sayfalar"]
        if not s:
            print(f"  {_clip(k['file_name'], 46):<46} olculebilir sayfa YOK "
                  f"(hepsi < {MIN_KARAKTER} karakter)")
            continue
        yog = sorted(x["c0_yogunluk"] for x in s)
        orta = yog[len(yog) // 2]
        p90 = yog[min(len(yog) - 1, int(0.90 * len(yog)))]
        sifir = sum(1 for x in yog if x == 0.0)
        print(f"  {_clip(k['file_name'], 46):<46} sayfa={len(s):>4} "
              f"c0/1k: medyan={orta:>7.3f} p90={p90:>8.3f} max={yog[-1]:>8.3f}  "
              f"sifir sayfa={sifir:>4}  ({k['saniye']:.1f}s)")


def _print_karar(bozuk: list[dict], temiz: list[dict]) -> None:
    b_sayfa = [s for k in bozuk for s in k["sayfalar"]]
    t_sayfa = [s for k in temiz for s in k["sayfalar"]]
    print()
    print("=" * 100)
    print("KARAR TABLOSU · C0 kolunun esigi")
    print("=" * 100)
    print(f"  bozuk ornekem sayfa: {len(b_sayfa)}   temiz ornekem sayfa: {len(t_sayfa)}")
    if not b_sayfa or not t_sayfa:
        print("  YETERSIZ ORNEKLEM -- karar tablosu basilmaz (yaniltici olurdu).")
        return

    # İmza kolu tek başına bozuk örneklemde ne kadarını görüyor? Kazancın
    # paydası budur; C0 kolunun katkısı bunun ÜSTÜNE eklediğidir.
    imza_goren = sum(1 for s in b_sayfa if s["imza_yogunluk"] >= 10.0)
    print(f"  mevcut imza kolu (>=10/1000) bozuk orneklemde: {imza_goren}/{len(b_sayfa)} sayfa")
    print()
    print(f"  {'esik(c0/1k)':>12} {'BOZUK yakalanan':>16} {'TEMIZ yanlis-poz':>18} "
          f"{'imza+c0 birlikte':>18}")
    for e in ADAY_ESIK:
        bk = _kova(b_sayfa, e)
        tk = _kova(t_sayfa, e)
        birlikte = sum(1 for s in b_sayfa
                       if s["c0_yogunluk"] >= e or s["imza_yogunluk"] >= 10.0)
        print(f"  {e:>12.1f} {bk:>7} (%{100.0*bk/len(b_sayfa):>5.1f}) "
              f"{tk:>7} (%{100.0*tk/len(t_sayfa):>5.1f}) "
              f"{birlikte:>7} (%{100.0*birlikte/len(b_sayfa):>5.1f})")
    print()
    print("  OKUMA: 'BOZUK yakalanan' yuksek + 'TEMIZ yanlis-poz' SIFIR olan en")
    print("  DUSUK esik secilir. Yanlis pozitifin bedeli somuttur: o sayfanin")
    print("  dosyasi tam-sayfa OCR'a gider (~1.0-1.2 s/sayfa, DOSYA duzeyinde).")
    print()
    print("  UYARI -- 'bozuk orneklem' DB'de C0 chunk'i olan dosyalardan secildi;")
    print("  bu secim TABLO yoluna yanlidir (duzyazinin C0'ini cleaner siliyor,")
    print("  bkz. cleaner.py:_strip_junk / :121). Yakalama orani bu yuzden")
    print("  duzyazi icin OLCULMUS DEGILDIR; yanlis-pozitif olcumu ise saglamdir.")


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bozuk", type=int, default=4,
                    help="Bilinen-bozuk orneklem buyuklugu (parse PAHALI)")
    ap.add_argument("--temiz", type=int, default=4,
                    help="Bilinen-temiz orneklem buyuklugu (yanlis-pozitif olcumu)")
    ap.add_argument("--json", action="store_true", help="Ham JSON bas")
    args = ap.parse_args()

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database
    from ragintel.ingestion.parsing.adapter import ParseAdapter

    db = Database(DbSettings()).open()
    try:
        with db.connection() as conn:
            bozuk_liste = conn.execute(_SQL_BOZUK, {"n": args.bozuk}).fetchall()
            temiz_liste = conn.execute(_SQL_TEMIZ, {"n": args.temiz}).fetchall()

        # Config ÜRETİMDEN: db verilir ki backend/pdf_backend app_config'ten okunsun.
        adapter = ParseAdapter(db=db)
        print("=" * 100)
        print("GLIF ESIK PROBU  (C0 kolunun esigi -- parse anindaki metinden)")
        print("=" * 100)
        print(f"  backend={getattr(adapter.backend, 'name', '?')}  "
              f"bozuk orneklem={len(bozuk_liste)}  temiz orneklem={len(temiz_liste)}")
        print("  DB'ye YAZILMAZ: yalniz parse_path (saf dispatch) cagrilir.")

        gruplar = {}
        for ad, liste in (("bozuk", bozuk_liste), ("temiz", temiz_liste)):
            kayitlar = []
            for fn, yol, tip, _n in liste:
                t0 = time.monotonic()
                sayfalar, hata = _sayfalari_olc(adapter, yol, tip)
                kayitlar.append({"file_name": fn, "sayfalar": sayfalar, "hata": hata,
                                 "saniye": time.monotonic() - t0})
            gruplar[ad] = kayitlar
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()

    if args.json:
        print(json.dumps(gruplar, ensure_ascii=False, indent=2, default=str))
        return 0

    _print_grup("ORNEKLEM A · BILINEN BOZUK (DB'de C0 chunk'i olan dosyalar)",
                gruplar["bozuk"])
    _print_grup("ORNEKLEM B · BILINEN TEMIZ (C0 yok, imza yok)", gruplar["temiz"])
    _print_karar(gruplar["bozuk"], gruplar["temiz"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

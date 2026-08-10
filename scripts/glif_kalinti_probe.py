#!/usr/bin/env python
r"""Adım-4 ARTIK-ÖLÇÜM · glif onarımının KAÇIRDIĞI metin — SALT-OKUMA (yalnız SELECT).

NEDEN BU PROB VAR (ölçüldü 2026-08-10): korpus taraması C0 kontrol karakteri
taşıyan **760 chunk / 57 dosya** buldu (44599 chunk içinde). Bu metin gerçek
metin DEĞİL: alt-küme fontu kaydırması, kod noktaları ASCII−29. Kanıt file_id=24
chunk 297'de zincirin ucuna kadar götürüldü — kod noktalarına +29 eklendiğinde
5411'in gerçek değişiklik tablosu çıkıyor (5472/14.03.2006/26108, 5667, 5754,
6111, KHK 2011-662 ... beş bağımsız satır tuttu) ve aynı metin embed backend'ine
gönderildiğinde 500 yerine GEÇİYOR.

BU 760 CHUNK EMBED EDİLDİ. Yani vektör indeksinde anlamsız 760 vektör duruyor;
retrieval sessizce bozuk. Tek bir chunk (file_id=24/297) 500 verip `run`'ı
tümden durdurduğu için görünür oldu — geri kalanı gürültüsüzce geçti. GÖRÜNÜR
OLAN KUSUR, KUSURUN TAMAMI DEĞİL.

TEŞHİSİN DÜZELTİLMESİ: bunu önce `min_broken_page_ratio` maliyet kapısına
(bd3887b) yazmıştım -- YANLIŞ. file_id=24 zaten `encoding_repaired` damgası
aldı, yani dosya düzeyindeki oran kapısı GEÇTİ. Kaçıran şey bir kapı değil,
ÖLÇÜT: `glyph_repair.bozuk_sayfalar()` sayfayı yalnız **imza karakteri**
yoğunluğundan (Õ ú ÷ › ‹ ¤ ...) tanır ve eşiği 10/1000'dir. Sayısal bir tablo
sayfasında (tarih, sayı, Resmî Gazete numarası) kaynak metinde Türkçe diyakritik
YOKTUR; bozulma imza üretmez, sayfa "sağlam" görünür. Bu, modülün 83-88.
satırlarında ADI KONMUŞ kör noktanın ta kendisidir ("kaynağında hiç Türkçe
diyakritik olmayan bir sayfa aile-A ile bozulduğunda imza üretmez").

BU PROB KOD YAZMADAN ÖNCE ŞUNU ÖLÇER (ön-veri kuralı):
  A) Artık ne kadar? Dosya/chunk/sayfa dağılımı, onarım damgası olan dosyalar
     ayrı sayılır -- "onarıldı" damgalı dosyada artık kalması, ölçütün sayfayı
     kaçırdığının doğrudan kanıtıdır.
  B) İmza kolu bu sayfaları GÖREBİLİR MİYDİ? Her bozuk sayfa için imza
     yoğunluğu üretimdeki formülle hesaplanır ve 10/1000 eşiğiyle kıyaslanır.
     Eşiğin altındaysa mevcut ölçüt bu sayfayı yapısal olarak kaçırır; eşiği
     düşürmek çözüm değildir (meşru imza taşıyan temiz sayfalar 1.7-4.6
     ölçülmüştü, aralık kapanır).
  C) Tek bir onarım kuralı yetiyor mu? Her bozuk chunk'a +29 uygulanır ve
     sonucun basılabilir ASCII oranı ölçülür. Hepsi tek ailede mi, yoksa
     dosyaya göre farklı kaydırmalar mı var?
  D) Sayfa KARIŞIK mı? Aynı sayfada hem bozuk hem temiz chunk varsa sayfada
     iki font vardır; onarım sayfa düzeyinde tam-sayfa OCR yaptığı için bu
     durum ayrıca raporlanır (temiz kısım OCR'a verilerek kaybedilir mi?).

BU PROBUN KANITLAMADIĞI ŞEY (gizlenmiyor): B kolundaki imza yoğunluğu
`core_chunks.chunk_text` üzerinden hesaplanır; üretimdeki ölçüt ise PARSE
ANINDAKİ sayfa metnini görür (temizlik öncesi, sayfanın tamamı). Bu bir VEKİL
ölçümdür. "Yoğunluk 10'un çok altında" bulgusu ölçütün kaçırdığına güçlü işaret
sayılır, MATEMATİKSEL KANIT sayılmaz; kesin kanıt dosyayı yeniden parse edip
`bozuk_sayfalar()`i doğrudan çağırmaktır (bu prob DB'ye dokunmadığı için onu
yapmaz).

ÖLÇÜM-ZEMİNİ / GÜVENLİK:
  • Yalnız SELECT. DB/prod/config'e YAZMAZ, golden'a DOKUNMAZ, embed ETMEZ.
  • Kırpma sessiz değildir: her listede havuzun boyu ve kaçının kırpıldığı basılır.

Bare-metal (H200):
  cd /opt/ragintel && python scripts/glif_kalinti_probe.py
Konteynerde:
  docker cp scripts/glif_kalinti_probe.py ragintel-api:/app/p.py
  docker exec ragintel-api python /app/p.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

# Üretimdeki imza kümesiyle AYNI olmalı; kopyalanmaz, içe aktarılır. Kopya
# sürüklenirse prob üretimin gördüğünden başka bir şey ölçer.
from ragintel.ingestion.parsing.glyph_repair import imza_yogunlugu

IMZA_ESIK = 10.0  # glyph_repair.bozuk_sayfalar varsayılanı (imza_bin)
KAYDIRMA = 29     # ölçüldü 2026-08-10: kod noktaları ASCII−29


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


def _kontrol_sayisi(s: str) -> int:
    return sum(1 for c in s if ord(c) < 0x20 and c not in "\n\t\r")


def _glif_coz(s: str) -> str:
    """Bozuk alt-küme fontunun kaydırmasını GERİ ALIR (yalnız C0 bölgesi).

    ASCII-dışı glifler (\\x81 = ü gibi) dokunulmadan bırakılır: onların eşlemesi
    dosyaya özgüdür ve veriden türetmek ÖLÇÜLDÜ-ÇÜRÜDÜ (bkz. glyph_repair.py).
    """
    return "".join(
        chr(ord(c) + KAYDIRMA) if ord(c) < 0x20 and c not in "\n\t\r" else c
        for c in s)


def _asci_orani(s: str) -> float:
    """Çözülmüş metnin ne kadarı okunabilir ASCII? Aileyi bu oran ayırır."""
    if not s:
        return 0.0
    iyi = sum(1 for c in s if 0x20 <= ord(c) < 0x7F or c in "\n\t")
    return iyi / len(s)


# --- SALT-OKUMA sorgular -----------------------------------------------------

# C0 kontrol karakteri (\n \t \r hariç) gerçek metin katmanında BULUNMAZ.
# Bu yüzden ölçüt sıfır-yanlış-pozitiflidir: imza karakterlerinin tersine,
# meşru Türkçe metinde \x03 veya \x13 geçmesinin yolu yoktur.
_KOSUL = r"chunk_text ~ E'[\\x01-\\x08\\x0B\\x0C\\x0E-\\x1F]'"

_SQL_TOPLAM = f"""
SELECT count(*)                                        AS toplam_chunk,
       count(*) FILTER (WHERE k)                       AS bozuk_chunk,
       count(DISTINCT file_id)                         AS toplam_dosya,
       count(DISTINCT file_id) FILTER (WHERE k)        AS bozuk_dosya
FROM (SELECT file_id, {_KOSUL} AS k FROM core_chunks) s;
"""

# Dosya başına artık + onarım damgası. Damga qc_findings'ten okunur: onarım
# koştuysa 'encoding_*' bulgusu yazılır (bkz. ParseAdapter._glif_onar).
_SQL_DOSYA = f"""
WITH b AS (
    SELECT file_id, page_number,
           {_KOSUL} AS bozuk
    FROM core_chunks
), d AS (
    SELECT file_id,
           count(*)                                          AS chunk,
           count(*) FILTER (WHERE bozuk)                     AS bozuk_chunk,
           count(DISTINCT page_number)                       AS sayfa,
           count(DISTINCT page_number) FILTER (WHERE bozuk)  AS bozuk_sayfa
    FROM b GROUP BY file_id
), q AS (
    SELECT file_id, string_agg(DISTINCT finding, ',') AS bulgular
    FROM qc_findings
    WHERE finding LIKE 'encoding%%'
    GROUP BY file_id
)
SELECT f.file_name, f.status, d.chunk, d.bozuk_chunk, d.sayfa, d.bozuk_sayfa,
       q.bulgular
FROM d
JOIN core_files f USING (file_id)
LEFT JOIN q      USING (file_id)
WHERE d.bozuk_chunk > 0
ORDER BY d.bozuk_chunk DESC, f.file_name;
"""

# Bozuk chunk'ların kendisi: imza yoğunluğu ve +29 çözümü Python'da hesaplanır.
# Sayfa vekili olarak aynı sayfanın TÜM chunk'ları (temiz olanlar dahil)
# birleştirilir -- üretimdeki ölçüt sayfanın tamamını görür, yalnız bozuk
# parçasını değil. Yalnız bozuk parçaya bakmak yoğunluğu YAPAY olarak yükseltir.
_SQL_SAYFA = f"""
WITH b AS (
    SELECT file_id, page_number, chunk_text, {_KOSUL} AS bozuk
    FROM core_chunks
), s AS (
    SELECT file_id, page_number,
           string_agg(chunk_text, '\n' ORDER BY chunk_text)  AS sayfa_metin,
           count(*)                                          AS chunk,
           count(*) FILTER (WHERE bozuk)                     AS bozuk_chunk,
           sum(length(chunk_text))                           AS uzunluk
    FROM b GROUP BY file_id, page_number
)
SELECT f.file_name, s.page_number, s.chunk, s.bozuk_chunk, s.uzunluk, s.sayfa_metin
FROM s
JOIN core_files f USING (file_id)
WHERE s.bozuk_chunk > 0
ORDER BY s.bozuk_chunk DESC, f.file_name, s.page_number;
"""

_SQL_ORNEK = f"""
SELECT f.file_name, c.chunk_index, c.page_number, c.token_count, c.chunk_text
FROM core_chunks c JOIN core_files f USING (file_id)
WHERE {_KOSUL.replace('chunk_text', 'c.chunk_text')}
ORDER BY length(c.chunk_text) DESC
LIMIT %(ornek)s;
"""


def _tara(conn, *, ornek: int) -> dict:
    toplam = conn.execute(_SQL_TOPLAM).fetchone()
    dosyalar = conn.execute(_SQL_DOSYA).fetchall()
    sayfalar = conn.execute(_SQL_SAYFA).fetchall()
    ornekler = conn.execute(_SQL_ORNEK, {"ornek": ornek}).fetchall()

    sayfa_kayit = []
    for fn, sayfa_no, chunk, bozuk_chunk, uzunluk, metin in sayfalar:
        yog = imza_yogunlugu(metin or "")
        cozulmus = _glif_coz(metin or "")
        sayfa_kayit.append({
            "file_name": fn, "page_number": sayfa_no,
            "chunk": chunk, "bozuk_chunk": bozuk_chunk, "uzunluk": uzunluk,
            "imza_yogunluk": round(yog, 2),
            "imza_gorur": yog >= IMZA_ESIK,
            "karisik": bozuk_chunk < chunk,
            "kontrol": _kontrol_sayisi(metin or ""),
            "cozum_ascii": round(_asci_orani(cozulmus), 4),
            "cozum_ornek": _clip(cozulmus, 160),
        })
    return {
        "toplam": {"toplam_chunk": toplam[0], "bozuk_chunk": toplam[1],
                   "toplam_dosya": toplam[2], "bozuk_dosya": toplam[3]},
        "dosyalar": [{"file_name": fn, "status": st, "chunk": ch,
                      "bozuk_chunk": bc, "sayfa": sy, "bozuk_sayfa": bs,
                      "bulgular": bg}
                     for fn, st, ch, bc, sy, bs, bg in dosyalar],
        "sayfalar": sayfa_kayit,
        "ornekler": [{"file_name": fn, "chunk_index": ci, "page_number": pn,
                      "token_count": tc, "ham": metin, "cozum": _glif_coz(metin)}
                     for fn, ci, pn, tc, metin in ornekler],
    }


def _print_human(veri: dict, *, dosya_limit: int, sayfa_limit: int) -> None:
    t = veri["toplam"]
    print("=" * 100)
    print("GLIF ARTIK PROBU  (glif onariminin KACIRDIGI metin)")
    print("=" * 100)
    oran_c = 100.0 * t["bozuk_chunk"] / t["toplam_chunk"] if t["toplam_chunk"] else 0.0
    oran_d = 100.0 * t["bozuk_dosya"] / t["toplam_dosya"] if t["toplam_dosya"] else 0.0
    print(f"  chunk : {t['bozuk_chunk']:>6} / {t['toplam_chunk']:<6} bozuk  (%{oran_c:.2f})")
    print(f"  dosya : {t['bozuk_dosya']:>6} / {t['toplam_dosya']:<6} bozuk  (%{oran_d:.2f})")
    print("  olcut: C0 kontrol karakteri (\\x01-\\x1F, \\n\\t\\r haric). Mesru metinde")
    print("         bulunmaz -- imza karakterlerinin tersine yanlis-pozitifi yok.")

    # -- A: onarim damgasi olanlar ------------------------------------------
    d = veri["dosyalar"]
    damgali = [x for x in d if x["bulgular"]]
    print()
    print("-" * 100)
    print("BOLUM A · ONARIM KOSTU MU? (damga qc_findings.encoding_*)")
    print("-" * 100)
    print(f"  artikli dosya: {len(d)}   bunlardan onarim DAMGALI: {len(damgali)}"
          f"   damgasiz: {len(d) - len(damgali)}")
    if damgali:
        print("  DAMGALI dosyada artik kalmasi = olcut sayfayi KACIRDI (kapi degil,")
        print("  olcut kusuru). Damgasiz dosyada ise onarim hic tetiklenmemis.")

    print()
    print(f"  {'dosya':<44} {'durum':<10} {'chunk':>6} {'bozuk':>6} "
          f"{'sayfa':>6} {'bzk':>4}  damga")
    for x in d[:dosya_limit]:
        print(f"  {_clip(x['file_name'], 44):<44} {str(x['status'])[:10]:<10} "
              f"{x['chunk']:>6} {x['bozuk_chunk']:>6} {x['sayfa']:>6} "
              f"{x['bozuk_sayfa']:>4}  {_clip(x['bulgular'], 30)}")
    if len(d) > dosya_limit:
        print(f"  ... + {len(d) - dosya_limit} dosya daha (--dosya-limit ile artirilir)")

    # -- B: imza kolu bunlari gorebilir miydi? -------------------------------
    s = veri["sayfalar"]
    goren = [x for x in s if x["imza_gorur"]]
    kor = [x for x in s if not x["imza_gorur"]]
    print()
    print("-" * 100)
    print(f"BOLUM B · IMZA KOLU BU SAYFALARI GOREBILIR MIYDI? (esik {IMZA_ESIK}/1000)")
    print("-" * 100)
    print(f"  bozuk sayfa: {len(s)}   imza esigini GECEN: {len(goren)}   "
          f"esik ALTI (kor): {len(kor)}")
    if s:
        yogunluklar = sorted(x["imza_yogunluk"] for x in s)
        orta = yogunluklar[len(yogunluklar) // 2]
        print(f"  imza yogunlugu: min={yogunluklar[0]:.2f}  medyan={orta:.2f}  "
              f"max={yogunluklar[-1]:.2f}")
    if kor:
        pay = 100.0 * len(kor) / len(s)
        print(f"  -> bozuk sayfalarin %{pay:.1f}'i mevcut olcutun YAPISAL kor noktasinda.")
        print("     Esigi dusurmek cozum DEGIL: temiz ama mesru imza tasiyan sayfalar")
        print("     1.73-4.60 araliginda olculmustu, esik oraya inince aralik kapanir.")
    if goren:
        print(f"  -> {len(goren)} sayfa esigi GECIYOR; bunlar olcutun degil OCR")
        print("     asamasinin (_daha_iyi reddi) hesabina yazilmali. Ayrica bakilir.")
    print()
    print("  UYARI: buradaki yogunluk `core_chunks.chunk_text` uzerinden, yani")
    print("  temizlik SONRASI metinden hesaplandi. Uretimdeki olcut PARSE ANINDAKI")
    print("  sayfa metnini gorur. Bu bir VEKIL olcumdur -- guclu isaret, kanit degil.")

    print()
    print(f"  {'dosya':<40} {'sf':>4} {'chunk':>6} {'bzk':>4} {'imza':>7} "
          f"{'gorur':>6} {'karisik':>8} {'ascii':>6}")
    for x in s[:sayfa_limit]:
        print(f"  {_clip(x['file_name'], 40):<40} {x['page_number'] or 0:>4} "
              f"{x['chunk']:>6} {x['bozuk_chunk']:>4} {x['imza_yogunluk']:>7.2f} "
              f"{'EVET' if x['imza_gorur'] else 'hayir':>6} "
              f"{'EVET' if x['karisik'] else '-':>8} {x['cozum_ascii']:>6.3f}")
    if len(s) > sayfa_limit:
        print(f"  ... + {len(s) - sayfa_limit} sayfa daha (--sayfa-limit ile artirilir)")

    # -- C: tek kural yetiyor mu? -------------------------------------------
    print()
    print("-" * 100)
    print(f"BOLUM C · TEK ONARIM KURALI (+{KAYDIRMA}) YETIYOR MU?")
    print("-" * 100)
    if s:
        kova = Counter()
        for x in s:
            a = x["cozum_ascii"]
            kova["0.98+" if a >= 0.98 else
                 "0.90-0.98" if a >= 0.90 else
                 "0.70-0.90" if a >= 0.70 else "<0.70"] += 1
        for ad in ("0.98+", "0.90-0.98", "0.70-0.90", "<0.70"):
            if kova[ad]:
                print(f"  cozum sonrasi ascii orani {ad:<10} : {kova[ad]:>4} sayfa")
        print("  0.98+ = tek aile, tek kaydirma yeter. Dusuk oranlar ASCII-DISI glif")
        print("  (\\x81=u gibi) tasir; onlarin eslemesi dosyaya ozgudur ve veriden")
        print("  turetme OLCULDU-CURUDU -> o sayfalar OCR koluna kalir.")

    # -- D: karisik sayfa ----------------------------------------------------
    karisik = [x for x in s if x["karisik"]]
    print()
    print("-" * 100)
    print("BOLUM D · KARISIK SAYFA (ayni sayfada hem bozuk hem temiz chunk)")
    print("-" * 100)
    print(f"  karisik sayfa: {len(karisik)} / {len(s)}")
    if karisik:
        print("  Bu sayfalarda IKI font var. Onarim sayfa duzeyinde TAM-SAYFA OCR")
        print("  yaptigi icin temiz kisim da OCR'a gider; `_daha_iyi` yalnizca imza")
        print("  yogunluguna bakar ve temiz kisimda OCR kaybi olusursa GORMEZ.")

    # -- E: ornekler ---------------------------------------------------------
    print()
    print("-" * 100)
    print("BOLUM E · EN BUYUK ARTIK CHUNK'LAR (ham -> +29 cozum)")
    print("-" * 100)
    for o in veri["ornekler"]:
        print(f"  {_clip(o['file_name'], 50)}  chunk={o['chunk_index']} "
              f"s.{o['page_number']}  {o['token_count']} token")
        print(f"    HAM   : {_clip(repr(o['ham']), 150)}")
        print(f"    COZUM : {_clip(o['cozum'], 150)}")
        print()


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dosya-limit", type=int, default=60,
                    help="Bolum A'da basilacak dosya sayisi")
    ap.add_argument("--sayfa-limit", type=int, default=40,
                    help="Bolum B'de basilacak sayfa sayisi")
    ap.add_argument("--ornek", type=int, default=4,
                    help="Bolum E'de basilacak ornek chunk sayisi")
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
        _print_human(veri, dosya_limit=args.dosya_limit,
                     sayfa_limit=args.sayfa_limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

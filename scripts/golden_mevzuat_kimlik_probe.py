#!/usr/bin/env python
"""Adım-6 ÖN-VERİ · korpustaki mevzuat dosyalarının KİMLİĞİ — SALT-OKUMA (yalnız SELECT).

NEDEN BU PROB VAR: `golden_kaynak_esleme_probe.py` mevzuatı BAŞLIĞIYLA arıyordu.
Bu yöntem yapısal olarak kusurlu -- bir kanun/yönetmelik kendi metninde kendi
başlığını anmaz ("bu Kanun", "bu Yönetmelik" der). Dolayısıyla başlık deseni
kaynak metni DEĞİL, ona ATIF yapanı bulur. Ölçülen örnek (2026-08-10): 5411
sayılı Bankacılık Kanunu'nun en yüksek yoğunluğu 0.071'de kaldı, oysa kanunun
kendi PDF'i (269 chunk) korpusta duruyordu. Aynı şüphe Sermaye Yeterliliği için
de var: tepedeki dosya yönetmeliğin ŞERHİ.

İkinci kusur: yoğunluk tek başına kaynağı ayırmıyor. `mevzuat_1167.pdf` (39
chunk) BEŞ ilgisiz mevzuatta tepe yoğunlukta çıktı -- bu bir derleme/özet
dokümanının imzası, beşinin de kaynağı olamaz.

BU PROBUN ÖLÇTÜĞÜ ŞEY BAŞLIKTAN BAĞIMSIZ:
  (a) madde_orani -- `madde <sayı>` geçen chunk / toplam chunk. Yönetmelik
      maddelerden OLUŞUR; kitap onlara atıf yapar. Oran normatif metni ayırır
      ve mevzuatın adını hiç bilmeden çalışır.
  (b) resmi gazete imzası -- yayım künyesi normatif metinde bulunur.
  (c) chunk 0'ın HAM METNİ -- kapak sayfası. `mevzuat_1207.pdf`'in hangi
      yönetmelik olduğu çıkarımla değil OKUNARAK öğrenilir. Asıl ürün budur:
      dosya adı anlamsız olan korpusta ad->mevzuat kataloğu.

ÜÇÜNCÜ SORU (ayrı bulgu): korpusun %80.9'u <=2 chunk'lık dosya (776 dosya TEK
chunk). Bu ya gerçekten tek sayfalık BDDK tebliğleri ya da parse'ın neredeyse
hiçbir şey çıkaramadığı dosyalar -- ikisi ÇOK farklı şeyler. Bölüm C ikisini
token dağılımı + iki uçtan örnekle ayırır. Yorum okuyucuya bırakılmaz diye
hem en küçük hem en büyük örnekler basılır.

ÖLÇÜM-ZEMİNİ / GÜVENLİK:
  • Yalnız SELECT. DB/prod/config'e YAZMAZ, golden'a DOKUNMAZ.
  • Kırpma sessiz değildir: her listede havuzun kaç dosya olduğu ve kaçının
    kırpıldığı basılır.
  • `madde_orani` bir ORANDIR: küçük paydada anlamsızdır. Bölüm A'ya taban
    konur (--min-chunk), taban altı ayrıca sayılır.

Bare-metal (H200):
  cd /opt/ragintel && python scripts/golden_mevzuat_kimlik_probe.py
Konteynerde:
  docker cp scripts/golden_mevzuat_kimlik_probe.py ragintel-api:/app/p.py
  docker exec ragintel-api python /app/p.py
"""

from __future__ import annotations

import argparse
import json
import sys


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _clip(text: str | None, n: int = 300) -> str:
    if not text:
        return ""
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


# --- SALT-OKUMA sorgular -----------------------------------------------------

_SQL_ENVANTER = """
SELECT count(DISTINCT f.file_id)                                       AS dosya,
       count(c.chunk_id)                                               AS chunk,
       count(DISTINCT f.file_id) FILTER (WHERE f.status <> 'COMPLETED') AS tamamlanmamis
FROM core_files f
LEFT JOIN core_chunks c USING (file_id);
"""

# Normatiflik: `madde 1`, `madde 23` ... Kitapta da geçer ama SEYREK; yönetmelik
# maddelerden oluştuğu için oran bir mertebe farklıdır. Ayırt eden oran, varlık değil.
_SQL_KIMLIK = """
WITH t AS (
    SELECT file_id,
           count(*)                                              AS dosya_chunk,
           count(*) FILTER (WHERE chunk_text_norm ~ 'madde [0-9]+') AS madde_chunk,
           count(*) FILTER (WHERE chunk_text_norm LIKE '%%resmi gazete%%'
                               OR chunk_text_norm LIKE '%%resmî gazete%%') AS rg_chunk,
           count(*) FILTER (WHERE chunk_text_norm LIKE '%%yönetmelik%%'
                               OR chunk_text_norm LIKE '%%tebliğ%%')       AS tur_chunk
    FROM core_chunks
    GROUP BY file_id
), kapak AS (
    SELECT DISTINCT ON (file_id) file_id, chunk_text, page_number
    FROM core_chunks
    ORDER BY file_id, chunk_index
)
SELECT f.file_name, t.dosya_chunk, t.madde_chunk, t.rg_chunk, t.tur_chunk,
       k.page_number, k.chunk_text
FROM t
JOIN core_files f USING (file_id)
LEFT JOIN kapak  k USING (file_id)
WHERE t.dosya_chunk >= %(min_chunk)s
ORDER BY (t.madde_chunk::numeric / t.dosya_chunk) DESC, t.dosya_chunk DESC;
"""

_SQL_TABAN_ALTI = """
SELECT count(*) FROM (
    SELECT file_id FROM core_chunks GROUP BY file_id HAVING count(*) < %(min_chunk)s
) s;
"""

# Tek-chunk dosyalar: gerçek tebliğ mi, kırık parse mi? Karar token dağılımından.
_SQL_TEK_DAGILIM = """
WITH tek AS (
    SELECT file_id FROM core_chunks GROUP BY file_id HAVING count(*) = 1
)
SELECT count(*)                                              AS dosya,
       min(c.token_count)                                    AS en_az,
       percentile_disc(0.25) WITHIN GROUP (ORDER BY c.token_count) AS q1,
       percentile_disc(0.50) WITHIN GROUP (ORDER BY c.token_count) AS medyan,
       percentile_disc(0.75) WITHIN GROUP (ORDER BY c.token_count) AS q3,
       max(c.token_count)                                    AS en_cok,
       count(*) FILTER (WHERE c.token_count < 50)            AS elli_alti,
       count(*) FILTER (WHERE c.token_count < 200)           AS ikiyuz_alti
FROM core_chunks c
JOIN tek USING (file_id);
"""

# İki uçtan örnek: yalnız küçükleri basmak "korpus kırık", yalnız büyükleri
# basmak "korpus sağlam" dedirtir. İkisi birlikte basılır.
_SQL_TEK_ORNEK = """
WITH tek AS (
    SELECT file_id FROM core_chunks GROUP BY file_id HAVING count(*) = 1
)
(SELECT 'EN KUCUK' AS uc, f.file_name, c.token_count, c.page_number, c.chunk_text
 FROM core_chunks c JOIN tek USING (file_id) JOIN core_files f USING (file_id)
 ORDER BY c.token_count ASC LIMIT %(n)s)
UNION ALL
(SELECT 'EN BUYUK' AS uc, f.file_name, c.token_count, c.page_number, c.chunk_text
 FROM core_chunks c JOIN tek USING (file_id) JOIN core_files f USING (file_id)
 ORDER BY c.token_count DESC LIMIT %(n)s)
ORDER BY 1 DESC, 3;
"""


def _tara(conn, *, min_chunk: int, kimlik_limit: int, ornek: int) -> dict:
    kimlikler = [
        {"file_name": fn, "dosya_chunk": dc, "madde_chunk": mc, "rg_chunk": rg,
         "tur_chunk": tc, "kapak_sayfa": sp, "kapak": kt,
         "madde_orani": mc / max(dc, 1)}
        for fn, dc, mc, rg, tc, sp, kt in conn.execute(
            _SQL_KIMLIK, {"min_chunk": min_chunk}).fetchall()
    ]
    (taban_alti,) = conn.execute(_SQL_TABAN_ALTI, {"min_chunk": min_chunk}).fetchone()
    tek = conn.execute(_SQL_TEK_DAGILIM).fetchone()
    ornekler = [
        {"uc": uc, "file_name": fn, "token_count": tk, "page": pg, "metin": txt}
        for uc, fn, tk, pg, txt in conn.execute(_SQL_TEK_ORNEK, {"n": ornek}).fetchall()
    ]
    return {"kimlik_havuz": len(kimlikler),
            "kimlikler": kimlikler[:kimlik_limit],
            "taban_alti": taban_alti,
            "tek_dagilim": tek,
            "tek_ornek": ornekler}


def _print_human(env, veri: dict, *, min_chunk: int, kapak_uzunluk: int) -> None:
    dosya, chunk, tamamlanmamis = env
    print("BOLUM A  KORPUS ENVANTERI")
    print("=" * 100)
    print(f"  dosya : {dosya:,}    chunk : {chunk:,}")
    if tamamlanmamis:
        print(f"  UYARI: {tamamlanmamis} dosya COMPLETED degil -- korpus su an DEGISIYOR;")
        print("         asagidaki sayimlar ara-durumdur, kosum bitince tekrarlayin.")
    print()

    print("-" * 100)
    print("BOLUM B  MEVZUAT KIMLIGI -- hangi dosya NORMATIF metin, ve hangi mevzuat?")
    print("-" * 100)
    print("  madde_or = 'madde <sayi>' gecen chunk / dosyanin toplam chunk'i.")
    print("  Yonetmelik maddelerden OLUSUR (oran yuksek); kitap atif yapar (oran dusuk).")
    print("  Olcu mevzuatin ADINI bilmez -- baslik desenlerinin kusuru burada yok.")
    print("  rg = 'resmi gazete' gecen chunk (yayim kunyesi). Destekleyici isaret.")
    print("  KAPAK = chunk 0'in ham metni: dosyanin HANGI mevzuat oldugu buradan OKUNUR.")
    print()
    print(f"  Havuz: madde_orani'na gore siralanmis {veri['kimlik_havuz']:,} dosya "
          f"(dosya_chunk >= {min_chunk}).")
    print(f"  Taban altinda kalan {veri['taban_alti']:,} dosya bu listede YOK -- kucuk")
    print("  paydada oran anlamsizdir. Aranan mevzuat burada cikmazsa taban dusurulmeli.")
    if len(veri["kimlikler"]) < veri["kimlik_havuz"]:
        print(f"  Basilan: ilk {len(veri['kimlikler'])} / {veri['kimlik_havuz']} "
              "(--kimlik-limit ile artirilir).")
    print()
    for k in veri["kimlikler"]:
        print(f"  {_clip(k['file_name'], 60):<60} chunk={k['dosya_chunk']:>5,}  "
              f"madde_or={k['madde_orani']:.3f} ({k['madde_chunk']:,})  "
              f"rg={k['rg_chunk']:>3}  tur={k['tur_chunk']:>4}")
        print(f"      KAPAK s.{k['kapak_sayfa'] or '-'}: {_clip(k['kapak'], kapak_uzunluk)}")
    print()

    print("-" * 100)
    print("BOLUM C  TEK-CHUNK DOSYALAR -- gercek teblig mi, kirik parse mi?")
    print("-" * 100)
    d, en_az, q1, medyan, q3, en_cok, elli, ikiyuz = veri["tek_dagilim"]
    print(f"  TEK chunk'li dosya : {d:,}")
    print(f"  token dagilimi     : min={en_az}  q1={q1}  medyan={medyan}  q3={q3}  max={en_cok}")
    print(f"  token < 50         : {elli:,}  ({100.0 * elli / max(d, 1):.1f}%)  <-- bu bant")
    print("                       kirik parse suphesi: bir sayfalik teblig bile 300+ token")
    print(f"  token < 200        : {ikiyuz:,}  ({100.0 * ikiyuz / max(d, 1):.1f}%)")
    print()
    print("  Iki uctan ornek (yalnizca bir uc basmak hukmu onceden belirler):")
    for o in veri["tek_ornek"]:
        print(f"    [{o['uc']}] {_clip(o['file_name'], 52):<52} {o['token_count']:>6} tok  "
              f"s.{o['page'] or '-'}")
        print(f"        {_clip(o['metin'], 240)}")
    print()


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-chunk", type=int, default=15,
                    help="Bolum B tabani: bu kadar chunk'i olmayan dosya listelenmez "
                         "(kucuk paydada oran anlamsiz)")
    ap.add_argument("--kimlik-limit", type=int, default=60,
                    help="Bolum B'de basilacak dosya sayisi")
    ap.add_argument("--kapak-uzunluk", type=int, default=220,
                    help="Kapak metninden basilacak karakter")
    ap.add_argument("--ornek", type=int, default=5, help="Bolum C'de her uctan ornek")
    ap.add_argument("--json", action="store_true", help="Ham JSON bas")
    args = ap.parse_args()

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database

    db = Database(DbSettings()).open()
    try:
        with db.connection() as conn:
            env = conn.execute(_SQL_ENVANTER).fetchone()
            veri = _tara(conn, min_chunk=args.min_chunk,
                         kimlik_limit=args.kimlik_limit, ornek=args.ornek)
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()

    if args.json:
        print(json.dumps(
            {"envanter": {"dosya": env[0], "chunk": env[1], "tamamlanmamis": env[2]},
             "min_chunk": args.min_chunk, **veri},
            ensure_ascii=False, indent=2, default=str))
    else:
        _print_human(env, veri, min_chunk=args.min_chunk,
                     kapak_uzunluk=args.kapak_uzunluk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Korpustan tek dosya kaldirma -- VARSAYILAN SALT-OKUMA, silme ACIK BAYRAKLA.

NEDEN AYRI BIR ARAC: dusuk Turkce-diyakritik yogunlugu "Turkce degil" DEMEK
DEGILDIR. 2026-08-09 taramasi bunu gosterdi: 60._Yilinda_Turkiye_Bankalar_
Birligi_2.pdf yogunlugu 1.26/1000 (korpus medyani 71.8) ama adindan belli ki
Turkce -- diyakritikleri karakter bozulmasi yuzunden yok olmus. Boyle bir dosya
SILINMEZ, ONARILIR. Silmeden once ikisini ayirt etmek gerekir.

AYIRT EDICI TEST: Turkce'nin en sik islev sozcuklerinin cogunda diyakritik YOKTUR
(ve, bir, bu, ile, olan, da, de, olarak, daha, gibi, ancak, veya). Diyakritikler
yok edilmis bir Turkce belgede bu sozcukler SAGLAM kalir. Ingilizce bir belgede
ise the/and/of/to baskin cikar. Yani bu test bozulmadan ETKILENMEZ -- diyakritik
yogunlugunun aksine.

KOSUM (H200, venv + .env.h200 yuklu):
    python scripts/dosya_kaldir.py 994186.pdf           # yalniz inceler
    python scripts/dosya_kaldir.py 994186.pdf --sil     # inceler VE siler

SILME KAPSAMI: core_files'tan tek satir. Alti bagimli tablo (core_chunks ->
core_vectors, core_tables, core_figures, metrics_ingestion, qc_findings) ON
DELETE CASCADE ile kendiliginden temizlenir. Ham dosya diskte KALIR -- onu bu
arac silmez; yol ekranda basilir, karar kullanicinindir (silinmezse bir sonraki
ingestion kosumunda yeniden alinir).
"""

from __future__ import annotations

import argparse
import sys


def _c(*kod_noktalari: int) -> str:
    return "".join(chr(k) for k in kod_noktalari)


_TR = _c(0x0131, 0x00E7, 0x011F, 0x00F6, 0x015F, 0x00FC,
         0x0130, 0x00C7, 0x011E, 0x00D6, 0x015E, 0x00DC)
_BEKLENEN = _c(0x09, 0x0A, 0x0D) + _c(*range(0x20, 0x7F)) + _TR

# Diyakritigi OLMAYAN Turkce islev sozcukleri -- bozulmadan etkilenmezler.
_TR_KELIME = ["ve", "bir", "bu", "ile", "olan", "da", "de", "olarak",
              "daha", "gibi", "ancak", "veya", "kadar", "sonra"]
_EN_KELIME = ["the", "and", "of", "to", "in", "for", "is", "that",
              "with", "as", "by", "on", "are", "this"]

# file_id tasiyan ve CASCADE ile temizlenecek tablolar (dogrulama icin).
_BAGIMLI = ["core_chunks", "core_tables", "core_figures",
            "metrics_ingestion", "qc_findings"]


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _kelime_sayisi(conn, fid: int, kelimeler: list[str]) -> list[tuple[str, int]]:
    out = []
    for k in kelimeler:
        n = conn.execute(
            "SELECT coalesce(sum(regexp_count(lower(chunk_text), %s)), 0) "
            "FROM core_chunks WHERE file_id = %s;",
            (r"\y" + k + r"\y", fid)).fetchone()[0]
        out.append((k, int(n)))
    return out


def incele(conn, dosya_adi: str):
    satir = conn.execute(
        "SELECT file_id, file_name, file_type, file_size, language, status, "
        "       quality_score, source_path, checksum "
        "FROM core_files WHERE file_name = %s ORDER BY file_id;",
        (dosya_adi,)).fetchall()
    if not satir:
        print(f"  HATA: core_files'ta '{dosya_adi}' yok.")
        return None
    if len(satir) > 1:
        print(f"  UYARI: ayni adla {len(satir)} kayit var -> tek tek bakilmali:")
        for r in satir:
            print(f"    file_id={r[0]}  checksum={r[8][:16]}...  status={r[5]}")
        return None

    (fid, ad, tur, boyut, dil, durum, skor, yol, cks) = satir[0]
    print("=" * 100)
    print(f"S1 KIMLIK -- {ad}")
    print("=" * 100)
    print(f"  file_id   : {fid}")
    print(f"  tur/boyut : {tur} / {boyut:,} bayt")
    print(f"  dil       : {dil}")
    print(f"  durum     : {durum}   quality_score: {skor}")
    print(f"  checksum  : {cks}")
    print(f"  ham yol   : {yol}")

    n_chunk, kar, tr = conn.execute(
        "SELECT count(*), coalesce(sum(length(chunk_text)), 0), "
        "       coalesce(sum(regexp_count(chunk_text, %s)), 0) "
        "FROM core_chunks WHERE file_id = %s;",
        ("[" + _TR + "]", fid)).fetchone()
    n_chunk, kar, tr = int(n_chunk), int(kar), int(tr)
    print(f"  chunk     : {n_chunk:,}   karakter: {kar:,}   "
          f"Turkce diyakritik: {tr:,} ({1000*tr/kar if kar else 0:.2f}/1000)")

    # ------------------------------------------------------------------ S2
    print("\n" + "=" * 100)
    print("S2 DIL TESTI -- diyakritik bozulmasindan ETKILENMEYEN olcut")
    print("=" * 100)
    print("  Turkce islev sozcuklerinin cogunda diyakritik yoktur; diyakritigi")
    print("  yok edilmis bir Turkce belgede bunlar SAGLAM kalir.\n")
    tr_k = _kelime_sayisi(conn, fid, _TR_KELIME)
    en_k = _kelime_sayisi(conn, fid, _EN_KELIME)
    tr_top, en_top = sum(n for _k, n in tr_k), sum(n for _k, n in en_k)
    print(f"  {'TURKCE':<12}", "  ".join(f"{k}={n}" for k, n in tr_k if n))
    print(f"  {'INGILIZCE':<12}", "  ".join(f"{k}={n}" for k, n in en_k if n))
    bin_kar = kar / 1000 if kar else 1
    print(f"\n  toplam: TR={tr_top:,} ({tr_top/bin_kar:.2f}/1000)   "
          f"EN={en_top:,} ({en_top/bin_kar:.2f}/1000)")
    if tr_top > en_top * 2:
        hukum = "TURKCE (diyakritigi dusmus olabilir) -> SILME, ONAR"
    elif en_top > tr_top * 2:
        hukum = "TURKCE DEGIL -> kaldirma gerekcesi DOGRULANDI"
    else:
        hukum = "KARARSIZ -> asagidaki metin orneklerine bak"
    print(f"  HUKUM : {hukum}")

    # ------------------------------------------------------------------ S3
    print("\n" + "=" * 100)
    print("S3 METIN ORNEKLERI -- son soz bunlarindir")
    print("=" * 100)
    for idx, metin in conn.execute(
            "SELECT chunk_index, chunk_text FROM core_chunks "
            "WHERE file_id = %s ORDER BY chunk_index LIMIT 4;", (fid,)).fetchall():
        tek = " ".join((metin or "").split())[:320]
        print(f"\n  --- chunk {idx} ---\n  {tek}")

    # ------------------------------------------------------------------ S4
    print("\n\n" + "=" * 100)
    print("S4 SILINECEK SATIRLAR (CASCADE ile)")
    print("=" * 100)
    toplam = 0
    for t in _BAGIMLI:
        n = int(conn.execute(f"SELECT count(*) FROM {t} WHERE file_id = %s;",
                             (fid,)).fetchone()[0])
        toplam += n
        print(f"  {t:<22} {n:>8,}")
    nv = int(conn.execute(
        "SELECT count(*) FROM core_vectors v JOIN core_chunks c USING (chunk_id) "
        "WHERE c.file_id = %s;", (fid,)).fetchone()[0])
    print(f"  {'core_vectors':<22} {nv:>8,}  (core_chunks uzerinden)")
    print(f"  {'core_files':<22} {1:>8,}")
    print(f"\n  TOPLAM {toplam + nv + 1:,} satir")
    return fid, ad, yol


def sil(conn, fid: int, ad: str) -> int:
    print("\n" + "=" * 100)
    print("S5 SILINIYOR")
    print("=" * 100)
    n = conn.execute("DELETE FROM core_files WHERE file_id = %s;", (fid,)).rowcount
    print(f"  core_files silinen satir: {n}")
    # Iddia etme, DOGRULA: cascade gercekten temizledi mi?
    artik = 0
    for t in _BAGIMLI:
        k = int(conn.execute(f"SELECT count(*) FROM {t} WHERE file_id = %s;",
                             (fid,)).fetchone()[0])
        artik += k
        if k:
            print(f"  UYARI: {t} icinde {k} satir KALDI")
    print(f"  cascade artigi: {artik}  ->",
          "temiz" if artik == 0 else "ELDE KONTROL GEREKIR")
    return 0 if (n == 1 and artik == 0) else 1


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("dosya_adi")
    ap.add_argument("--sil", action="store_true",
                    help="incelemeden sonra core_files satirini SILER (CASCADE)")
    a = ap.parse_args()

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database

    db = Database(DbSettings()).open()
    try:
        with db.connection() as conn:
            sonuc = incele(conn, a.dosya_adi)
            if sonuc is None:
                return 1
            fid, ad, yol = sonuc
            if not a.sil:
                print("\n  (salt-okuma) Silmek icin ayni komutu --sil ile kosun.")
                return 0
            rc = sil(conn, fid, ad)
        print(f"\n  HAM DOSYA DISKTE KALDI: {yol}")
        print("  Silinmezse bir sonraki ingestion kosumunda yeniden alinir.")
        return rc
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

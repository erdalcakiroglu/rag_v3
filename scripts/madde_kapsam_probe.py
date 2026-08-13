#!/usr/bin/env python
"""Mevzuat dosyaları arasında MADDE KAPSAMI karşılaştırır + alıntı izi sürer.
SALT-OKUMA — yalnız SELECT.

NEDEN
    `mukerrer_baski_probe --kapsam` küme 1'i şuraya getirdi: yedi dosya aynı
    kanun, tutulacak aday (`5411 sayılı Bankacılık Kanunu.pdf`) en güncel metin
    (19 değişiklik işaretçisi, 2025). Asimetri 60 karakterlik pencerede çürüdü
    (ters yön %40 -> %72-77), yani sayfa farkı (117 vs 196-209) dizgiden
    geliyor: dipnot numaraları, ön kapak, OCR hatası.

    AMA tek bir golden alıntısı (`gs-bddk-h01`) beş eski baskıda çözülüyor,
    tutulacak dosyada ÇÖZÜLMÜYOR. Bu üç şeyden biri olabilir:
      (a) alıntı tutulanda CHUNK SINIRINA denk gelmiş (eşleşme kusuru),
      (b) o bölge bozuk (korpusta ölçülmüş bir kusur),
      (c) madde tutulanda GERÇEKTEN YOK (o zaman sayfa farkı gerçek).
    Rastgele metin penceresi bu üçünü ayıramaz. Silme kararı (c)'ye bağlı.

NEDEN MADDE NUMARASI
    Kanun metninde bütünlüğün doğal ölçüsü rastgele karakter penceresi değil,
    MADDE numarası kümesidir. Dizgi farkı, dipnot, ön kapak, hatta OCR gürültüsü
    madde başlıklarını topluca yok edemez. "Elenecek dosyalarda geçen ama
    tutulanda geçmeyen madde" kümesi boşsa, tutulan dosya kapsam olarak yeterli
    demektir; boş değilse eksik olan tam olarak listelenir.

NEDEN ALINTI İZİ
    Bir alıntı "bulunamadı" ise sebebi gösterilmeli. En uzun eşleşen ön ek ve
    son ek ikili aramayla bulunur; ön ek bir chunk'ın SONUNDA bitiyorsa sebep
    sınır kusurudur (metin vardır), hiçbir parça tutmuyorsa metin gerçekten
    yoktur.

KULLANIM
    python scripts/madde_kapsam_probe.py "5411 sayılı Bankacılık Kanunu.pdf" \
        "BankacilikKanunu_7.baski_2.pdf" "BankacilikKanunu_8.baski-web_2.pdf"
    # ilk dosya TUTULACAK olandır, geri kalanı karşılaştırılır

    python scripts/madde_kapsam_probe.py --golden-id gs-bddk-h01 \
        "5411 sayılı Bankacılık Kanunu.pdf"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Normalize metinde madde başlığı. Dipnot artığı ("24 madde: 8") yüzünden
# araya iki nokta/tire girebiliyor; sayı üç haneyi aşmaz (5411'de 171 + geçici).
_MADDE = re.compile(r"\bmadde\s*[:\-–—]?\s*(\d{1,3})\b")
_GECICI = re.compile(r"\bge[çc]ici\s+madde\s*[:\-–—]?\s*(\d{1,3})\b")
LADDER_MIN = 24            # bu uzunluğun altında eşleşme tesadüfi sayılır


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dosyalar", nargs="+",
                    help="ilk = TUTULACAK dosya, sonrakiler karşılaştırılacaklar "
                         "(ad parça eşleşmesi yeter)")
    ap.add_argument("--golden-id", action="append", default=[],
                    help="bu kaydın alıntılarının izi tutulacak dosyada sürülür "
                         "(birden çok kez verilebilir)")
    ap.add_argument("--golden", type=Path, default=Path("eval/golden/v1.jsonl"))
    ap.add_argument("--doc-scope", default="default")
    ap.add_argument("--chunk", type=int, action="append", default=[],
                    help="tutulacak dosyanın bu chunk_index'ini basar. Alıntı "
                         "izi KISMI dediğinde yürürlükteki lafız buradan okunur "
                         "(golden'ı güncel metne yeniden bağlamak için)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database
    from ragintel.text.normalize import normalize_for_quote

    db = Database(DbSettings()).open()
    try:
        with db.connection() as conn:
            cozum: list[tuple[str, int]] = []
            for desen in args.dosyalar:
                rows = conn.execute(
                    "SELECT file_id, file_name FROM core_files "
                    "WHERE doc_scope = %s AND status = 'COMPLETED' "
                    "AND file_name ILIKE %s ORDER BY file_name;",
                    (args.doc_scope, f"%{desen}%"),
                ).fetchall()
                if len(rows) != 1:
                    print(f"'{desen}' -> {len(rows)} dosyaya uyuyor; tek olmali.")
                    for r in rows:
                        print(f"   {r[1]}")
                    return 2
                cozum.append((rows[0][1], rows[0][0]))

            tut_ad, tut_id = cozum[0]

            def _maddeler(fid: int) -> tuple[set[int], set[int]]:
                rows = conn.execute(
                    "SELECT chunk_text_norm FROM core_chunks WHERE file_id = %s;",
                    (fid,),
                ).fetchall()
                metin = " ".join(r[0] or "" for r in rows)
                gecici = {int(m) for m in _GECICI.findall(metin)}
                # "geçici madde 5" _MADDE'ye de uyar. Küme farkı almak YANLIŞ
                # olurdu: 5411'de hem "madde 3" hem "geçici madde 3" var,
                # çıkarma gerçek maddeyi de silerdi. Metinden ÇIKARIP sayılır.
                normal = {int(m) for m in _MADDE.findall(_GECICI.sub(" ", metin))}
                return normal, gecici

            tut_normal, tut_gecici = _maddeler(tut_id)
            satirlar = []
            for ad, fid in cozum[1:]:
                normal, gecici = _maddeler(fid)
                satirlar.append({
                    "ad": ad,
                    "madde_n": len(normal), "gecici_n": len(gecici),
                    "eksik": sorted(normal - tut_normal),
                    "eksik_gecici": sorted(gecici - tut_gecici),
                    "fazla": sorted(tut_normal - normal),
                })

            # --- ALINTI İZİ ---
            izler = []
            if args.golden_id and args.golden.exists():
                istenen = set(args.golden_id)
                for satir in args.golden.read_text(encoding="utf-8").splitlines():
                    if not satir.strip():
                        continue
                    kayit = json.loads(satir)
                    if kayit["id"] not in istenen:
                        continue
                    gorulen = set()
                    for ev in kayit.get("gold_evidence", []):
                        norm = normalize_for_quote(ev["quote"])
                        if not norm or norm in gorulen:
                            continue
                        gorulen.add(norm)
                        izler.append(_iz_sur(conn, kayit["id"], norm, tut_ad, tut_id))

            govdeler = []
            for ci in args.chunk:
                row = conn.execute(
                    "SELECT chunk_index, page_number, chunk_text_norm "
                    "FROM core_chunks WHERE file_id = %s AND chunk_index = %s;",
                    (tut_id, ci),
                ).fetchone()
                if row:
                    govdeler.append({"chunk_index": row[0], "sayfa": row[1],
                                     "metin": row[2] or ""})

        if args.json:
            print(json.dumps({"tut": tut_ad, "kapsam": satirlar, "izler": izler,
                              "govdeler": govdeler}, ensure_ascii=False, indent=2))
            return 0

        print("=" * 78)
        print(f"MADDE KAPSAMI — tutulacak: {tut_ad}")
        print(f"tutulanda: {len(tut_normal)} madde, {len(tut_gecici)} gecici madde"
              + (f"  (en yuksek madde: {max(tut_normal)})" if tut_normal else ""))
        print("=" * 78)
        print(f"{'karsilastirilan':<46}{'madde':>7}{'gecici':>8}{'EKSIK':>7}")
        for s in satirlar:
            print(f"{s['ad'][:44]:<46}{s['madde_n']:>7}{s['gecici_n']:>8}"
                  f"{len(s['eksik']) + len(s['eksik_gecici']):>7}")
        bos = True
        for s in satirlar:
            if not s["eksik"] and not s["eksik_gecici"]:
                continue
            bos = False
            print(f"\n  -- {s['ad']} : tutulanda GECMEYEN madde --")
            if s["eksik"]:
                print(f"     madde: {', '.join(map(str, s['eksik']))}")
            if s["eksik_gecici"]:
                print(f"     gecici madde: {', '.join(map(str, s['eksik_gecici']))}")
        if bos and satirlar:
            print("\n  Eksik madde YOK — tutulacak dosya kapsam olarak yeterli.")

        if izler:
            print("\n" + "=" * 78)
            print(f"ALINTI IZI — tutulacak dosyada ({tut_ad})")
            print("=" * 78)
            for iz in izler:
                print(f"\n[{iz['id']}] {iz['alinti'][:100]}")
                if iz["tam"]:
                    print(f"   TAM BULUNDU  chunk_index={iz['chunk_index']} "
                          f"sayfa={iz['sayfa']}")
                    continue
                print(f"   tam eslesme YOK  (alinti {iz['uzunluk']} karakter)")
                print(f"   en uzun on ek : {iz['on_ek']} karakter"
                      + (f"  chunk_index={iz['on_ek_chunk']} "
                         f"sayfa={iz['on_ek_sayfa']} "
                         f"chunk sonuna kalan={iz['on_ek_kalan']}"
                         if iz["on_ek"] >= LADDER_MIN else ""))
                print(f"   en uzun son ek: {iz['son_ek']} karakter")
                print(f"   HUKUM: {iz['hukum']}")

        for g in govdeler:
            print("\n" + "=" * 78)
            print(f"CHUNK {g['chunk_index']}  (sayfa {g['sayfa']})  "
                  f"{len(g['metin'])} karakter")
            print("=" * 78)
            print(g["metin"])
        return 0
    finally:
        db.close()


def _iz_sur(conn, kimlik: str, norm: str, tut_ad: str, tut_id: int) -> dict:
    """Alıntının tutulan dosyada neden bulunmadığını sınıflar."""
    def _bul(parca: str):
        return conn.execute(
            "SELECT chunk_index, page_number, "
            "       length(chunk_text_norm) - position(%s in chunk_text_norm) "
            "       - length(%s) + 1 AS kalan "
            "FROM core_chunks WHERE file_id = %s "
            "AND position(%s in chunk_text_norm) > 0 "
            "ORDER BY chunk_index LIMIT 1;",
            (parca, parca, tut_id, parca),
        ).fetchone()

    tam = _bul(norm)
    if tam:
        return {"id": kimlik, "alinti": norm, "tam": True,
                "chunk_index": tam[0], "sayfa": tam[1], "uzunluk": len(norm)}

    def _en_uzun(yon: str) -> tuple[int, object]:
        """İkili arama: hangi uzunluğa kadar eşleşiyor."""
        alt, ust, iyi, iyi_row = 0, len(norm), 0, None
        while alt <= ust:
            orta = (alt + ust) // 2
            if orta == 0:
                break
            parca = norm[:orta] if yon == "on" else norm[-orta:]
            row = _bul(parca)
            if row:
                iyi, iyi_row, alt = orta, row, orta + 1
            else:
                ust = orta - 1
        return iyi, iyi_row

    on_ek, on_row = _en_uzun("on")
    son_ek, _ = _en_uzun("son")

    if on_ek < LADDER_MIN and son_ek < LADDER_MIN:
        hukum = ("METIN YOK — ne bas ne son tutuyor; madde tutulan dosyada "
                 "gercekten bulunmuyor (ya da bolge bozuk)")
    elif on_row is not None and on_row[2] is not None and on_row[2] <= 2:
        hukum = ("CHUNK SINIRI — on ek chunk'in tam SONUNDA bitiyor; metin var, "
                 "eslesme sinirdan kirilmis (silmeye engel DEGIL)")
    elif on_ek + son_ek >= len(norm) * 0.8:
        hukum = ("PARCALI — bas ve son ayri ayri var, ortasi farkli: dizgi/"
                 "dipnot gurultusu ya da METIN DEGISMIS; goze bakilmali")
    else:
        hukum = ("KISMI — yalnizca bir ucu tutuyor; buyuk olasilikla METIN "
                 "FARKLI (hukum degismis olabilir)")
    return {"id": kimlik, "alinti": norm, "tam": False, "uzunluk": len(norm),
            "on_ek": on_ek, "son_ek": son_ek,
            "on_ek_chunk": on_row[0] if on_row else None,
            "on_ek_sayfa": on_row[1] if on_row else None,
            "on_ek_kalan": on_row[2] if on_row else None,
            "hukum": hukum}


if __name__ == "__main__":
    raise SystemExit(main())

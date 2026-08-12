#!/usr/bin/env python
"""Golden v1 `synthesis` çöküşü teşhis probu — SALT-OKUMA (yalnız SELECT + arama).

NEDEN
    v1 baseline'ında (2026-08-12, hybrid, n=30) `synthesis` recall@10 = 0.1011,
    `single_fact` = 0.6786 — 6.7 kat fark. Karne bunu GÖSTERİR ama AÇIKLAMAZ.
    Üç aday sebep var ve recall sayısı üçünü ayırt edemez:

      (1) YAPISAL CEZA   gold iki ayrı dokümanda; `recall_at_k` paydası sert,
                         bir çıpayı bulmak 0.5 verir.
      (2) SIRALAMA       doğru chunk dönüyor ama k'nın altında kalıyor.
      (3) ÖLÇÜT KUSURU   sorgu metni chunk metniyle hiç örtüşmüyor; ölçtüğümüz
                         şey retriever değil setin kendisi olur.

    Bu ayrım tarihsel olarak PAHALI: aynı imza ([[rerank-ab-onveri]]) bir kez
    "retriever synthesis'te zayıf" diye okundu, kökü ölçü aracı çıktı. Aynı
    hatayı üçüncü kez yapmamak için sebep ÖLÇÜLÜR, tahmin edilmez.

NASIL AYIRIR
    Her ÇIPA (evidence quote) için iki ayrı soru sorar:
      a) gold chunk sıralamada kaçıncı?          → yoksa None
      b) o çıpanın DOSYASINDAN herhangi bir chunk sıralamada kaçıncı?
    İkisinin birleşimi sınıfı verir:

      BULUNDU     gold chunk <= --esik            → sorun yok
      GEC         gold chunk var ama esikten sonra → (2) SIRALAMA
      YANLIS_CHUNK dosya var, gold chunk yok       → çıpa yanlış chunk'ta /
                                                     chunk sınırı böldü
      DOSYA_YOK   dosya --derinlik içinde hiç yok  → (3) TERİM UYUŞMAZLIĞI

    (1)'i de test eder: kayıt başına "kaç çıpadan kaçı bulundu" dökülür.
    Hep-bir-bulundu-biri-hiç deseni = sorgu tek dokümana kilitleniyor.

    KONTROL KOLU ŞART: aynı ölçüm `single_fact` üzerinde de koşar. synthesis'in
    DOSYA_YOK oranı single_fact'inkiyle aynıysa sorun synthesis'e özgü değildir,
    setin geneline aittir — o hâlde teşhis de müdahale de başka yere gider.

GÜVENLİK / ZEMİN
    • Yalnız SELECT + retrieval araması. DB'ye, config'e, golden'a YAZMAZ.
    • Eşleme, benchmark'ın kullandığı ÜRETİM yolunun aynısı:
      `list_candidate_chunks` + `normalize_for_quote` (kopya mantık yazılmadı).
    • Retriever de aynı: `ServiceRetriever` — probe kendi arama yolunu kurmaz,
      yoksa ölçtüğü şey benchmark'ın ölçtüğü şey olmaz.

KULLANIM
    python scripts/synthesis_teshis_probe.py --from-file eval/golden/v1.jsonl
    python scripts/synthesis_teshis_probe.py --golden v1-bddk --derinlik 200 --json
"""

from __future__ import annotations

import argparse
import json
import sys

VARSAYILAN_DERINLIK = 200
VARSAYILAN_ESIK = 10


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _clip(metin: str, n: int = 88) -> str:
    tek = " ".join((metin or "").split())
    return tek if len(tek) <= n else tek[: n - 1] + "…"


def _chunk_dosyalari(conn, chunk_ids: list[int]) -> dict[int, tuple[str, int | None]]:
    """chunk_id -> (file_name, page). Sıralamadaki dosyaları isimlendirmek için."""
    if not chunk_ids:
        return {}
    rows = conn.execute(
        "SELECT c.chunk_id, f.file_name, c.page_number "
        "FROM core_chunks c JOIN core_files f USING (file_id) "
        "WHERE c.chunk_id = ANY(%s);",
        (list(chunk_ids),),
    ).fetchall()
    return {int(r[0]): (r[1], r[2]) for r in rows}


def _cipa_gold(conn, repo, normalize_for_quote, ev: dict, doc_scope: str) -> set[int]:
    """Tek bir evidence girdisinin gold chunk kümesi — benchmark'la birebir mantık."""
    cands = repo.list_candidate_chunks(
        conn, file_name=ev["file_name"], doc_scope=doc_scope,
        page=ev.get("page"), sheet=ev.get("sheet"))
    qn = normalize_for_quote(ev["quote"])
    return {c["chunk_id"] for c in cands if qn and qn in (c["chunk_text_norm"] or "")}


def _sinifla(gold_rank: int | None, dosya_rank: int | None, esik: int) -> str:
    if gold_rank is not None and gold_rank <= esik:
        return "BULUNDU"
    if gold_rank is not None:
        return "GEC"
    if dosya_rank is not None:
        return "YANLIS_CHUNK"
    return "DOSYA_YOK"


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    kaynak = ap.add_mutually_exclusive_group(required=True)
    kaynak.add_argument("--from-file", help="golden JSONL (DB'ye yazılmamış set de olur)")
    kaynak.add_argument("--golden", help="DB'deki set_version")
    ap.add_argument("--variant", default="hybrid", choices=("hybrid", "vector"))
    ap.add_argument("--derinlik", type=int, default=VARSAYILAN_DERINLIK,
                    help=f"retriever'dan istenecek top_k (vars. {VARSAYILAN_DERINLIK}); "
                         "DOSYA_YOK hükmü bu derinliğe GÖRELİDİR")
    ap.add_argument("--esik", type=int, default=VARSAYILAN_ESIK,
                    help=f"BULUNDU sayılmak için gerekli sıra (vars. {VARSAYILAN_ESIK} "
                         "— manşet metrik recall@10)")
    ap.add_argument("--kategori", action="append", default=[], metavar="AD",
                    help="yalnız bu kategoriler (yinelenebilir); "
                         "varsayılan synthesis + single_fact (kontrol kolu)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from ragintel.config.loader import load_config
    from ragintel.config.settings import DbSettings
    from ragintel.database import Database
    from ragintel.database.config_store import make_db_reader
    from ragintel.eval import repository as repo
    from ragintel.eval.retrieval_benchmark import (
        ServiceRetriever, from_db_rows, from_golden_records,
    )
    from ragintel.retrieval import RetrievalService
    from ragintel.text import normalize_for_quote

    kategoriler = set(args.kategori) or {"synthesis", "single_fact"}

    db = Database(DbSettings()).open()
    try:
        if args.from_file:
            from ragintel.eval.models import load_golden_jsonl
            kayitlar = from_golden_records(load_golden_jsonl(args.from_file))
            kaynak_ad = f"file:{args.from_file}"
        else:
            with db.connection() as conn:
                rows = repo.list_golden_records(conn, args.golden)
            if not rows:
                print(f"HATA: '{args.golden}' setinde kayıt yok.", file=sys.stderr)
                return 2
            kayitlar = from_db_rows(rows)
            kaynak_ad = f"db:{args.golden}"

        kayitlar = [r for r in kayitlar if r.answerable and r.category in kategoriler]
        if not kayitlar:
            print(f"HATA: {sorted(kategoriler)} kategorilerinde answerable kayıt yok.",
                  file=sys.stderr)
            return 2

        cfg = load_config(db_reader=make_db_reader(db))
        retriever = ServiceRetriever(RetrievalService(db=db, config=cfg), args.variant)

        rapor: list[dict] = []
        for rec in kayitlar:
            siralama = retriever.rank(rec.question, rec.doc_scope, args.derinlik)
            yer = {cid: i + 1 for i, cid in enumerate(siralama)}
            with db.connection() as conn:
                cid_dosya = _chunk_dosyalari(conn, siralama)
                cipalar = []
                for ev in rec.evidence:
                    gold = _cipa_gold(conn, repo, normalize_for_quote, ev, rec.doc_scope)
                    gold_rank = min((yer[c] for c in gold if c in yer), default=None)
                    dosya_rank = min(
                        (r for c, r in yer.items()
                         if cid_dosya.get(c, ("", None))[0] == ev["file_name"]),
                        default=None)
                    cipalar.append({
                        "file_name": ev["file_name"], "page": ev.get("page"),
                        "quote": _clip(ev["quote"], 60),
                        "gold_chunk_sayisi": len(gold),
                        "gold_rank": gold_rank, "dosya_rank": dosya_rank,
                        "sinif": _sinifla(gold_rank, dosya_rank, args.esik),
                    })
            # Çıpalar dosya bazında tekilleştirilir: mükerrer baskı aynı çıpayı
            # N kez tekrar eder, sınıf dağılımını sahte olarak şişirirdi.
            gorulen: set[tuple[str, str]] = set()
            tekil = []
            for c in cipalar:
                anahtar = (c["file_name"], c["quote"])
                if anahtar in gorulen:
                    continue
                gorulen.add(anahtar)
                tekil.append(c)
            rapor.append({
                "id": rec.id, "category": rec.category,
                "question": _clip(rec.question),
                "cipa_sayisi": len(tekil),
                "bulunan_cipa": sum(1 for c in tekil if c["sinif"] == "BULUNDU"),
                "cipalar": tekil,
            })

        sinif_sirasi = ["BULUNDU", "GEC", "YANLIS_CHUNK", "DOSYA_YOK"]
        ozet: dict[str, dict[str, int]] = {}
        for r in rapor:
            kova = ozet.setdefault(r["category"], {s: 0 for s in sinif_sirasi})
            for c in r["cipalar"]:
                kova[c["sinif"]] += 1

        sonuc = {"kaynak": kaynak_ad, "variant": args.variant,
                 "derinlik": args.derinlik, "esik": args.esik,
                 "ozet": ozet, "kayitlar": rapor}

        if args.json:
            print(json.dumps(sonuc, ensure_ascii=False, indent=2))
            return 0

        print(f"kaynak={kaynak_ad}  variant={args.variant}  "
              f"derinlik={args.derinlik}  BULUNDU esigi=rank<={args.esik}\n")

        print("=== ÇIPA SINIF DAĞILIMI (kategori bazında) ===")
        basliklar = "  ".join(f"{s:>13}" for s in sinif_sirasi)
        print(f"{'kategori':<20}{'cipa':>6}  {basliklar}")
        for kat in sorted(ozet):
            kova = ozet[kat]
            toplam = sum(kova.values())
            hucreler = "  ".join(
                f"{kova[s]:>4} %{100 * kova[s] / toplam:>5.1f}" if toplam else f"{0:>13}"
                for s in sinif_sirasi)
            print(f"{kat:<20}{toplam:>6}  {hucreler}")
        print("\nOKUMA: GEC = sıralama sorunu (chunk dönüyor, k altında kalıyor).")
        print("       YANLIS_CHUNK = dosya bulunuyor ama alıntı başka chunk'ta.")
        print(f"       DOSYA_YOK = dosya top-{args.derinlik} içinde HİÇ yok "
              "→ sorgu-metin terim uyuşmazlığı.")
        print("       synthesis ile single_fact dağılımı BENZERSE sorun "
              "synthesis'e özgü değildir.")

        print("\n=== ÇOK-ÇIPALI KAYITLARDA DAĞILIM (yapısal ceza testi) ===")
        cok = [r for r in rapor if r["cipa_sayisi"] > 1]
        if not cok:
            print("  (çok çıpalı kayıt yok)")
        else:
            for n in range(0, max(r["cipa_sayisi"] for r in cok) + 1):
                adet = sum(1 for r in cok if r["bulunan_cipa"] == n)
                if adet:
                    print(f"  {n} çıpası bulunan kayıt: {adet}")
            print("  'tam 1 bulundu' baskınsa sorgu TEK dokümana kilitleniyor;")
            print("  '0 bulundu' baskınsa sorun yapısal ceza DEĞİL, erişimin kendisi.")

        print("\n=== KAYIT KAYIT ===")
        for r in sorted(rapor, key=lambda x: (x["category"], x["id"])):
            print(f"\n[{r['category']}] {r['id']}  "
                  f"({r['bulunan_cipa']}/{r['cipa_sayisi']} çıpa bulundu)")
            print(f"  S: {r['question']}")
            for c in r["cipalar"]:
                gr = c["gold_rank"] if c["gold_rank"] is not None else "-"
                dr = c["dosya_rank"] if c["dosya_rank"] is not None else "-"
                print(f"  {c['sinif']:<13} gold#{gr:<5} dosya#{dr:<5} "
                      f"{c['file_name']} s.{c['page']} "
                      f"(gold chunk: {c['gold_chunk_sayisi']})")
                print(f"                \"{c['quote']}\"")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

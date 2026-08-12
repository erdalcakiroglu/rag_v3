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

NEDEN `StoreRetriever`, NEDEN `ServiceRetriever` DEĞİL
    `RetrievalService._effective_top_k` isteği `retrieval.max_top_k`'ya kırpar
    ve üretimde bu değer **20**'dir: `--derinlik 200` verilse bile servis 20
    döndürür (ilk koşumda `result_count: 20` diye görüldü). O hâlde DOSYA_YOK
    hükmü top-20'ye göreli olurdu — yani (2) ile (3) tam da ayrılamaz kalırdı,
    probe'un tek varlık sebebi buydu.
    Config'i değiştirmek seçenek DEĞİL (üretim ayarına ölçüm için dokunulmaz),
    bu yüzden sweep kolunun yolu kullanılır: `StoreRetriever` aynı store
    metodlarını çağırır ama parametreleri AÇIK alır. Parametreler üretim
    config'inden okunup birebir aktarılır — semantik aynı, yalnız kırpma yok.
    Kendini denetler: `--tutarlilik` (vars. açık) ilk 20 sırayı
    `ServiceRetriever` ile karşılaştırır. Ayrışma varsa GÜRÜLTÜYLE bildirir;
    sessizce farklı bir şey ölçmektense ölçümü şüpheli ilan etmek yeğdir.

GÜVENLİK / ZEMİN
    • Yalnız SELECT + retrieval araması. DB'ye, config'e, golden'a YAZMAZ.
    • Eşleme, benchmark'ın kullandığı ÜRETİM yolunun aynısı:
      `list_candidate_chunks` + `normalize_for_quote` (kopya mantık yazılmadı).

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


_SINIF_SIRASI = ("BULUNDU", "GEC", "YANLIS_CHUNK", "DOSYA_YOK")


def _daha_iyi(a: dict, b: dict) -> bool:
    """Aynı alıntının iki baskısından hangisi daha iyi konumda (sınıf, sonra sıra)."""
    ia, ib = _SINIF_SIRASI.index(a["sinif"]), _SINIF_SIRASI.index(b["sinif"])
    if ia != ib:
        return ia < ib
    return (a["gold_rank"] or 10**9) < (b["gold_rank"] or 10**9)


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
    ap.add_argument("--tutarlilik", action=argparse.BooleanOptionalAction, default=True,
                    help="ilk 20 sırayı ServiceRetriever ile karşılaştır (vars. açık)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from ragintel.config.loader import load_config
    from ragintel.config.settings import DbSettings
    from ragintel.database import Database
    from ragintel.database.config_store import make_db_reader
    from ragintel.eval import repository as repo
    from ragintel.eval.retrieval_benchmark import (
        ServiceRetriever, StoreRetriever, from_db_rows, from_golden_records,
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
        service = RetrievalService(db=db, config=cfg)
        rc = service.retrieval_cfg

        # Servis kırpması ölçümü sakatlardı (bkz. docstring): store yolu
        # ÜRETİM parametreleriyle, kırpmasız sürülür.
        embed_cache = {r.question: service._embed_query(r.question)[0] for r in kayitlar}
        retriever = StoreRetriever(
            service.store, embed_cache, name=f"{args.variant}-derin",
            method=args.variant,
            ef_search=int(rc.vector_ef_search),
            iterative_scan=str(rc.hnsw_iterative_scan),
            fusion=str(rc.hybrid_fusion), rrf_k=int(rc.hybrid_rrf_k),
            dense_weight=float(rc.hybrid_dense_weight),
            sparse_weight=float(rc.hybrid_sparse_weight),
            sparse_variant=str(rc.hybrid_sparse_variant))

        kontrol = ServiceRetriever(service, args.variant) if args.tutarlilik else None
        sapma: list[dict] = []

        rapor: list[dict] = []
        for rec in kayitlar:
            siralama = retriever.rank(rec.question, rec.doc_scope, args.derinlik)
            if kontrol is not None:
                # İki ayrı şey sorulur, yoksa sapmanın sebebi bilinemez:
                #   yol farkı mı (store vs service, AYNI k)?
                #   derinlik farkı mı (ANN aday havuzu k'ya göre değişir)?
                servis_k = int(rc.max_top_k)
                bekleyen = kontrol.rank(rec.question, rec.doc_scope, servis_k)
                sig_derin = siralama[:len(bekleyen)]
                if bekleyen != sig_derin:
                    sig_sig = retriever.rank(rec.question, rec.doc_scope, servis_k)
                    sapma.append({
                        "id": rec.id, "servis_n": len(bekleyen),
                        "ortak_derin": len(set(bekleyen) & set(sig_derin)),
                        "yol_ayni": sig_sig == bekleyen,
                    })
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
            # İKİ SAYIM, ikisi de gerekli:
            #  • cipalar   — evidence girdisi başına. recall'ün paydasıyla aynı
            #                (mükerrer baskı orada da ayrı ayrı sayılır).
            #  • alintilar — AYRI ALINTI başına, baskılar arası en iyi sıra.
            #                Sebep teşhisi bunu ister: aynı cümlenin 6 baskısı
            #                6 bağımsız kanıt değildir, dağılımı şişirir.
            per_alinti: dict[str, dict] = {}
            for c in cipalar:
                onceki = per_alinti.get(c["quote"])
                if onceki is None or _daha_iyi(c, onceki):
                    per_alinti[c["quote"]] = c
            alintilar = list(per_alinti.values())
            rapor.append({
                "id": rec.id, "category": rec.category,
                "question": _clip(rec.question),
                "cipa_sayisi": len(cipalar),
                "alinti_sayisi": len(alintilar),
                "bulunan_alinti": sum(1 for c in alintilar if c["sinif"] == "BULUNDU"),
                "cipalar": cipalar,
                "alintilar": alintilar,
            })

        sinif_sirasi = list(_SINIF_SIRASI)

        def _dagilim(alan: str) -> dict[str, dict[str, int]]:
            d: dict[str, dict[str, int]] = {}
            for r in rapor:
                kova = d.setdefault(r["category"], {s: 0 for s in sinif_sirasi})
                for c in r[alan]:
                    kova[c["sinif"]] += 1
            return d

        ozet = _dagilim("alintilar")
        ozet_cipa = _dagilim("cipalar")

        sonuc = {"kaynak": kaynak_ad, "variant": args.variant,
                 "derinlik": args.derinlik, "esik": args.esik,
                 "servis_max_top_k": int(rc.max_top_k),
                 "tutarlilik_sapmasi": sapma,
                 "ozet_alinti": ozet, "ozet_cipa": ozet_cipa,
                 "kayitlar": rapor}

        if args.json:
            print(json.dumps(sonuc, ensure_ascii=False, indent=2))
            return 0

        print(f"kaynak={kaynak_ad}  variant={args.variant}  "
              f"derinlik={args.derinlik}  BULUNDU esigi=rank<={args.esik}")
        print(f"servis max_top_k={int(rc.max_top_k)} (store yolu bu kirpmayi "
              "asar; parametreler uretim config'inden)")
        if kontrol is not None:
            if sapma:
                yol_ayni = sum(1 for s in sapma if s["yol_ayni"])
                print(f"\ntutarlilik: {len(sapma)} kayitta ilk {int(rc.max_top_k)} "
                      "sira store ile servis arasinda ayni degil.")
                print(f"  bunlarin {yol_ayni}/{len(sapma)}'inde store AYNI k ile "
                      "kosuldugunda servisle BIREBIR ayni ciktı")
                print("  → sapmanin sebebi yol degil DERINLIK (ANN aday havuzu "
                      "k'ya gore degisir); beklenen artefakt, siniflar gecerli.")
                if yol_ayni < len(sapma):
                    print(f"  !! {len(sapma) - yol_ayni} kayitta AYNI k'da bile "
                          "ayrisiyor — YOL FARKI var, o kayitlarin siniflari SUPHELI:")
                    for s in sapma:
                        if not s["yol_ayni"]:
                            print(f"     {s['id']}: ortak "
                                  f"{s['ortak_derin']}/{s['servis_n']}")
            else:
                print("tutarlilik: store ilk 20 == servis 20 (TUM kayitlarda)")
        print()

        def _tablo(d: dict[str, dict[str, int]], birim: str) -> None:
            basliklar = "  ".join(f"{s:>13}" for s in sinif_sirasi)
            print(f"{'kategori':<20}{birim:>6}  {basliklar}")
            for kat in sorted(d):
                kova = d[kat]
                toplam = sum(kova.values())
                hucreler = "  ".join(
                    f"{kova[s]:>4} %{100 * kova[s] / toplam:>5.1f}" if toplam
                    else f"{0:>13}" for s in sinif_sirasi)
                print(f"{kat:<20}{toplam:>6}  {hucreler}")

        print("=== SINIF DAĞILIMI · AYRI ALINTI başına (SEBEP TEŞHİSİ İÇİN BU) ===")
        print("(mükerrer baskılar tekilleştirildi; baskılar arası EN İYİ konum)")
        _tablo(ozet, "alinti")

        print("\n=== SINIF DAĞILIMI · evidence girdisi başına (recall paydasıyla aynı) ===")
        print("(baskılar ayrı ayrı sayılır; karnedeki sayıların türediği taban)")
        _tablo(ozet_cipa, "cipa")

        print("\nOKUMA: GEC = sıralama sorunu (chunk dönüyor, k altında kalıyor).")
        print("       YANLIS_CHUNK = dosya bulunuyor ama alıntı başka chunk'ta.")
        print(f"       DOSYA_YOK = dosya top-{args.derinlik} içinde HİÇ yok "
              "→ sorgu-metin terim uyuşmazlığı.")
        print("       synthesis ile single_fact dağılımı BENZERSE sorun "
              "synthesis'e özgü değildir.")

        print("\n=== YAPISAL CEZA TESTİ — çok ALINTILI kayıtlar ===")
        print("(mükerrer baskı burada sayılmaz: 6 baskı çok-dokümanlılık değildir)")
        cok = [r for r in rapor if r["alinti_sayisi"] > 1]
        if not cok:
            print("  (çok alıntılı kayıt yok)")
        else:
            for kat in sorted({r["category"] for r in cok}):
                grup = [r for r in cok if r["category"] == kat]
                dagilim = {}
                for r in grup:
                    dagilim[r["bulunan_alinti"]] = dagilim.get(r["bulunan_alinti"], 0) + 1
                yazi = ", ".join(f"{n} bulundu: {a}" for n, a in sorted(dagilim.items()))
                print(f"  {kat:<16} (n={len(grup)})  {yazi}")
            print("  'tam 1 bulundu' baskınsa sorgu TEK dokümana kilitleniyor;")
            print("  '0 bulundu' baskınsa sorun yapısal ceza DEĞİL, erişimin kendisi.")

        print("\n=== KAYIT KAYIT ===")
        for r in sorted(rapor, key=lambda x: (x["category"], x["id"])):
            print(f"\n[{r['category']}] {r['id']}  "
                  f"({r['bulunan_alinti']}/{r['alinti_sayisi']} alıntı bulundu; "
                  f"{r['cipa_sayisi']} evidence)")
            print(f"  S: {r['question']}")
            for c in r["alintilar"]:
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

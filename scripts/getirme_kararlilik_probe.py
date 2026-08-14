"""probe: aday GETİRME aşaması koşumdan koşuma aynı sonucu veriyor mu?

NEDEN VAR
    2026-08-13'te `rerank_ab_probe --pool 200` GENEL r@10 **0.682** ölçtü; aynı araç
    2026-08-14'te aynı golden, aynı config, aynı uçla **0.618** ölçtü. Rerank'i suçlamak
    kolaydı ama PASSTHROUGH kolu da kaydı (r@5 0.424→0.347, r@10 0.486→0.474) — o kol
    TEI'ye hiç dokunmuyor. Demek ki oynaklık sıralamada değil, ADAY GETİRMEDE.

    Bu, tek başına bir kusurdan daha kötüsü: ölçüm ZEMİNİ oynuyorsa iki koşumu
    kıyaslayan HER karar (rerank A/B, havuz derinliği, füzyon ağırlıkları) gürültüyü
    kazanç sanmış olabilir. Karne kendi paydasını ilan etmeli — bu probe da kendi
    tekrarlanabilirliğini ilan ediyor.

NE ÖLÇER — farkı ÜÇ AŞAMAYA ayırır
    1. EMBED: aynı soru `--tekrar` kez embed edilir. Vektörler birebir aynı mı?
    2. DB (vektör SABİT): ilk vektörle `search_hybrid` `--tekrar` kez koşar. Aynı
       chunk_id listesi mi? (Fark çıkarsa kök pgvector/planlayıcı tarafındadır —
       `ef_search`=80 < havuz=200 ve `iterative_scan` bunu yapabilir.)
    3. UÇTAN UCA (her tekrarda YENİDEN embed): 2'ye göre fazladan çıkan fark
       tam olarak embedding seğirmesinin getirme üzerindeki etkisidir.

    OKUMA: 2 ve 3 birebir kararlıysa 0.682→0.618 GÜRÜLTÜ DEĞİLDİR — iki tarih arasında
    bir DURUM değişmiştir (indeks, istatistik, model) ve ayrıca aranmalıdır.

MALİYET / GÜVENLİK
    SALT OKUR: yalnız SELECT + embed çağrısı. DB'ye, config'e, golden'a YAZMAZ.
    Embedder'ı `--tekrar × soru` kez çağırır (Ollama seri — [[kapasite-seri-lock]]);
    canlı karne koşarken çalıştırmayın.

KULLANIM
    python -u scripts/getirme_kararlilik_probe.py --golden v1-bddk --pool 200 --tekrar 3
"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="getirme_kararlilik_probe")
    ap.add_argument("--golden", default="v1-bddk", help="DB set_version")
    ap.add_argument("--pool", type=int, default=200,
                    help="Getirilecek aday sayısı (rerank havuzuyla AYNI olmalı)")
    ap.add_argument("--tekrar", type=int, default=3, help="Her aşamada koşum sayısı (>=2)")
    ap.add_argument("--ust", type=int, default=20,
                    help="'Tepe' karşılaştırma derinliği (karneye giren kısım)")
    args = ap.parse_args(argv)
    if args.tekrar < 2:
        print("HATA: --tekrar en az 2 olmalı (kararlılık tek koşumla ölçülemez).",
              file=sys.stderr)
        return 2

    from ragintel.config.loader import load_config
    from ragintel.config.settings import DbSettings
    from ragintel.database import Database
    from ragintel.database.config_store import make_db_reader
    from ragintel.eval import repository as repo
    from ragintel.eval.retrieval_benchmark import from_db_rows
    from ragintel.retrieval import RetrievalService
    from ragintel.text import normalize_for_search

    db = Database(DbSettings()).open()
    try:
        cfg = load_config(db_reader=make_db_reader(db))
        service = RetrievalService(db=db, config=cfg)
        rc = service.retrieval_cfg

        with db.connection() as conn:
            rows = repo.list_golden_records(conn, args.golden)
        if not rows:
            print(f"HATA: '{args.golden}' golden set DB'de yok.", file=sys.stderr)
            return 2
        kayitlar = [r for r in from_db_rows(rows) if r.answerable]

        # Payda AÇIKÇA ilan edilir: hangi zeminde, kaç soruda, kaç tekrarla ölçtük.
        print(f"golden={args.golden} · soru={len(kayitlar)} (answerable) · havuz={args.pool} "
              f"· tepe={args.ust} · tekrar={args.tekrar}")
        print(f"ZEMİN: ef_search={rc.vector_ef_search} · iterative_scan={rc.hnsw_iterative_scan} "
              f"· fusion={rc.hybrid_fusion} · rrf_k={rc.hybrid_rrf_k} "
              f"· dense={rc.hybrid_dense_weight} · sparse={rc.hybrid_sparse_weight} "
              f"· sparse_variant={rc.hybrid_sparse_variant}")
        if args.pool > int(rc.vector_ef_search):
            print(f"NOT: havuz {args.pool} > ef_search {rc.vector_ef_search} — HNSW arama "
                  f"listesi istenen aday sayısından küçük; kuyruk buradan oynayabilir.")
        print()

        ortak = dict(
            allowed_doc_scopes=None,            # kayıt bazında doldurulur
            top_k=args.pool,
            ef_search=int(rc.vector_ef_search),
            iterative_scan=str(rc.hnsw_iterative_scan),
            fusion_strategy=str(rc.hybrid_fusion),
            rrf_k=int(rc.hybrid_rrf_k),
            dense_weight=float(rc.hybrid_dense_weight),
            sparse_weight=float(rc.hybrid_sparse_weight),
            sparse_variant=str(rc.hybrid_sparse_variant),
        )

        def getir(rec, vektor: list[float]) -> list[int]:
            p = dict(ortak)
            p["allowed_doc_scopes"] = [rec.doc_scope]
            satir = service.store.search_hybrid(
                query=rec.question, query_vector=vektor,
                normalized_query=normalize_for_search(rec.question), **p)
            return [int(r["chunk_id"]) for r in satir]

        # --- 1) EMBED kararlılığı -------------------------------------------------
        print("[1/3] embed kararlılığı…")
        vektorler: dict[str, list[list[float]]] = {}
        for rec in kayitlar:
            vektorler[rec.id] = [service._embed_query(rec.question)[0]
                                 for _ in range(args.tekrar)]
        embed_oynak, embed_max = 0, 0.0
        for vs in vektorler.values():
            d = max(max(abs(a - b) for a, b in zip(vs[0], v)) for v in vs[1:])
            embed_max = max(embed_max, d)
            if d > 0.0:
                embed_oynak += 1
        print(f"  aynı soruyu {args.tekrar} kez embed: değişen {embed_oynak}/{len(kayitlar)} "
              f"soru · en büyük eleman farkı {embed_max:.3e}")

        # --- 2) DB kararlılığı (vektör SABİT) ------------------------------------
        print(f"[2/3] DB kararlılığı (vektör sabit, {args.tekrar} koşum)…")
        db_sonuc = {rec.id: [getir(rec, vektorler[rec.id][0]) for _ in range(args.tekrar)]
                    for rec in kayitlar}
        db_rapor, db_kararli = _karsilastir(db_sonuc, args.ust)
        print("  " + db_rapor)

        # --- 3) UÇTAN UCA (her koşumda yeniden embed) ----------------------------
        print(f"[3/3] uçtan uca (her koşumda yeniden embed, {args.tekrar} koşum)…")
        uc_sonuc = {rec.id: [getir(rec, vektorler[rec.id][i]) for i in range(args.tekrar)]
                    for rec in kayitlar}
        uc_rapor, uc_kararli = _karsilastir(uc_sonuc, args.ust)
        print("  " + uc_rapor)

        # --- OKUMA ----------------------------------------------------------------
        print("\n--- OKUMA ---")
        if db_kararli and uc_kararli:
            print("GETİRME KARARLI: aynı gün, aynı zeminde tekrar aynı adayları veriyor.\n"
                  "  ⇒ 0.682 → 0.618 GÜRÜLTÜ DEĞİL. İki tarih arasında bir DURUM değişti "
                  "(indeks/istatistik/embed modeli/uç). Fark ayrıca aranmalı; tek koşumluk "
                  "karne kıyası bu zeminde geçerlidir.")
            return 0
        if db_kararli and not uc_kararli:
            print("KÖK: EMBEDDING SEĞİRMESİ. DB aynı vektöre aynı adayları veriyor, ama sorgu "
                  "iki kez embed edilince aday listesi değişiyor.\n"
                  "  ⇒ Tek koşumluk A/B kıyasları bu oynaklık kadar gürültü taşır; "
                  "karar eşiği bunun ÜSTÜNDE olmalı.")
            return 1
        print("KÖK: DB/GETİRME KATMANI. Vektör SABİTken bile aday listesi koşumdan koşuma "
              f"değişiyor (ef_search={rc.vector_ef_search} < havuz={args.pool} + "
              f"iterative_scan={rc.hnsw_iterative_scan} bunu yapabilir).\n"
              "  ⇒ Derin havuz ölçümleri tekrarlanabilir değil; ef_search havuzu "
              "karşılayacak kadar yükseltilmeden A/B kıyası güvenilmez.")
        return 1
    finally:
        db.close()


def _karsilastir(sonuc: dict[str, list[list[int]]], ust: int) -> tuple[str, bool]:
    """Tekrarları 1. koşuma göre kıyasla: tepe sırası ve havuz ÜYELİĞİ ayrı raporlanır.

    İkisi AYRI: tepe değişmeden havuz kuyruğu oynayabilir (rerank'in yukarı çekeceği
    altın chunk havuzda ya vardır ya yoktur) — 0.682/0.618 farkının imzası tam buydu.
    """
    tepe_degisen, havuz_degisen, en_kotu_ortak = 0, 0, 1.0
    for listeler in sonuc.values():
        ilk = listeler[0]
        if any(l[:ust] != ilk[:ust] for l in listeler[1:]):
            tepe_degisen += 1
        k0 = set(ilk)
        for l in listeler[1:]:
            k = set(l)
            if k != k0:
                havuz_degisen += 1
                break
        for l in listeler[1:]:
            birlesim = len(set(ilk) | set(l))
            if birlesim:
                en_kotu_ortak = min(en_kotu_ortak, len(set(ilk) & set(l)) / birlesim)
    n = len(sonuc)
    metin = (f"tepe DEĞİŞEN {tepe_degisen}/{n} (ilk {ust}) · "
             f"havuz DEĞİŞEN {havuz_degisen}/{n} · en düşük havuz örtüşmesi "
             f"{en_kotu_ortak:.3f} (Jaccard)")
    return metin, (tepe_degisen == 0 and havuz_degisen == 0)


if __name__ == "__main__":
    if os.name == "nt":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

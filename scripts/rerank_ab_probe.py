#!/usr/bin/env python
"""İP-3.6 §7 rerank A/B ön-veri probu — TEI rerank RETRIEVAL kalitesini ölçer.

İki kol, golden v0 üzerinde, judge'sız/deterministik:
  A) passthrough  — rerank YOK (mevcut üretim davranışı, §7 taban zemini)
  B) tei-rerank   — bge-reranker-v2-m3 (TEI, localhost:8085)

AYNI 20 adaylık havuz iki kolda da retrieve edilir; rerank YALNIZCA sıralamayı
değiştirir. Bu yüzden:
  • recall@20  → iki kolda ÖZDEŞ olmalı (havuz aynı) = sağlık kontrolü.
  • recall@5 / MRR / nDCG → rerank'in doğru chunk'ı TEPEYE çekişini ölçer.
Hedef metrik: multi_hop recall@5 (§7 taban 0.30).

ÖLÇÜM-ZEMİNİ DİSİPLİNİ:
  1. DB/prod/config'e DOKUNMAZ. StoreRetriever AÇIK rerank_fn ile sürülür →
     DB-pinli `retrieval.rerank_backend` baypas edilir; TEI URL yalnız bu
     process'in ENV'inde. KALICI DEĞİŞİKLİK YOK — salt ölçüm.
  2. Baseline üretim hybrid parametreleriyle koşar (ef_search/rrf_k/ağırlıklar
     cfg'den) → sayı KARNE zeminiyle kıyaslanabilir, sweep-varsayılanı sızmaz.
  3. Kalite ölçümü ASLA fail-open etmez. Prod `_tei_rerank` timeout'ta SESSİZCE
     passthrough'a düşer (erişilebilirlik doğrusu) → ama ölçümde bu B=A YALANINI
     üretir. Prob rerank'i KENDİ İÇİNDE, fallback'siz, cömert timeout'la yapar →
     her hata GÜRÜLTÜLÜ ÇÖKER (sessiz düşüş yok). Ayrıca preflight ile koşudan
     önce TEI'nin gerçekten sıraladığı doğrulanır; geçmezse exit 2.

TEI-CPU notu: bge-reranker-v2-m3 batch>4 desteklemez → 20 aday = 5 seri batch,
CPU'da tek sorgu birkaç saniye sürebilir. `--rerank-timeout` (vars. 120s) bunun
için cömert; config'in ~5s'lik rerank_timeout_sec'i ölçümü zamanaşımına uğratırdı.

İSTEMCİ PARTİLEMESİ (`--tei-batch`, vars. 32): TEI'nin `--max-client-batch-size`
sınırı aşılırsa endpoint **413 Payload Too Large** döner — `--pool 50` ve
`--pool 100` ilk koşumda tam bu yüzden çöktü. Havuz parti parti skorlanır.
Bu bir yaklaşıklama DEĞİLDİR: cross-encoder skoru (query, text) çifti başına
bağımsız hesaplanır, aynı çift hangi partide olursa olsun aynı skoru alır →
parçalayıp skora göre birleştirmek tek-istekle BİREBİR aynı sıralamayı verir.
Değişen tek şey HTTP çağrısı sayısı (gecikme), ölçülen kalite değil.

Konteynerde koşulur (DB + bge-m3 embedder + TEI hepsi host-network):
  docker exec <ragintel-api> python /app/scripts/rerank_ab_probe.py
"""

from __future__ import annotations

import argparse
import os
import sys


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _preflight(url: str, timeout: float) -> tuple[bool, str]:
    """TEI gerçekten rerank ediyor mu? Bilinen çiftte alakalı metin TEPEDE mi?

    Sessiz passthrough-fallback'i koşudan ÖNCE yakalar (ölçüm-zemini güvencesi).
    Koşu ile AYNI timeout kullanılır ki CPU yavaşlığı burada da temsil edilsin.
    """
    import httpx

    payload = {
        "query": "Sözleşmenin fesih bildirim süresi kaç gündür?",
        "texts": [
            "Bu belge kedi bakımı hakkındadır ve hiçbir hukuki içerik taşımaz.",
            "Sözleşmenin feshi için karşı tarafa en az otuz (30) gün önceden yazılı bildirim yapılması zorunludur.",
            "Toplantı tutanağı: kahve molası 15.00'te verilecektir.",
        ],
    }
    try:
        resp = httpx.post(f"{url.rstrip('/')}/rerank", json=payload, timeout=timeout)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — preflight her hatada ölçmeyi durdurur
        return False, f"TEI erişilemedi/hatalı: {exc}"
    body = resp.json()
    results = body if isinstance(body, list) else body.get("results")
    if not isinstance(results, list) or not results:
        return False, f"TEI yanıtı beklenmedik biçimde: {body!r}"
    top = max(results, key=lambda x: float(x["score"]))
    if int(top["index"]) != 1:
        return False, (f"alakalı metin (idx=1) TEPEDE değil; top idx={top['index']} "
                       f"score={float(top['score']):.3f} — sözleşme/servis uyuşmuyor")
    return True, f"alakalı metin tepede (idx=1, score={float(top['score']):.3f})"


def _delta_table(res_a: dict, res_b: dict) -> str:
    """A→B yan-yana Δ; multi_hop recall@5 vurgulu. (+) rerank iyileştirdi."""
    ks = res_a["ks"]
    cols = [f"recall@{k}" for k in ks] + ["mrr"] + [f"ndcg@{k}" for k in ks]
    L = ["=== A/B Δ  (B: tei-rerank  −  A: passthrough) — (+) rerank İYİLEŞTİRDİ ==="]

    def _row(label: str, ma: dict, mb: dict, suffix: str = "") -> str:
        parts = [f"n={ma.get('n', '?')}"]
        for c in cols:
            d = mb[c] - ma[c]
            parts.append(f"{c} {ma[c]:.3f}→{mb[c]:.3f} ({d:+.3f})")
        return label.ljust(20) + "  ".join(parts) + suffix

    L.append(_row("GENEL", res_a["aggregate"]["overall"], res_b["aggregate"]["overall"]))
    cats_a = res_a["aggregate"]["by_category"]
    cats_b = res_b["aggregate"]["by_category"]
    for cat in sorted(cats_a):
        suffix = "   ← HEDEF (§7 taban recall@5=0.300)" if cat == "multi_hop" else ""
        L.append(_row(cat, cats_a[cat], cats_b.get(cat, cats_a[cat]), suffix))
    L.append("")
    L.append("NOT: recall@20 iki kolda ÖZDEŞ beklenir (havuz aynı, rerank yalnız sıralar). "
             "Eğer recall@5 de A'ya EŞİTse → TEI sessizce passthrough'a düşmüş olabilir.")
    return "\n".join(L)


def _kayit_tablosu(res_a: dict, res_b: dict) -> str:
    """KAYIT bazında A→B; en çok BOZULAN üstte.

    NEDEN TOPLAM YETMİYOR: pool 50'de GENEL recall@5 düştü (0.496→0.462) ama
    recall@10 yükseldi. İki ayrı dünya bu ortalamayı verir — (a) birkaç kayıt
    sert bozuldu, gerisi iyileşti; (b) bozulma tabana yayıldı. (a) ise bozulan
    kayda bakılıp sebebi görülebilir ve rerank sevk edilebilir; (b) ise
    cross-encoder bu korpusla uyuşmuyor demektir. Ortalama ikisini ayırmaz.
    Ek hesap YOK: `per_record` iki kolda da zaten üretiliyor, yalnız basılmıyordu.
    """
    ka = {r["id"]: r for r in res_a["per_record"]}
    kb = {r["id"]: r for r in res_b["per_record"]}
    ortak = sorted(set(ka) & set(kb))
    L = ["=== KAYIT BAZINDA A→B  (− = rerank BOZDU) ==="]

    def _say(m: str) -> str:
        iyi = sum(1 for i in ortak if kb[i][m] - ka[i][m] > 1e-9)
        kot = sum(1 for i in ortak if kb[i][m] - ka[i][m] < -1e-9)
        return f"{m}: iyilesen {iyi}  bozulan {kot}  ayni {len(ortak) - iyi - kot}"

    for m in ("recall@5", "recall@10", "mrr"):
        L.append("  " + _say(m))
    L.append("")
    L.append(f"{'kayit':<16}{'kategori':<20}{'gold_n':>7}"
             f"{'r@5 A→B':>18}{'r@10 A→B':>18}{'Δr@10':>9}")
    for i in sorted(ortak, key=lambda i: kb[i]["recall@10"] - ka[i]["recall@10"]):
        d = kb[i]["recall@10"] - ka[i]["recall@10"]
        if abs(d) <= 1e-9 and abs(kb[i]["recall@5"] - ka[i]["recall@5"]) <= 1e-9:
            continue  # iki metrikte de kıpırdamayan kaydı basma (gürültü azalt)
        L.append(f"{i:<16}{kb[i]['category']:<20}{kb[i]['gold_n']:>7}"
                 f"{ka[i]['recall@5']:>9.3f}→{kb[i]['recall@5']:<8.3f}"
                 f"{ka[i]['recall@10']:>9.3f}→{kb[i]['recall@10']:<8.3f}{d:>+9.3f}")
    L.append("")
    L.append("(iki metrikte de degismeyen kayitlar listelenmez; sayimlar TUM kayitlar uzerinden)")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(prog="rerank_ab_probe")
    ap.add_argument("--golden", default="v0", help="DB set_version (varsayılan v0)")
    ap.add_argument("--tei-url", default="http://localhost:8085", help="TEI rerank kök URL")
    ap.add_argument("--pool", type=int, default=20, help="Rerank aday havuzu (varsayılan 20)")
    ap.add_argument("--tei-batch", type=int, default=32,
                    help="Tek TEI isteğine konacak azami metin (TEI "
                         "--max-client-batch-size; aşılırsa 413). Skoru DEĞİŞTİRMEZ.")
    ap.add_argument("--rerank-timeout", type=float, default=120.0,
                    help="TEI rerank HTTP timeout sn (CPU'da 20 aday yavaş; vars. 120)")
    ap.add_argument("--json", action="store_true", help="Ham A/B JSON de bas")
    args = ap.parse_args(argv)

    # TEI URL'yi service kurulmadan ÖNCE ENV'e koy — TeiSettings() init'te okur.
    os.environ["RAGINTEL_TEI_RERANK_URL"] = args.tei_url

    from ragintel.config.loader import load_config
    from ragintel.config.settings import DbSettings
    from ragintel.database import Database
    from ragintel.database.config_store import make_db_reader
    from ragintel.eval import repository as repo
    from ragintel.eval.retrieval_benchmark import (
        StoreRetriever,
        format_summary,
        from_db_rows,
        map_gold_chunks,
        run_benchmark,
    )
    from ragintel.retrieval import RetrievalService

    # 1) Preflight — TEI gerçekten rerank ediyor mu? (sessiz passthrough tuzağı)
    ok, msg = _preflight(args.tei_url, args.rerank_timeout)
    print(f"[preflight] {'OK' if ok else 'BAŞARISIZ'} — {msg}")
    if not ok:
        print("Ölçüm durduruldu: TEI güvenilir rerank yapmıyor.", file=sys.stderr)
        return 2

    db = Database(DbSettings()).open()
    try:
        cfg = load_config(db_reader=make_db_reader(db))
        service = RetrievalService(db=db, config=cfg)
        rc = service.retrieval_cfg

        # 2) golden + gold-chunk eşleme (CLI `eval retrieval` ile birebir mantık)
        with db.connection() as conn:
            rows = repo.list_golden_records(conn, args.golden)
        if not rows:
            print(f"HATA: '{args.golden}' golden set DB'de yok.", file=sys.stderr)
            return 2
        records = from_db_rows(rows)
        with db.connection() as conn:
            mapping = map_gold_chunks(conn, records)

        # 3) sorgu embedding cache — iki kol PAYLAŞIR (sorgu tek kez embed edilir)
        embed_cache: dict[str, list[float]] = {}
        for rec in records:
            if rec.answerable and rec.question not in embed_cache:
                embed_cache[rec.question] = service._embed_query(rec.question)[0]

        # 4) üretim hybrid parametreleri — baseline'ı KARNE zeminine hizala
        common = dict(
            method="hybrid",
            ef_search=int(rc.vector_ef_search),
            iterative_scan=str(rc.hnsw_iterative_scan),
            fusion=str(rc.hybrid_fusion),
            rrf_k=int(rc.hybrid_rrf_k),
            dense_weight=float(rc.hybrid_dense_weight),
            sparse_weight=float(rc.hybrid_sparse_weight),
            sparse_variant=str(rc.hybrid_sparse_variant),
        )

        # KENDİ İÇİNDE rerank — prod `_tei_rerank`'in fail-open'ını BİLEREK atlıyoruz:
        # kalite ölçümünde timeout SESSİZCE yutulmamalı, GÜRÜLTÜLÜ çökmeli (B=A yalanı
        # yerine). Cömert timeout (args.rerank_timeout) CPU'nun 20-aday yavaşlığını karşılar.
        import httpx

        rerank_client = httpx.Client(timeout=args.rerank_timeout)
        rerank_url = f"{args.tei_url.rstrip('/')}/rerank"

        def _skorla(question: str, texts: list[str]) -> list[float]:
            """Havuzu parti parti skorlar; index'ler HAVUZ tabanına geri çevrilir.

            TEI her yanıtta index'i O PARTİ içinde verir; `bas` eklenmezse ikinci
            partinin skorları birincinin üstüne yazılır ve sıralama sessizce
            bozulurdu (413 gibi gürültülü değil — bu yüzden burada açıkça yazılı).
            """
            skorlar = [float("-inf")] * len(texts)
            for bas in range(0, len(texts), max(1, args.tei_batch)):
                parca = texts[bas:bas + max(1, args.tei_batch)]
                resp = rerank_client.post(rerank_url,
                                          json={"query": question, "texts": parca})
                resp.raise_for_status()  # her hata → ÇÖK (sessiz passthrough YOK)
                payload = resp.json()
                sonuc = payload if isinstance(payload, list) else payload.get("results")
                if not isinstance(sonuc, list) or len(sonuc) != len(parca):
                    raise ValueError(f"TEI rerank yanıtı geçersiz: {payload!r}")
                for it in sonuc:
                    i = int(it["index"])
                    if i < 0 or i >= len(parca):
                        raise ValueError(f"TEI index geçersiz: {i} (parti={len(parca)})")
                    skorlar[bas + i] = float(it["score"])
            if any(s == float("-inf") for s in skorlar):
                raise ValueError("TEI bazı adayları skorlamadı")
            return skorlar

        def tei_fn(question: str, ids: list[int], doc_scope: str) -> list[int]:
            rows = service.store.rerank_texts(chunk_ids=ids, allowed_doc_scopes=[doc_scope])
            if len(rows) != len(ids):
                raise ValueError(f"rerank metinleri eksik: {len(rows)} ≠ {len(ids)}")
            skorlar = _skorla(question, [str(r["text"]) for r in rows])
            # `sorted` kararlı: eşit skorda hibrit sıra korunur (keyfi kayma yok).
            sira = sorted(range(len(ids)), key=lambda i: skorlar[i], reverse=True)
            return [ids[i] for i in sira]

        arm_a = StoreRetriever(service.store, embed_cache, name="passthrough",
                               rerank=False, **common)
        arm_b = StoreRetriever(service.store, embed_cache, name="tei-rerank",
                               rerank=True, rerank_fn=tei_fn, pool=args.pool, **common)

        res_a = run_benchmark(records, mapping, arm_a)
        res_b = run_benchmark(records, mapping, arm_b)

        print()
        print(format_summary(res_a))
        print()
        print(format_summary(res_b))
        print()
        print(_delta_table(res_a, res_b))
        print()
        print(_kayit_tablosu(res_a, res_b))

        if args.json:
            import json
            print()
            print(json.dumps({"passthrough": res_a, "tei_rerank": res_b},
                             ensure_ascii=False, default=str))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

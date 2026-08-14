#!/usr/bin/env python
"""top_k artışı LLM'e GERÇEKTEN ulaşıyor mu? — bağlam bütçesi ön-kontrolü.

NEDEN VAR: `default_top_k` 10→20 önerisi retrieval recall'üne dayanıyor
(synthesis r@10 0.145 → r@20 0.310, bkz. komsu_tavan_probe --tani). Ama retriever'ın
20 chunk döndürmesi, LLM'in 20 chunk GÖRMESİ demek DEĞİLDİR. Arada iki kesme var:

  1. `ContextBuilder._apply_budget` — `retrieval.context_token_budget` (vars. 4000)
     dolunca blok TAHLİYE eder. Tahliye kurbanı `min(score, -order)`, yani EN DÜŞÜK
     skorlu blok. 11-20. sıradaki altın chunk'lar tam olarak en düşük skorlu olanlardır
     ⇒ bütçe doluysa k'yı büyütmek yalnız TAHLİYE edilecek chunk üretir.
  2. `agents/tools.py` — tool şeması ve handler `top_k`'yı 10 olarak SABİT gönderiyor
     (`args.get("top_k", 10)`), `_effective_top_k` yalnız `None` gelince config'e bakar.
     Bu probe onu ölçmez, kodda okunur; buradaki ölçüm "bütçe izin verse ne olurdu"dur.

Bu yüzden pahalı uçtan uca koşumdan ÖNCE ölçülür:
  • r@k        : retriever'ın döndürdüğü altın chunk oranı (bilinen sayı)
  • ctx@k      : CANLI bütçeyle LLM bağlamına GİREN altın chunk oranı  ← asıl sayı
  • ctx@k(B)   : `--butce` ile süpürülen her B değerinde aynı oran
  • ctx@k(∞)   : kesme yokken bağlama giren oran (tavan)
`ctx@10 == ctx@20` ise k çevirmesi sessiz bir no-op'tur ve asıl kaldıraç bütçedir.

İLK KOŞUMUN BULGUSU (2026-08-14, v1-bddk): kayıp k=20'de DEĞİL, BUGÜN k=10'da:
r@10 0.569 → ctx@10 0.451, 30 kaydın 27'sinde tahliye var (54 chunk). Kesmesiz
token medyanı 4695 > canlı bütçe 4000. Bu yüzden probe artık bütçe SÜPÜRÜR.

SALT OKUR: DB'ye/config'e/golden'a HİÇBİR ŞEY YAZMAZ. Sınırsız-bütçe kolu için
config nesnesinin process-içi `model_copy`'si kullanılır (kalıcı değildir).

Koşum (H200, konteyner DIŞI — README §H200 ön-hazırlığı + TEI URL'i):
  export RAGINTEL_TEI_RERANK_URL=http://localhost:8085
  python scripts/baglam_butcesi_probe.py --golden v1-bddk
  python scripts/baglam_butcesi_probe.py --golden v1-bddk --k 10 20 30
"""

from __future__ import annotations

import argparse
import sys


_SINIRSIZ = 128_000  # context_token_budget üst sınırı (settings.py le=128000) = "kesme yok" kolu


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _blok_tokenlari(counter, bloklar) -> int:
    return sum(counter.count(f"{b['label']}\n{b['text']}") for b in bloklar)


def _kapsanan(bloklar) -> set[int]:
    return {int(cid) for b in bloklar for cid in b["chunk_ids"]}


def _oran(pay: int, payda: int) -> float:
    return pay / payda if payda else 0.0


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description="Bağlam bütçesi ön-kontrolü (salt okur)")
    ap.add_argument("--golden", default="v1-bddk", help="DB set_version (vars. v1-bddk)")
    ap.add_argument("--k", type=int, nargs="+", default=[10, 20],
                    help="Denenecek top_k değerleri (vars. 10 20)")
    ap.add_argument("--butce", type=int, nargs="+", default=[6000, 8000, 12000],
                    help="Süpürülecek context_token_budget değerleri (canlı değer + ∞ "
                         "her zaman eklenir)")
    ap.add_argument("--limit", type=int, default=0, help="Yalnız ilk N kayıt (0=hepsi)")
    args = ap.parse_args()

    from ragintel.config.loader import load_config
    from ragintel.config.settings import DbSettings
    from ragintel.database import Database
    from ragintel.database.config_store import make_db_reader
    from ragintel.eval import repository as erepo
    from ragintel.eval.retrieval_benchmark import from_db_rows, map_gold_chunks
    from ragintel.retrieval import RetrievalService
    from ragintel.retrieval.context_builder import ContextBuilder

    db = Database(DbSettings()).open()
    try:
        cfg = load_config(db_reader=make_db_reader(db))
        rc = cfg.group("retrieval")
        butce = int(rc.context_token_budget)
        marj = float(rc.context_token_safety_margin)

        print("=== 1) ZEMİN ===")
        for ad in ("default_top_k", "max_top_k", "rerank_backend", "rerank_pool",
                   "context_token_budget", "context_token_safety_margin"):
            try:
                print(f"  {ad:30s} = {cfg.value('retrieval', ad)!r:12s} "
                      f"kaynak: {cfg.source_of('retrieval', ad)}")
            except KeyError:
                print(f"  {ad:30s} = <ALAN YOK>")
        ust = int(rc.max_top_k)
        asanlar = [k for k in args.k if k > ust]
        if asanlar:
            print(f"\n  ⚠ max_top_k={ust} — {asanlar} bu değere KIRPILIR "
                  f"(_effective_top_k). Ölçüm o k'larda yanıltıcı olur.")

        with db.connection() as conn:
            satirlar = erepo.list_golden_records(conn, args.golden)
            if not satirlar:
                print(f"\n  ✗ '{args.golden}' set'inde kayıt yok.")
                return 2
            kayitlar = [r for r in from_db_rows(satirlar) if r.answerable]
            if args.limit:
                kayitlar = kayitlar[: args.limit]
            esleme = map_gold_chunks(conn, kayitlar)

        print(f"\n  kayıt: {len(kayitlar)} answerable · quote eşleme: "
              f"{esleme.mapped_evidence}/{esleme.total_evidence} = "
              f"%{esleme.rate * 100:.1f}")
        if esleme.rate < 1.0:
            print("  ⚠ eşleme %100 değil — eksik evidence altın kümeye GİRMEZ, "
                  "sayılar bu paydaya göre okunur.")

        # Bütçe süpürmesi: canlı değer HER ZAMAN listede (karşılaştırma zemini),
        # ∞ (128000) tavanı verir. Her bütçe için ayrı builder; token sayacı ORTAK
        # (tokenizer yüklemesi pahalı) ve builder durumsuzdur.
        service = RetrievalService(db=db, config=cfg)
        butceler = sorted({butce, *args.butce} - {_SINIRSIZ})
        builder = ContextBuilder(db=db, config=cfg)
        counter = builder.token_counter
        kurucular: dict[int, ContextBuilder] = {butce: builder}
        for b in [*butceler, _SINIRSIZ]:
            if b in kurucular:
                continue
            kb = ContextBuilder(db=db, config=cfg, token_counter=counter)
            # Process-içi kopya — DB'ye/config'e YAZMAZ, kalıcı değildir.
            kb.retrieval_cfg = rc.model_copy(update={"context_token_budget": b})
            kurucular[b] = kb
        kolonlar = [*butceler, _SINIRSIZ]

        print("\n=== 2) KAYIT KAYIT ===")
        satir_sonuc: list[dict] = []
        for kayit in kayitlar:
            altin = esleme.gold_by_id.get(kayit.id, set())
            uc = {"user_id": "butce_probe", "tenant_id": "eval", "roles": ["eval"],
                  "allowed_doc_scopes": [kayit.doc_scope]}
            hucre: dict = {"id": kayit.id, "kategori": kayit.category, "altin": len(altin)}
            for k in args.k:
                bulunan = service.search_hybrid(kayit.question, top_k=k, user_ctx=uc)
                ids = [int(c["chunk_id"]) for c in bulunan]
                ctx_map: dict[int, dict] = {}
                for b in kolonlar:
                    ctx = kurucular[b].build(bulunan)
                    ctx_map[b] = {
                        "ctx": len(altin & _kapsanan(ctx["blocks"])),
                        "blok": len(ctx["blocks"]),
                        "dusen": len(ctx["dropped_chunk_ids"]),
                        "tok": int(_blok_tokenlari(counter, ctx["blocks"]) * marj),
                    }
                hucre[k] = {"donen": len(ids), "r": len(altin & set(ids)), "b": ctx_map}
            satir_sonuc.append(hucre)
            parcalar = " | ".join(
                f"k={k}: dönen {hucre[k]['donen']:2d} r={hucre[k]['r']} "
                f"ctx({butce})={hucre[k]['b'][butce]['ctx']} "
                f"düşen={hucre[k]['b'][butce]['dusen']:2d} "
                f"tok∞={hucre[k]['b'][_SINIRSIZ]['tok']:5d} "
                f"ctx∞={hucre[k]['b'][_SINIRSIZ]['ctx']}"
                for k in args.k
            )
            print(f"  {kayit.id:12s} {kayit.category:20s} altın={len(altin)}  {parcalar}")

        payda_hep = sum(h["altin"] for h in satir_sonuc)
        kategoriler = sorted({h["kategori"] for h in satir_sonuc}) + ["GENEL"]

        def _grup(kat: str) -> list[dict]:
            return satir_sonuc if kat == "GENEL" else [h for h in satir_sonuc if h["kategori"] == kat]

        print("\n=== 3) KATEGORİ × BÜTÇE (bağlama GİREN altın chunk oranı) ===")
        print("  NOT: bu oran havuzlanmış (mikro) — retrieval_benchmark kayıt-başına "
              "ortalar (makro).\n  İki araç birebir karşılaştırılmaz; buradaki karşılaştırma "
              "kolonlar ARASINDADIR.")
        for k in args.k:
            basliklar = "".join(f"  b{b:<7d}" if b != _SINIRSIZ else "  ∞       " for b in kolonlar)
            print(f"\n  top_k={k}\n  {'kategori':22s} {'n':>3s}  {'r@k':<7s}{basliklar}")
            for kat in kategoriler:
                grup = _grup(kat)
                payda = sum(h["altin"] for h in grup)
                satir = f"  {_oran(sum(h[k]['r'] for h in grup), payda):<7.3f}"
                for b in kolonlar:
                    satir += f"  {_oran(sum(h[k]['b'][b]['ctx'] for h in grup), payda):<7.3f}"
                print(f"  {kat:22s} {len(grup):3d}{satir}")

        print("\n=== 4) BÜTÇE İHTİYACI ===")
        for k in args.k:
            toklar = sorted(h[k]["b"][_SINIRSIZ]["tok"] for h in satir_sonuc)
            p95 = toklar[min(len(toklar) - 1, int(len(toklar) * 0.95))] if toklar else 0
            print(f"  k={k:<3d} kesmesiz token: medyan {toklar[len(toklar) // 2] if toklar else 0} "
                  f"· p95 {p95} · azami {toklar[-1] if toklar else 0}")
            for b in kolonlar:
                dusen = sum(h[k]["b"][b]["dusen"] for h in satir_sonuc)
                kayip_kayit = sum(1 for h in satir_sonuc if h[k]["b"][b]["dusen"] > 0)
                altin_kayip = sum(h[k]["r"] - h[k]["b"][b]["ctx"] for h in satir_sonuc)
                ad = "∞" if b == _SINIRSIZ else str(b)
                isaret = "  ← canlı" if b == butce else ""
                print(f"       bütçe {ad:>7s}: tahliye {dusen:4d} chunk / {kayip_kayit:2d} kayıt "
                      f"· BAĞLAMA GİREMEYEN ALTIN {altin_kayip:3d}{isaret}")

        print("\n=== OKUMA ===")
        for k in args.k:
            r = _oran(sum(h[k]["r"] for h in satir_sonuc), payda_hep)
            canli = _oran(sum(h[k]["b"][butce]["ctx"] for h in satir_sonuc), payda_hep)
            tavan = _oran(sum(h[k]["b"][_SINIRSIZ]["ctx"] for h in satir_sonuc), payda_hep)
            yeter = next((b for b in kolonlar
                          if _oran(sum(h[k]["b"][b]["ctx"] for h in satir_sonuc), payda_hep)
                          >= tavan - 1e-9), _SINIRSIZ)
            yeter_ad = "∞ (denenen bütçelerin hiçbiri yetmedi)" if yeter == _SINIRSIZ else str(yeter)
            print(f"  k={k:<3d} retrieval {r:.3f} → canlı bağlam {canli:.3f} "
                  f"(KAYIP {r - canli:+.3f}) · tavan {tavan:.3f} · tavanı veren en küçük "
                  f"bütçe: {yeter_ad}")
        ilk, son = args.k[0], args.k[-1]
        if len(args.k) > 1:
            c_fark = _oran(sum(h[son]["b"][butce]["ctx"] for h in satir_sonuc), payda_hep) - \
                _oran(sum(h[ilk]["b"][butce]["ctx"] for h in satir_sonuc), payda_hep)
            i_fark = _oran(sum(h[son]["b"][_SINIRSIZ]["ctx"] for h in satir_sonuc), payda_hep) - \
                _oran(sum(h[ilk]["b"][_SINIRSIZ]["ctx"] for h in satir_sonuc), payda_hep)
            print(f"\n  k {ilk}→{son}: canlı bütçede {c_fark:+.3f} · sınırsız bütçede {i_fark:+.3f}")
            if c_fark <= 1e-9 < i_fark:
                print("  ⇒ SIRALAMA NET: bütçe ÖNCE, k SONRA. Bütçe sabitken k çevirmek "
                      "kazanç vermez\n    (hatta tahliye artar); bütçe açılınca k'nın "
                      "kazancı ORTAYA ÇIKAR.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

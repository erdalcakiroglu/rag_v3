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

Bu yüzden pahalı uçtan uca koşumdan ÖNCE üç sayı ölçülür:
  • r@k        : retriever'ın döndürdüğü altın chunk oranı (bilinen sayı)
  • ctx@k      : CANLI bütçeyle LLM bağlamına GİREN altın chunk oranı  ← asıl sayı
  • ctx@k(∞)   : bütçe sınırsızken bağlama giren oran (tavan)
`ctx@10 == ctx@20` ise k çevirmesi sessiz bir no-op'tur ve asıl kaldıraç bütçedir.

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

        service = RetrievalService(db=db, config=cfg)
        builder = ContextBuilder(db=db, config=cfg)
        counter = builder.token_counter
        # Sınırsız kol: aynı builder'ın config'inin process-içi kopyası (kalıcı DEĞİL).
        sinirsiz = ContextBuilder(db=db, config=cfg, token_counter=counter)
        sinirsiz.retrieval_cfg = rc.model_copy(update={"context_token_budget": 128000})

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
                ctx = builder.build(bulunan)
                ctx_inf = sinirsiz.build(bulunan)
                kapsanan = _kapsanan(ctx["blocks"])
                kapsanan_inf = _kapsanan(ctx_inf["blocks"])
                hucre[k] = {
                    "donen": len(ids),
                    "r": len(altin & set(ids)),
                    "ctx": len(altin & kapsanan),
                    "ctx_inf": len(altin & kapsanan_inf),
                    "blok": len(ctx["blocks"]),
                    "dusen": len(ctx["dropped_chunk_ids"]),
                    "tok": int(_blok_tokenlari(counter, ctx["blocks"]) * marj),
                    "tok_inf": int(_blok_tokenlari(counter, ctx_inf["blocks"]) * marj),
                }
            satir_sonuc.append(hucre)
            parcalar = " | ".join(
                f"k={k}: dönen {hucre[k]['donen']:2d} blok {hucre[k]['blok']:2d} "
                f"düşen {hucre[k]['dusen']:2d} tok {hucre[k]['tok']:5d} "
                f"altın r/ctx/∞ {hucre[k]['r']}/{hucre[k]['ctx']}/{hucre[k]['ctx_inf']}"
                for k in args.k
            )
            print(f"  {kayit.id:12s} {kayit.category:20s} altın={len(altin)}  {parcalar}")

        print("\n=== 3) KATEGORİ KIRILIMI (altın chunk oranı) ===")
        basliklar = "".join(f"  r@{k:<5d} ctx@{k:<5d} ∞@{k:<5d}" for k in args.k)
        print(f"  {'kategori':22s} {'n':>3s}{basliklar}")
        kategoriler = sorted({h["kategori"] for h in satir_sonuc}) + ["GENEL"]
        for kat in kategoriler:
            grup = satir_sonuc if kat == "GENEL" else [h for h in satir_sonuc if h["kategori"] == kat]
            payda = sum(h["altin"] for h in grup)
            hucreler = ""
            for k in args.k:
                hucreler += (f"  {_oran(sum(h[k]['r'] for h in grup), payda):<7.3f}"
                             f" {_oran(sum(h[k]['ctx'] for h in grup), payda):<8.3f}"
                             f" {_oran(sum(h[k]['ctx_inf'] for h in grup), payda):<7.3f}")
            print(f"  {kat:22s} {len(grup):3d}{hucreler}")

        print("\n=== 4) BÜTÇE ===")
        for k in args.k:
            toklar = sorted(h[k]["tok_inf"] for h in satir_sonuc)
            p95 = toklar[min(len(toklar) - 1, int(len(toklar) * 0.95))] if toklar else 0
            dusen = sum(h[k]["dusen"] for h in satir_sonuc)
            dusen_kayit = sum(1 for h in satir_sonuc if h[k]["dusen"] > 0)
            print(f"  k={k:<3d} tahliye: {dusen:3d} chunk / {dusen_kayit:2d} kayıt · "
                  f"kesmesiz token medyan {toklar[len(toklar) // 2] if toklar else 0} "
                  f"p95 {p95} (canlı bütçe {butce})")

        print("\n=== OKUMA ===")
        ilk, son = args.k[0], args.k[-1]
        payda = sum(h["altin"] for h in satir_sonuc)
        r_fark = _oran(sum(h[son]["r"] for h in satir_sonuc), payda) - \
            _oran(sum(h[ilk]["r"] for h in satir_sonuc), payda)
        c_fark = _oran(sum(h[son]["ctx"] for h in satir_sonuc), payda) - \
            _oran(sum(h[ilk]["ctx"] for h in satir_sonuc), payda)
        i_fark = _oran(sum(h[son]["ctx_inf"] for h in satir_sonuc), payda) - \
            _oran(sum(h[ilk]["ctx_inf"] for h in satir_sonuc), payda)
        print(f"  k {ilk}→{son}:  retrieval {r_fark:+.3f}  ·  CANLI bağlam {c_fark:+.3f}  ·  "
              f"sınırsız bağlam {i_fark:+.3f}")
        if abs(c_fark) < 1e-9 and r_fark > 1e-9:
            print("  ⇒ k çevirmesi SESSİZ NO-OP: retriever daha çok altın getiriyor ama "
                  "bütçe hepsini\n    tahliye ediyor. Asıl kaldıraç context_token_budget "
                  "(ya da chunk başına token).")
        elif c_fark > 1e-9:
            print("  ⇒ k çevirmesi bağlama GERÇEKTEN altın taşıyor; uçtan uca karne ölçümü "
                  "anlamlı.\n    (retrieval kazancı ≠ cevap kalitesi — karne yine de "
                  "`eval run --agent-runs 3` ile ölçülmeli.)")
        else:
            print("  ⇒ k artışı bağlamı ZENGİNLEŞTİRMİYOR; ölçüm zeminini gözden geçirin.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

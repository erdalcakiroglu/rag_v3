#!/usr/bin/env python
"""`eval retrieval --json` çıktısını okur: ÖNCE eşleme, SONRA metrikler.
Dosyaya dokunmaz, DB'ye bağlanmaz — saf metin okuyucu.

NEDEN
    Kuru koşum `2>&1` ile dosyaya alınınca başına yapılandırılmış log satırları
    düşer ve `json.load` patlar. `raw_decode` ile ilk `{`'ten okumak da yetmez:
    ilk nesne bir LOG kaydı olur ve `KeyError: gold_mapping` verir. Her `{`
    konumu taranıp `gold_mapping` taşıyan ilk nesne seçilir.

NEDEN EŞLEME ÖNCE BASILIR
    Golden v1'in bütün varlık sebebi buydu: eski set 0/43 eşleşiyordu ve tüm
    metrikler 0.000 çıkıyordu — korpus kusuru sanılabilecek bir ÖLÇÜ ARACI
    kusuruydu ([[korpus-degisti-golden-bayat]]). Eşleme oranı 1.0 değilse
    aşağıdaki hiçbir sayı sistem hakkında değildir; `unmapped` listesi hangi
    (dosya, sayfa, alıntı) üçlüsünün düştüğünü söyler.

KULLANIM
    python -m ragintel.eval retrieval --from-file eval/golden/v1.jsonl \
        --variant hybrid --json > docs/kuru2.txt 2>&1
    python scripts/kuru_kosum_ozet.py docs/kuru2.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ANAHTAR = "gold_mapping"


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _cikar(ham: str) -> dict:
    """Log gürültüsü içinden `gold_mapping` taşıyan ilk JSON nesnesini bulur."""
    cozucu = json.JSONDecoder()
    yer = ham.find("{")
    while yer != -1:
        try:
            nesne, _ = cozucu.raw_decode(ham[yer:])
        except json.JSONDecodeError:
            yer = ham.find("{", yer + 1)
            continue
        if isinstance(nesne, dict) and ANAHTAR in nesne:
            return nesne
        yer = ham.find("{", yer + 1)
    raise SystemExit(f"'{ANAHTAR}' tasiyan JSON nesnesi bulunamadi.")


def _satir(ad: str, m: dict) -> str:
    def g(k: str) -> str:
        v = m.get(k)
        return f"{v:.4f}" if isinstance(v, (int, float)) else "-"
    return (f"{ad:<20}{g('recall@5'):>10}{g('recall@10'):>11}"
            f"{g('recall@20'):>11}{g('ndcg@10'):>10}{g('mrr'):>9}"
            f"{str(m.get('n', '-')):>5}")


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dosya", type=Path)
    ap.add_argument("--unmapped-goster", type=int, default=10)
    args = ap.parse_args()

    veri = _cikar(args.dosya.read_text(encoding="utf-8", errors="replace"))
    gm = veri.get(ANAHTAR) or {}
    toplam = gm.get("total_evidence")
    eslesen = gm.get("mapped")
    unmapped = gm.get("unmapped") or []

    oran = gm.get("rate")
    if oran is None and toplam:
        oran = eslesen / toplam
    print("=" * 74)
    print(f"variant={veri.get('variant')}  top_k={veri.get('top_k')}")
    print(f"ESLEME: {eslesen} / {toplam}"
          + (f" = {oran:.4f}" if isinstance(oran, (int, float)) else "")
          + f"   unmapped: {len(unmapped)}")
    print("=" * 74)
    if isinstance(oran, (int, float)) and oran < 1.0:
        print("!! 1.0 DEGIL — asagidaki metrikler sistem hakkinda DEGILDIR.")
        for u in unmapped[:args.unmapped_goster]:
            print(f"   {u.get('record_id')} [{u.get('file_name')} "
                  f"s.{u.get('page')}]: \"{str(u.get('quote'))[:80]}\"")
        if len(unmapped) > args.unmapped_goster:
            print(f"   ... +{len(unmapped) - args.unmapped_goster} tane daha")

    agg = veri.get("aggregate") or {}
    print(f"\n{'':<20}{'recall@5':>10}{'recall@10':>11}{'recall@20':>11}"
          f"{'nDCG@10':>10}{'MRR':>9}{'n':>5}")
    if agg.get("overall"):
        print(_satir("genel", agg["overall"]))
    for ad, m in sorted((agg.get("by_category") or {}).items()):
        print(_satir(ad, m))

    # gold_n: mükerrer eleme sonrası paydanın gerçekten düştüğünü gösterir.
    kayitlar = veri.get("per_record") or []
    if kayitlar:
        golds = [r.get("gold_n", 0) for r in kayitlar]
        sifir = [r["id"] for r in kayitlar if not r.get("gold_n")]
        print(f"\ngold_n: toplam {sum(golds)}  ort {sum(golds) / len(golds):.2f}  "
              f"max {max(golds)}  kayit {len(golds)}")
        if sifir:
            print(f"!! gold_n=0 olan kayit ({len(sifir)}): {', '.join(sifir)}")
    if not agg:
        print("(metrik alani taninamadi — ham anahtarlar:)")
        print("  " + ", ".join(sorted(veri)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

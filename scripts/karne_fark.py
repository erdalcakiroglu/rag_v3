#!/usr/bin/env python
"""İki kuru koşum çıktısını KAYIT BAZINDA karşılaştırır. Saf metin okuyucu.

NEDEN
    Mükerrer baskı elemesi (Golden_v1_BDDK_Ankrali §10) karneyi yükseltmedi:
    genel recall@10 0.4968 -> 0.4856, single_fact 0.6786 -> 0.6667. Elemenin
    tam da bu sayıları düzeltmesi bekleniyordu (§5b-DÜZELTME), düzeltmedi.

    Toplam sayı bunun SEBEBİNİ söylemez. İki farklı dünya aynı ortalamayı verir:
      (a) hiçbir şey değişmedi — eleme etkisiz,
      (b) bazı kayıtlar sıçradı, bazıları ÇÖKTÜ ve ortalama denkleşti.
    (b) doğruysa çöken kayıtların ortak yanı vardır ve o yan bulunabilir.

HİPOTEZ (bu araç sınamak için var)
    `recall_at_k` paydası sert: `|gold ∩ topk| / |gold|`. Mükerrer kopyalar
    paydayı şişiriyordu AMA aynı zamanda N tane BİLET veriyordu — kopyalardan
    herhangi biri top-k'ya girince pay artıyordu. Eleme sonrası "kopyalardan
    biri" yerine "tam olarak bu chunk" aranıyor. Tutulan dosya konsolide metin
    ve gövdesinde satır arası "(Değişik: …)" şerhleri var; bu şerhler embedding
    için gürültüdür. Hukuken doğru dosyayı tutup erişim açısından en zayıf
    kopyayı tutmuş olabiliriz. Çöken kayıtların hepsi 5411 kümesine bağlıysa
    hipotez ayakta kalır; dağınıksa çürür.

KULLANIM
    python scripts/karne_fark.py docs/kuru.txt docs/kuru2.txt
    python scripts/karne_fark.py docs/kuru.txt docs/kuru2.txt --metrik recall@5
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


def _cikar(yol: Path) -> dict:
    """Log gürültüsü içinden `gold_mapping` taşıyan ilk JSON nesnesini bulur."""
    ham = yol.read_text(encoding="utf-8", errors="replace")
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
    raise SystemExit(f"{yol}: '{ANAHTAR}' tasiyan JSON nesnesi bulunamadi.")


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("once", type=Path, help="ONCEKI kuru kosum ciktisi")
    ap.add_argument("sonra", type=Path, help="SONRAKI kuru kosum ciktisi")
    ap.add_argument("--metrik", default="recall@10")
    ap.add_argument("--esik", type=float, default=1e-9,
                    help="bu farkin altindaki degisim 'ayni' sayilir")
    args = ap.parse_args()

    a, b = _cikar(args.once), _cikar(args.sonra)
    ka = {r["id"]: r for r in a.get("per_record", [])}
    kb = {r["id"]: r for r in b.get("per_record", [])}
    ortak = sorted(set(ka) & set(kb))
    if not ortak:
        raise SystemExit("iki kosumda ortak kayit yok.")

    m = args.metrik
    satirlar = []
    for kid in ortak:
        ra, rb = ka[kid], kb[kid]
        va, vb = ra.get(m, 0.0), rb.get(m, 0.0)
        satirlar.append({
            "id": kid, "kategori": rb.get("category", "?"),
            "once": va, "sonra": vb, "fark": vb - va,
            "gold_once": ra.get("gold_n", 0), "gold_sonra": rb.get("gold_n", 0),
        })

    yukselen = [s for s in satirlar if s["fark"] > args.esik]
    dusen = [s for s in satirlar if s["fark"] < -args.esik]
    ayni = [s for s in satirlar if abs(s["fark"]) <= args.esik]

    # Paydası değişmemiş kayıt elemeden ETKİLENMEMİŞTİR: kontrol kolu budur.
    etkilenen = [s for s in satirlar if s["gold_once"] != s["gold_sonra"]]
    etkisiz = [s for s in satirlar if s["gold_once"] == s["gold_sonra"]]

    print("=" * 76)
    print(f"KAYIT BAZINDA FARK — {m}   ({args.once.name} -> {args.sonra.name})")
    print("=" * 76)
    print(f"yukselen {len(yukselen)}   dusen {len(dusen)}   ayni {len(ayni)}"
          f"   toplam {len(satirlar)}")
    ort_a = sum(s["once"] for s in satirlar) / len(satirlar)
    ort_b = sum(s["sonra"] for s in satirlar) / len(satirlar)
    print(f"ortalama {ort_a:.4f} -> {ort_b:.4f}  ({ort_b - ort_a:+.4f})")

    print(f"\nPAYDASI DEGISEN (elemeden etkilenen) {len(etkilenen)} kayit:")
    if etkilenen:
        ea = sum(s["once"] for s in etkilenen) / len(etkilenen)
        eb = sum(s["sonra"] for s in etkilenen) / len(etkilenen)
        print(f"  ortalama {ea:.4f} -> {eb:.4f}  ({eb - ea:+.4f})")
    print(f"PAYDASI AYNI (kontrol kolu) {len(etkisiz)} kayit:")
    if etkisiz:
        ka_ = sum(s["once"] for s in etkisiz) / len(etkisiz)
        kb_ = sum(s["sonra"] for s in etkisiz) / len(etkisiz)
        print(f"  ortalama {ka_:.4f} -> {kb_:.4f}  ({kb_ - ka_:+.4f})")
        print("  (kontrol kolu OYNUYORSA fark elemeden degil kosum "
              "gurultusunden/ANN'den geliyor demektir)")

    print(f"\n{'kayit':<18}{'kategori':<20}{'once':>8}{'sonra':>8}{'fark':>9}"
          f"{'gold_n':>12}")
    for s in sorted(satirlar, key=lambda s: s["fark"]):
        if abs(s["fark"]) <= args.esik and s["gold_once"] == s["gold_sonra"]:
            continue
        im = " " if abs(s["fark"]) <= args.esik else ("+" if s["fark"] > 0 else "-")
        print(f"{im}{s['id']:<17}{s['kategori']:<20}{s['once']:>8.3f}"
              f"{s['sonra']:>8.3f}{s['fark']:>+9.3f}"
              f"{s['gold_once']:>7} ->{s['gold_sonra']:>3}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

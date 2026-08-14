#!/usr/bin/env python
"""Komşu-pencere A/B ÖN-KONTROLÜ — kol gerçekten farklı mı koşuyor?

NEDEN VAR: iki kollu bir A/B'nin en sinsi başarısızlığı kolun HİÇ ETKİ ETMEMESİ.
O zaman B kolu A ile birebir aynı sayıyı verir ve bu "kazanç yok" diye okunur.
Oysa okunan şey ayarın ulaşmadığıdır. Aynı tuzak Ollama kilit probunda dört koşum
boyunca "kilit yok" dedirtmişti (6d50af4). Bu yüzden ölçümden ÖNCE üç şey KANITLANIR:

  1. `rerank_neighbor_window` efektif değeri KAÇ ve HANGİ KATMANDAN geliyor
     (db > env > default). DB katmanı ENV'i EZER: `app_config.retrieval` içinde bu
     anahtar varsa `RAGINTEL_RETRIEVAL_RERANK_NEIGHBOR_WINDOW` sessizce ÖLÜR.
  2. Genişletmenin ön-koşulu sağlanıyor mu: `_komsu_genislet` yalnız `havuz > top_k`
     iken çağrılır, havuz da yalnız `rerank_backend != passthrough` iken büyür.
  3. GERÇEK bir golden sorusu koşulur ve havuza KAÇ komşu eklendiği sayılır.
     Sayı 0 ise A/B koşmanın anlamı yoktur.

SALT OKUR: DB'ye/config'e/golden'a HİÇBİR ŞEY YAZMAZ. Yaptığı tek "müdahale"
process-içi bir sarmalayıcıyla `_komsu_genislet`in dönüşünü saymaktır.

Koşum (H200, konteyner DIŞI — README §H200 ön-hazırlığı + TEI URL'i):
  export RAGINTEL_TEI_RERANK_URL=http://localhost:8085
  python scripts/komsu_pencere_onkontrol.py --golden v1-bddk
  RAGINTEL_RETRIEVAL_RERANK_NEIGHBOR_WINDOW=1 \
    python scripts/komsu_pencere_onkontrol.py --golden v1-bddk
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


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description="Komşu-pencere A/B ön-kontrolü (salt okur)")
    ap.add_argument("--golden", default="v1-bddk", help="DB set_version (vars. v1-bddk)")
    ap.add_argument("--soru", type=int, default=3, help="Kaç golden sorusu denensin (vars. 3)")
    ap.add_argument("--top-k", type=int, default=10)
    args = ap.parse_args()

    from ragintel.config.loader import load_config
    from ragintel.config.settings import DbSettings
    from ragintel.database import Database
    from ragintel.database.config_store import make_db_reader
    from ragintel.eval import repository as erepo
    from ragintel.eval.retrieval_benchmark import from_db_rows
    from ragintel.retrieval import RetrievalService

    db = Database(DbSettings()).open()
    try:
        cfg = load_config(db_reader=make_db_reader(db))
        rc = cfg.group("retrieval")

        print("=== 1) AYAR NEREDEN GELİYOR ===")
        alanlar = ["rerank_neighbor_window", "rerank_neighbor_top", "rerank_backend",
                   "rerank_pool", "max_top_k", "default_top_k"]
        for ad in alanlar:
            try:
                deger = cfg.value("retrieval", ad)
                katman = cfg.source_of("retrieval", ad)
            except KeyError:
                print(f"  {ad:26s} = <ALAN YOK — kod güncel mi?>")
                continue
            print(f"  {ad:26s} = {deger!r:22s} kaynak: {katman}")

        env_ad = "RAGINTEL_RETRIEVAL_RERANK_NEIGHBOR_WINDOW"
        env_ham = os.environ.get(env_ad)
        pencere = int(getattr(rc, "rerank_neighbor_window", 0) or 0)
        if env_ham is not None and cfg.source_of("retrieval", "rerank_neighbor_window") != "env":
            print(f"\n  ⚠ ENV EZİLDİ: {env_ad}={env_ham} verilmiş ama efektif değer "
                  f"{pencere} ve '{cfg.source_of('retrieval', 'rerank_neighbor_window')}' "
                  f"katmanından geliyor.\n    `app_config.retrieval` bu anahtarı "
                  f"içeriyor → DB, ENV'i EZER. B kolu A ile aynı koşacaktı.")

        print("\n=== 2) GENİŞLETMENİN ÖN-KOŞULU ===")
        arka = str(getattr(rc, "rerank_backend", "passthrough"))
        havuz = max(args.top_k, int(getattr(rc, "rerank_pool", 0) or 0)) \
            if arka != "passthrough" else args.top_k
        print(f"  rerank_backend={arka} · top_k={args.top_k} → havuz={havuz}")
        if havuz <= args.top_k:
            print("  ✗ havuz > top_k DEĞİL → rerank hiç çağrılmaz, komşu genişletme de "
                  "çağrılmaz.\n    A/B ANLAMSIZ. (rerank_backend passthrough mu?)")
            return 3
        print("  ✓ havuz > top_k → genişletme çağrı yolunda")

        if pencere <= 0:
            print(f"\n=== 3) CANLI SAYIM ===\n  pencere=0 → bu A kolu (kontrol). "
                  f"Komşu eklenmez, beklenen budur.\n  B kolu için: "
                  f"{env_ad}=1 ile tekrar koşun.")
            return 0

        print("\n=== 3) CANLI SAYIM (gerçek golden sorusu) ===")
        with db.connection() as conn:
            satirlar = erepo.list_golden_records(conn, args.golden)
        if not satirlar:
            print(f"  ✗ '{args.golden}' set'inde kayıt yok.")
            return 2
        kayitlar = [r for r in from_db_rows(satirlar) if r.answerable][: args.soru]

        service = RetrievalService(db=db, config=cfg)
        sayac: list[int] = []
        gercek = service._komsu_genislet

        def sarmal(chunks, allowed):
            out, ek = gercek(chunks, allowed)
            sayac.append(ek)
            return out, ek

        service._komsu_genislet = sarmal  # type: ignore[method-assign]

        for kayit in kayitlar:
            uc = {"user_id": "onkontrol", "tenant_id": "eval", "roles": ["eval"],
                  "allowed_doc_scopes": [kayit.doc_scope]}
            once = len(sayac)
            sonuc = service.search_hybrid(kayit.question, top_k=args.top_k, user_ctx=uc)
            ek = sayac[-1] if len(sayac) > once else -1
            durum = "ÇAĞRILMADI" if ek < 0 else f"+{ek} komşu"
            print(f"  {kayit.id:10s} {durum:14s} sonuç={len(sonuc)}  "
                  f"soru={kayit.question[:52]}…")

        toplam = sum(s for s in sayac if s > 0)
        print(f"\n  TOPLAM eklenen komşu: {toplam} ({len(sayac)} çağrı)")
        if not sayac:
            print("  ✗ `_komsu_genislet` HİÇ ÇAĞRILMADI → kod yolu bu sürümde yok "
                  "(imaj/checkout eski?).\n    A/B koşmayın, iki kol aynı çıkar.")
            return 4
        if toplam == 0:
            print("  ✗ Genişletme çağrıldı ama SIFIR komşu eklendi. Olası sebep: "
                  "tepe chunk'ların\n    komşuları zaten havuzda, ya da kapsam süzgeci "
                  "hepsini eledi. A/B fark üretmez.")
            return 5
        print("  ✓ B kolu A'dan GERÇEKTEN farklı koşuyor — karne ölçümü anlamlı.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

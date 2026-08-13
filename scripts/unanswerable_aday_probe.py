#!/usr/bin/env python
"""Unanswerable aday probe — "korpus bunu cevaplamıyor" hükmünü GÖVDEDEN kanıtlar.

NEDEN VAR
    `v1-bddk` golden setinde `answerable=false` TEK kayıt yok (30/30 answerable).
    M-17 dürüstlük kapısı (D4) yalnız unanswerable kolunda koşar; kol olmadan
    kapı bankacılık korpusunda hiç ölçülemez. Kolu kurmanın tek dürüst yolu,
    her aday soru için yokluğu kanıtlamaktır.

NEDEN AD DESENİYLE DEĞİL
    Daha önce "14 yönetmeliğin hiçbiri korpusta yok" hükmü dosya ADI deseniyle
    verildi ve ÇÜRÜDÜ: `mevzuat_1340` aranan kaldıraç yönetmeliğinin ta kendisiydi,
    yalnız adı öyle değildi. Bu yüzden burada tek delil `core_chunks.chunk_text`
    GÖVDESİDİR; dosya adına hiç bakılmaz.

İKİ YÖNLÜ TUZAK — ikisi de raporlanır, ikisi de aday öldürür
    (a) SAHTE YOKLUK — korpus aslında cevaplıyor, erişim ıskaladı. Erişime
        güvenmek burada döngüsel olurdu (erişim kusuru yokluk sanılır), bu yüzden
        A bölümü erişimden BAĞIMSIZ: terimler korpus geneli ILIKE ile taranır.
    (b) FAZLA KOLAY — soru konu dışıysa model zaten reddeder ve test hiçbir şey
        kanıtlamaz. B bölümü üretim erişiminin ilk adaylarını gövdesiyle basar;
        adaylar konuca YAKIN değilse aday zayıftır. Dürüstlük testi ancak model
        ilgili GÖRÜNEN bağlam okurken anlamlıdır.

KONTROL KOLU
    `karar: "kontrol"` kayıtları korpusta OLDUĞU bilinen sorulardır. Probe onları
    "var" diye işaretleyemiyorsa taramanın kendisi bozuktur ve aday hükümlerine
    güvenilmez — sıfır bulgu, kanıt değil arıza demektir.

SALT OKUNUR. Yalnız SELECT çalıştırır; DB'ye, config'e, golden'a hiçbir şey yazmaz.
Golden'a yazma ayrı ve bilinçli adımdır (`golden_v1_jsonl_uret.py` + `eval load`).

KULLANIM (H200, repo kökünde, venv açık)
    python scripts/unanswerable_aday_probe.py > docs/unans.txt 2>&1
    python scripts/unanswerable_aday_probe.py --aday U06 --top 20
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
ADAYLAR = KOK / "docs" / "golden_v1_unanswerable_adaylar.json"

# Gövde penceresi: eşleşmenin çevresinden basılan karakter sayısı. Maddenin ne
# DEDİĞİ bu pencereden okunur; kısa tutulursa "var/yok" hükmü verilemez.
PENCERE = 140
ORNEK = 3


def _pencere(metin: str, terim: str, genislik: int = PENCERE) -> str:
    yer = metin.lower().find(terim.lower())
    if yer < 0:
        return metin[: genislik * 2].replace("\n", " ")
    bas = max(0, yer - genislik)
    son = min(len(metin), yer + len(terim) + genislik)
    parca = metin[bas:son].replace("\n", " ")
    return ("…" if bas else "") + parca + ("…" if son < len(metin) else "")


def _sozcuksel(conn, terim: str, doc_scope: str) -> tuple[int, list[tuple]]:
    """(kaç chunk içeriyor, ilk N örnek). Erişimden BAĞIMSIZ ham gövde taraması."""
    kalip = f"%{terim}%"
    adet = conn.execute(
        """
        SELECT count(*)
        FROM core_chunks c JOIN core_files f USING (file_id)
        WHERE f.doc_scope = %s AND c.chunk_text ILIKE %s;
        """,
        (doc_scope, kalip),
    ).fetchone()[0]
    if not adet:
        return 0, []
    ornekler = conn.execute(
        """
        SELECT f.file_name, c.page_number, c.chunk_text
        FROM core_chunks c JOIN core_files f USING (file_id)
        WHERE f.doc_scope = %s AND c.chunk_text ILIKE %s
        ORDER BY c.chunk_id
        LIMIT %s;
        """,
        (doc_scope, kalip, ORNEK),
    ).fetchall()
    return int(adet), ornekler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Unanswerable adaylarının yokluğunu gövdeden sınar")
    ap.add_argument("--adaylar", default=str(ADAYLAR), help="aday JSON yolu")
    ap.add_argument("--aday", action="append", default=None, help="yalnız bu id (tekrarlanabilir)")
    ap.add_argument("--doc-scope", default="default", help="golden ile AYNI scope olmalı")
    ap.add_argument("--top", type=int, default=10, help="üretim erişiminden basılacak aday sayısı")
    args = ap.parse_args(argv)

    veri = json.loads(Path(args.adaylar).read_text(encoding="utf-8"))
    adaylar = veri["adaylar"]
    if args.aday:
        istenen = set(args.aday)
        adaylar = [a for a in adaylar if a["id"] in istenen]
        if not adaylar:
            print(f"HATA: {sorted(istenen)} aday dosyasında yok.", file=sys.stderr)
            return 2

    from ragintel.config.loader import load_config
    from ragintel.config.settings import DbSettings
    from ragintel.database import Database
    from ragintel.database.config_store import make_db_reader
    from ragintel.retrieval import RetrievalService

    db = Database(DbSettings()).open()
    try:
        cfg = load_config(db_reader=make_db_reader(db))
        service = RetrievalService(db=db, config=cfg)
        rc = service.retrieval_cfg
        ctx = {
            "user_id": "probe",
            "tenant_id": "probe",
            "roles": ["reader"],
            "allowed_doc_scopes": [args.doc_scope],
        }

        with db.connection() as conn:
            toplam = conn.execute(
                """
                SELECT count(*), count(DISTINCT c.file_id)
                FROM core_chunks c JOIN core_files f USING (file_id)
                WHERE f.doc_scope = %s;
                """,
                (args.doc_scope,),
            ).fetchone()
        print("=" * 78)
        print(f"ZEMİN  doc_scope={args.doc_scope}  chunk={toplam[0]}  dosya={toplam[1]}")
        print(f"       rerank_backend={rc.rerank_backend}  rerank_pool={rc.rerank_pool}  "
              f"ef_search={rc.vector_ef_search}")
        if toplam[0] == 0:
            print("DURDU: bu scope'ta hiç chunk yok — 'yokluk' hükmü verilemez, scope yanlış.")
            return 2
        print("=" * 78)

        for aday in adaylar:
            kontrol = aday.get("karar") == "kontrol"
            print()
            print("#" * 78)
            print(f"[{aday['id']}]{' (KONTROL)' if kontrol else ''} {aday['soru']}")
            print(f"  gerekçe: {aday['gerekce']}")

            # --- A) Sözcüksel tarama: erişimden BAĞIMSIZ yokluk delili -------------
            print("\n  A) GÖVDE TARAMASI (korpus geneli, erişimden bağımsız)")
            toplam_isabet = 0
            with db.connection() as conn:
                for terim in aday["terimler"]:
                    adet, ornekler = _sozcuksel(conn, terim, args.doc_scope)
                    toplam_isabet += adet
                    print(f"    '{terim}' → {adet} chunk")
                    for fn, sayfa, metin in ornekler:
                        print(f"        {fn} s.{sayfa}: {_pencere(metin, terim)}")

            # --- B) Üretim erişimi: yakınlık + modelin göreceği bağlam -------------
            print(f"\n  B) ÜRETİM ERİŞİMİ (search_hybrid, top {args.top}; modelin GÖRECEĞİ bağlam)")
            sonuc = service.search_hybrid(aday["soru"], top_k=args.top, user_ctx=ctx)
            for sira, ch in enumerate(sonuc, 1):
                kaynak = ch.get("source") or {}
                bas = (ch.get("text") or "").replace("\n", " ")[:220]
                print(f"    {sira:2d}. [{ch.get('score'):.4f}] {kaynak.get('file_name')} "
                      f"s.{kaynak.get('page')} [{kaynak.get('section') or '-'}]")
                print(f"        {bas}…")

            # --- Hüküm: OTOMATİK DEĞİL, okumaya yönlendirir ------------------------
            if kontrol:
                hkm = ("KONTROL GEÇTİ (tarama varlığı görüyor)" if toplam_isabet
                       else "KONTROL ÇAKILDI — tarama bozuk, aday hükümlerine GÜVENME")
            elif toplam_isabet == 0:
                hkm = "terim isabeti SIFIR → yokluk güçlü; B'deki adaylar konuca yakınsa aday SAĞLAM"
            else:
                hkm = (f"terim isabeti {toplam_isabet} → A'daki gövdeleri OKU: bunlar soruyu "
                       "CEVAPLIYORSA aday reddedilir, yalnız konuya değiniyorsa aday durur")
            print(f"\n  ⇒ {hkm}")
    finally:
        db.close()

    print()
    print("=" * 78)
    print("Hüküm otomatik verilmez. Gövdeler okunup aday JSON'daki `karar` alanı")
    print("'onaylandi'/'reddedildi' yapılır; üretici yalnız 'onaylandi' olanları yazar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

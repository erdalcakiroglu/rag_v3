#!/usr/bin/env python
"""Komşu-pencere ızgara probu — TAVAN ile GERÇEKLEŞENİ ayırır. SALT-OKUMA.

NEDEN VAR
    İlk A/B (2026-08-14, `rerank_neighbor_window=1`, `_top=20`) karneyi dört
    satırda da KIPIRDATMADI: GENEL 0.682, single_fact 0.947, synthesis 0.145 —
    birebir aynı. Ön-kontrol kolun gerçekten farklı koştuğunu kanıtlamıştı
    (sorgu başına +20…26 komşu, TEI 221-224 chunk skorladı). Yani sonuç
    "genişletme etkisiz" DEĞİL, "eklenen komşuların hiçbiri top-10'a giremedi".

    Bunun İKİ ayrı sebebi olabilir ve karne sayısı ikisini AYIRT EDEMEZ:

      (A) ALTIN CHUNK HAVUZA HİÇ GİRMEDİ. Tohum yalnız havuzun ilk 20'sinden
          alınıyor; teşhis ise "doğru DOSYA havuzda" derken 200 derinliği
          kastediyordu. Altın chunk'ın komşusu 21-200 arasındaysa hiç çekilmez.
      (B) GİRDİ AMA CROSS-ENCODER GÖMDÜ. Komşu havuza eklendi, TEI skorladı ve
          10'un altında bıraktı. Bu durumda pencereyi büyütmek İŞE YARAMAZ,
          sorun sıralayıcıdadır.

    (A) ise `rerank_neighbor_top`/`window` büyütmek çözer; (B) ise büyütmek
    yalnız TEI maliyetini artırır. Ayrımı ÖLÇMEDEN parametre süpürmek, aynı
    sayıyı farklı bahanelerle üç kez okumak olurdu ([[olcum-zemini-dersleri]]).

NASIL AYIRIR
    Her (T=rerank_neighbor_top, W=window) hücresi için İKİ sayı basar:
      • TAVAN   : altın chunk'ların yüzde kaçı HAVUZA GİRDİ (rerank'ten bağımsız)
      • recall@10: aynı havuz TEI ile sıralanınca karneye ne yansıdı
    İkisi arasındaki uçurum doğrudan (B)'nin büyüklüğüdür. Tavan artmıyorsa
    (A) yok demektir ve pencere büyütmek anlamsızdır.

    Baseline hücresi (W=0) da AYNI kod yoluyla ölçülür — kıyas kolun kendi
    ölçümüyle yapılır, karneden hatırlanan sayıyla değil.

NEDEN `StoreRetriever` + doğrudan `service.rerank`
    `RetrievalService._effective_top_k` isteği `max_top_k`=20'ye kırpar → 200
    derinlikli havuz servis üzerinden ALINAMAZ (bkz. synthesis_teshis_probe).
    Havuz store yolundan ÜRETİM parametreleriyle çekilir, komşular bellekte
    hesaplanır, sıralama üretimin `service.rerank`'i ile yapılır.

SESSİZ PASSTHROUGH BEKÇİSİ
    Üretim `_tei_rerank`'i timeout/bağlantı hatasında SESSİZCE giriş sırasını
    döndürür. Ölçümde bu "fark yok" YALANI üretir. Prob her sorguda dönen sıranın
    girişle birebir aynı olup olmadığını sayar ve oran yüksekse GÜRÜLTÜYLE uyarır.

GÜVENLİK / ZEMİN
    Yalnız SELECT + arama + rerank. DB'ye, config'e, golden'a HİÇBİR ŞEY YAZMAZ.
    Altın eşlemesi benchmark'ın ÜRETİM yolu (`map_gold_chunks`) ile yapılır.

KULLANIM (H200, konteyner dışı; TEI URL'i export edilmiş olmalı)
    python scripts/komsu_tavan_probe.py --golden v1-bddk
    python scripts/komsu_tavan_probe.py --golden v1-bddk --tepe 20,50,200 --pencere 1,2,3
"""

from __future__ import annotations

import argparse
import json
import sys

VARSAYILAN_HAVUZ = 200


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _sayilar(ham: str) -> list[int]:
    return [int(p) for p in ham.replace(" ", "").split(",") if p]


def _konum_haritasi(conn, chunk_ids: list[int]) -> dict[int, tuple[int, int]]:
    """chunk_id -> (file_id, chunk_index)."""
    if not chunk_ids:
        return {}
    rows = conn.execute(
        "SELECT chunk_id, file_id, chunk_index FROM core_chunks WHERE chunk_id = ANY(%s);",
        (list(chunk_ids),),
    ).fetchall()
    return {int(r[0]): (int(r[1]), int(r[2])) for r in rows}


def _komsu_havuzu(conn, konumlar: list[tuple[int, int]], scopes: list[str],
                  en_genis: int) -> dict[tuple[int, int], int]:
    """(file_id, chunk_index) -> chunk_id, havuzdaki her chunk'ın ±en_genis komşuluğu.

    Tek sorguda çekilir; (T, W) ızgarasının her hücresi bunun BELLEKTEKİ alt
    kümesidir — her hücre için DB'ye gitmek ızgarayı gereksiz yere pahalılaştırırdı.
    Kapsam süzgeci burada da uygulanır: pencere, kullanıcının göremeyeceği bir
    chunk'ı ölçüme sokamaz (üretim davranışıyla aynı).
    """
    if not konumlar:
        return {}
    fids = [f for f, _ in konumlar]
    ixs = [i for _, i in konumlar]
    rows = conn.execute(
        """
        SELECT DISTINCT c.chunk_id, c.file_id, c.chunk_index
        FROM unnest(%s::int[], %s::int[]) AS t(fid, ix)
        JOIN core_chunks c
          ON c.file_id = t.fid
         AND c.chunk_index BETWEEN t.ix - %s AND t.ix + %s
        JOIN core_files f ON f.file_id = c.file_id
        WHERE f.doc_scope = ANY(%s);
        """,
        (fids, ixs, int(en_genis), int(en_genis), scopes),
    ).fetchall()
    return {(int(r[1]), int(r[2])): int(r[0]) for r in rows}


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description="Komşu-pencere tavan/gerçekleşen ızgarası",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--golden", default="v1-bddk", help="DB set_version (vars. v1-bddk)")
    ap.add_argument("--havuz", type=int, default=VARSAYILAN_HAVUZ,
                    help=f"hibrit aday havuzu (vars. {VARSAYILAN_HAVUZ} = üretim)")
    ap.add_argument("--tepe", default="20,50,200",
                    help="rerank_neighbor_top adayları (virgüllü, vars. 20,50,200)")
    ap.add_argument("--pencere", default="1,2,3",
                    help="window adayları (virgüllü, vars. 1,2,3)")
    ap.add_argument("--k", type=int, default=10, help="recall@k (vars. 10)")
    ap.add_argument("--tavan-only", action="store_true",
                    help="rerank'i HİÇ çağırma; yalnız tavanı ölç (TEI maliyeti sıfır, "
                         "saniyeler sürer). Kategori kırılımını ucuza almak için.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from ragintel.config.loader import load_config
    from ragintel.config.settings import DbSettings
    from ragintel.database import Database
    from ragintel.database.config_store import make_db_reader
    from ragintel.eval import repository as erepo
    from ragintel.eval.metrics import recall_at_k
    from ragintel.eval.retrieval_benchmark import (
        StoreRetriever, from_db_rows, map_gold_chunks,
    )
    from ragintel.retrieval import RetrievalService

    tepeler = _sayilar(args.tepe)
    pencereler = _sayilar(args.pencere)
    en_genis = max(pencereler) if pencereler else 0

    db = Database(DbSettings()).open()
    try:
        with db.connection() as conn:
            rows = erepo.list_golden_records(conn, args.golden)
        if not rows:
            print(f"HATA: '{args.golden}' setinde kayıt yok.", file=sys.stderr)
            return 2
        kayitlar = [r for r in from_db_rows(rows) if r.answerable]
        with db.connection() as conn:
            esleme = map_gold_chunks(conn, kayitlar)
        print(f"KAYNAK: db:{args.golden} · {len(kayitlar)} answerable kayıt · "
              f"quote eşleme {esleme.mapped_evidence}/{esleme.total_evidence} "
              f"= %{esleme.rate * 100:.1f}")
        if esleme.rate < 0.95:
            print("  ⚠ eşleme <%95 — ölçüm aracı zemini şüpheli, sayılar buna göre okunmalı")

        cfg = load_config(db_reader=make_db_reader(db))
        service = RetrievalService(db=db, config=cfg)
        rc = service.retrieval_cfg
        print(f"ÜRETİM AYARI: rerank_backend={rc.rerank_backend} · "
              f"rerank_pool={rc.rerank_pool} · ef_search={rc.vector_ef_search}")

        embed_cache = {r.question: service._embed_query(r.question)[0] for r in kayitlar}
        retriever = StoreRetriever(
            service.store, embed_cache, name="hybrid-derin", method="hybrid",
            ef_search=int(rc.vector_ef_search),
            iterative_scan=str(rc.hnsw_iterative_scan),
            fusion=str(rc.hybrid_fusion), rrf_k=int(rc.hybrid_rrf_k),
            dense_weight=float(rc.hybrid_dense_weight),
            sparse_weight=float(rc.hybrid_sparse_weight),
            sparse_variant=str(rc.hybrid_sparse_variant))

        # --- Havuzlar ve komşuluk haritası: sorgu başına BİR kez -------------
        durum: dict[str, dict] = {}
        for kayit in kayitlar:
            havuz = retriever.rank(kayit.question, kayit.doc_scope, args.havuz)
            with db.connection() as conn:
                konum = _konum_haritasi(conn, havuz)
                yerlesim = _komsu_havuzu(
                    conn, [konum[c] for c in havuz if c in konum],
                    [kayit.doc_scope], en_genis)
            durum[kayit.id] = {
                "kayit": kayit, "havuz": havuz, "konum": konum, "yerlesim": yerlesim,
                "gold": esleme.gold_by_id.get(kayit.id, set()),
            }

        kategoriler = sorted({r.category for r in kayitlar})
        hucreler: list[dict] = []
        ayni_sira = 0
        toplam_sira = 0

        def _hucre(tepe: int, pencere: int) -> dict:
            nonlocal ayni_sira, toplam_sira
            satirlar = []
            for kid, d in durum.items():
                havuz, konum, yerlesim = d["havuz"], d["konum"], d["yerlesim"]
                gold = d["gold"]
                genis = list(havuz)
                if pencere > 0:
                    var = set(havuz)
                    for c in havuz[:tepe]:
                        if c not in konum:
                            continue
                        fid, ix = konum[c]
                        for delta in range(-pencere, pencere + 1):
                            komsu = yerlesim.get((fid, ix + delta))
                            if komsu is not None and komsu not in var:
                                var.add(komsu)
                                genis.append(komsu)
                if args.tavan_only:
                    sirali = []
                else:
                    uc = {"user_id": "tavan-probe", "tenant_id": "eval", "roles": ["eval"],
                          "allowed_doc_scopes": [d["kayit"].doc_scope]}
                    sira = service.rerank(d["kayit"].question, genis, user_ctx=uc)
                    sirali = [int(s["chunk_id"]) for s in sira]
                    toplam_sira += 1
                    if sirali == genis:
                        ayni_sira += 1
                satirlar.append({
                    "id": kid, "kategori": d["kayit"].category,
                    # TAVAN: altın chunk havuza GİRDİ mi (sıralamadan bağımsız)
                    "tavan": (len(gold & set(genis)) / len(gold)) if gold else 0.0,
                    "recall": recall_at_k(gold, sirali, args.k),
                    "havuz_boy": len(genis),
                })
            return {"tepe": tepe, "pencere": pencere, "satirlar": satirlar}

        hucreler.append(_hucre(0, 0))
        for tepe in tepeler:
            for pencere in pencereler:
                hucreler.append(_hucre(tepe, pencere))

        def _ort(satirlar, alan, kategori=None):
            secili = [s[alan] for s in satirlar
                      if kategori is None or s["kategori"] == kategori]
            return sum(secili) / len(secili) if secili else 0.0

        def _tablo(alan: str, baslik: str) -> None:
            print(f"\n=== {baslik} (n={len(kayitlar)}, havuz={args.havuz}, k={args.k}) ===")
            bas = f"{'T':>4} {'W':>3} {'havuz':>6} | {'GENEL':>6} |"
            for kat in kategoriler:
                bas += f" {kat[:12]:>13}"
            print(bas)
            print("-" * len(bas))
            for h in hucreler:
                s = h["satirlar"]
                boy = sum(x["havuz_boy"] for x in s) / len(s)
                satir = (f"{h['tepe']:>4} {h['pencere']:>3} {boy:>6.0f} | "
                         f"{_ort(s, alan):>6.3f} |")
                for kat in kategoriler:
                    satir += f" {_ort(s, alan, kat):>13.3f}"
                print(satir + ("  ← taban" if h["pencere"] == 0 else ""))

        # İKİ AYRI TABLO — kategori kırılımı ŞART: toplam tavan artışının hangi
        # kategoriden geldiğini görmeden "kaldıraç sıralayıcı" hükmü kurulamaz.
        # synthesis'in tavanı zaten yüksekse sorun getirmede DEĞİL sıralamadadır;
        # düşükse pencere doğru yerde ama yetersizdir. Aynı sayı, iki zıt karar.
        _tablo("tavan", "TAVAN — altın chunk havuza girdi mi (sıralamadan bağımsız)")
        if not args.tavan_only:
            _tablo("recall", f"REC — TEI sıralamasından sonra recall@{args.k}")
        else:
            print("\n(--tavan-only: rerank hiç çağrılmadı, REC tablosu YOK)")

        taban = hucreler[0]
        t_tavan, t_rec = _ort(taban["satirlar"], "tavan"), _ort(taban["satirlar"], "recall")
        print("\n=== OKUMA ===")
        en_iyi_tavan = max(hucreler, key=lambda h: _ort(h["satirlar"], "tavan"))
        d_tavan = _ort(en_iyi_tavan["satirlar"], "tavan") - t_tavan
        print(f"  taban (W=0): TAVAN {t_tavan:.3f}"
              + ("" if args.tavan_only else f" · REC {t_rec:.3f}"))
        print(f"  en iyi TAVAN: T={en_iyi_tavan['tepe']} W={en_iyi_tavan['pencere']} "
              f"→ {_ort(en_iyi_tavan['satirlar'],'tavan'):.3f} ({d_tavan:+.3f})")
        # KATEGORİ BAŞINA TAVAN AÇIĞI: tavan yüksek + karne düşük = sıralayıcı sorunu;
        # tavan da düşükse getirme sorunu. Toplam sayı bu ikisini gizler.
        for kat in kategoriler:
            tv = _ort(taban["satirlar"], "tavan", kat)
            en = _ort(en_iyi_tavan["satirlar"], "tavan", kat)
            ek = "" if args.tavan_only else f" · REC {_ort(taban['satirlar'],'recall',kat):.3f}"
            print(f"    {kat:20s} TAVAN taban {tv:.3f} → en iyi {en:.3f} ({en-tv:+.3f}){ek}")
        if args.tavan_only:
            print("  (REC ölçülmedi — hüküm için --tavan-only'siz koşum gerekir)")
            if args.json:
                print(json.dumps(
                    [{"tepe": h["tepe"], "pencere": h["pencere"], "satirlar": h["satirlar"]}
                     for h in hucreler], ensure_ascii=False, indent=2))
            return 0
        en_iyi_rec = max(hucreler, key=lambda h: _ort(h["satirlar"], "recall"))
        d_rec = _ort(en_iyi_rec["satirlar"], "recall") - t_rec
        print(f"  en iyi REC  : T={en_iyi_rec['tepe']} W={en_iyi_rec['pencere']} "
              f"→ {_ort(en_iyi_rec['satirlar'],'recall'):.3f} ({d_rec:+.3f})")
        if d_tavan <= 0.001:
            print("  ⇒ (A) YOK: pencere altın chunk'ı havuza SOKMUYOR. Altın chunk zaten "
                  "havuzdaydı\n     ya da komşuluk mesafesinde değil. Pencereyi büyütmek "
                  "yalnız TEI maliyetini artırır.")
        elif d_rec <= 0.001:
            print("  ⇒ (B) BASKIN: altın chunk havuza GİRİYOR ama cross-encoder onu 10'un "
                  "altında\n     bırakıyor. Kaldıraç pencere değil SIRALAYICI.")
        else:
            print(f"  ⇒ pencere hem tavanı hem karneyi taşıyor; kazancın "
                  f"{d_rec/d_tavan*100:.0f}%'i tavana yansıyor.")

        oran = ayni_sira / toplam_sira if toplam_sira else 0.0
        if oran > 0.05:
            print(f"\n  ⚠ SESSİZ PASSTHROUGH ŞÜPHESİ: {ayni_sira}/{toplam_sira} sıralamada "
                  f"(%{oran*100:.0f}) TEI\n    çıktısı girişle BİREBİR aynı. Rerank "
                  f"timeout'ta sessizce passthrough'a düşmüş olabilir;\n    bu ızgaranın "
                  f"tamamını şüpheli yapar (fark yok yalanı).")

        if args.json:
            print(json.dumps(
                [{"tepe": h["tepe"], "pencere": h["pencere"], "satirlar": h["satirlar"]}
                 for h in hucreler], ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

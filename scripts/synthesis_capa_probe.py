"""probe: synthesis soruları neden 0.145'te takılı — altın chunk'lar nerede, kaça bölünmüş?

NEDEN VAR
    2026-08-14 karnesi (v1-bddk, canlı config, rerank tei + havuz 200):
        single_fact  r@10 0.947
        synthesis    r@10 0.145
    Rerank single_fact'i 0.667'den 0.947'ye taşıdı ama synthesis'e neredeyse hiç
    dokunmadı; h07 (−0.200) ve h09 (−0.500) HER havuz derinliğinde bozuluyor.
    "Sıralama sorunu" açıklaması burada yetersiz: rerank sıralamayı düzeltiyor ve
    synthesis kıpırdamıyorsa kusur sıralamadan ÖNCE olabilir.

NE AYIRIR — üç kusuru birbirinden ayırmak tek amaç
    A) HAVUZ DIŞI  : altın chunk 200 adayın içinde HİÇ yok → GETİRME kusuru.
                     Sıralamayı ne kadar iyileştirsek de bu chunk gelmez; çare
                     havuz/füzyon/embedding tarafında.
    B) HAVUZDA AMA GERİDE : havuzda var, top-20 dışında → SIRALAMA kusuru.
                     Rerank'in işi; iyileşmiyorsa cross-encoder bu soru tipinde
                     zayıf demektir.
    C) BİTİŞİK BÖLÜNME : altın chunk'lar aynı dosyanın ardışık chunk_index'lerine
                     dağılmış → GRANÜLERLİK kusuru. recall küme üzerinden
                     hesaplandığı için (metrics.recall_at_k) parçaların HEPSİ
                     top-k'ya girmeden 1.0 olmaz; tek bir cevabın iki parçası
                     ayrı ayrı yarışıyor.

    Ayrıca ölçülen dördüncü şey: ALINTI ŞİŞMESİ. Bir gold_evidence alıntısı birden
    çok chunk'a eşleşirse |gold| büyür ve recall paydası sessizce şişer — kaydın
    r@10'u düşük görünür ama kusur ölçüm aracındadır. Bu, bu projede beş kez
    yakalanan "sessiz payda" ailesinin aynısı; bakmadan varsayamayız.

MALİYET / GÜVENLİK
    SALT OKUR: yalnız SELECT + embed + TEI skorlama. DB'ye, config'e, golden'a
    YAZMAZ. Tüm getirme parametreleri ÜRETİM config'inden (`rc.*`) okunur ve
    başlıkta ilan edilir. Embedder'ı kayıt başına 1 kez çağırır (Ollama seri —
    [[kapasite-seri-lock]]); canlı karne koşarken çalıştırmayın.

KULLANIM
    python -u scripts/synthesis_capa_probe.py --golden v1-bddk --pool 200
    python -u scripts/synthesis_capa_probe.py --kategori tum        # tüm kategoriler
    python -u scripts/synthesis_capa_probe.py --kayit gs-bddk-h07 gs-bddk-h09
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="synthesis_capa_probe")
    ap.add_argument("--golden", default="v1-bddk", help="DB set_version")
    ap.add_argument("--kategori", default="synthesis",
                    help="Kategori süzgeci ('tum' = hepsi)")
    ap.add_argument("--kayit", nargs="*", default=None, help="Yalnız bu kayıt id'leri")
    ap.add_argument("--pool", type=int, default=200, help="Aday havuzu (üretimle aynı olmalı)")
    ap.add_argument("--ust", type=int, default=20, help="Karneye giren derinlik")
    ap.add_argument("--tei-url", default="http://localhost:8085",
                    help="TEI rerank ucu (RAGINTEL_TEI_RERANK_URL)")
    ap.add_argument("--neden", action="store_true",
                    help="Havuz DIŞI kalan altın chunk'lar için teşhis: metin, uzunluk, "
                         "terim örtüşmesi ve AYNI DOSYANIN havuzdaki en iyi chunk'ı")
    args = ap.parse_args(argv)

    os.environ.setdefault("RAGINTEL_TEI_RERANK_URL", args.tei_url)

    from ragintel.config.loader import load_config
    from ragintel.config.settings import DbSettings
    from ragintel.database import Database
    from ragintel.database.config_store import make_db_reader
    from ragintel.eval import repository as repo
    from ragintel.eval.retrieval_benchmark import corpus_fingerprint, from_db_rows
    from ragintel.retrieval import RetrievalService
    from ragintel.text import normalize_for_quote, normalize_for_search

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
        if args.kategori != "tum":
            kayitlar = [r for r in kayitlar if r.category == args.kategori]
        if args.kayit:
            istenen = set(args.kayit)
            kayitlar = [r for r in kayitlar if r.id in istenen]
        if not kayitlar:
            print("HATA: süzgeçten geçen kayıt yok.", file=sys.stderr)
            return 2

        with db.connection() as conn:
            parmak = corpus_fingerprint(conn, [r.doc_scope for r in kayitlar])

        # Payda AÇIKÇA ilan edilir.
        print(f"golden={args.golden} · kategori={args.kategori} · kayıt={len(kayitlar)} "
              f"· havuz={args.pool} · tepe={args.ust}")
        print(f"KORPUS: {parmak['files']} dosya · {parmak['chunks']} chunk "
              f"· son değişiklik {parmak['last_change']}")
        print(f"ZEMİN: rerank_backend={rc.rerank_backend} · rerank_pool={rc.rerank_pool} "
              f"· ef_search={rc.vector_ef_search} · fusion={rc.hybrid_fusion} "
              f"· rrf_k={rc.hybrid_rrf_k} · dense={rc.hybrid_dense_weight} "
              f"· sparse={rc.hybrid_sparse_weight}")
        print()

        ortak = dict(
            top_k=args.pool,
            ef_search=int(rc.vector_ef_search),
            iterative_scan=str(rc.hnsw_iterative_scan),
            fusion_strategy=str(rc.hybrid_fusion),
            rrf_k=int(rc.hybrid_rrf_k),
            dense_weight=float(rc.hybrid_dense_weight),
            sparse_weight=float(rc.hybrid_sparse_weight),
            sparse_variant=str(rc.hybrid_sparse_variant),
        )

        toplam = defaultdict(int)
        bitisik_kayit, sisen_kayit = [], []

        for rec in kayitlar:
            # --- 1) Alıntı → chunk eşlemesi (alıntı BAŞINA, şişme görünsün) ------
            with db.connection() as conn:
                per_alinti: list[tuple[str, list[int]]] = []
                for ev in rec.evidence:
                    cands = repo.list_candidate_chunks(
                        conn, file_name=ev["file_name"], doc_scope=rec.doc_scope,
                        page=ev.get("page"), sheet=ev.get("sheet"))
                    qn = normalize_for_quote(ev["quote"])
                    hit = [c["chunk_id"] for c in cands
                           if qn and qn in (c["chunk_text_norm"] or "")]
                    per_alinti.append((ev["file_name"], hit))
                gold = sorted({cid for _f, hit in per_alinti for cid in hit})
                yerler = _chunk_yerleri(conn, gold)

            if not gold:
                print(f"{rec.id}  {rec.category}  ALTIN CHUNK EŞLEŞMEDİ — atlanıyor "
                      f"(kanıt {len(rec.evidence)})")
                print()
                continue

            # --- 2) Havuz (hibrit sıra) ve rerank sırası -------------------------
            vek = service._embed_query(rec.question)[0]
            havuz = [int(r["chunk_id"]) for r in service.store.search_hybrid(
                query=rec.question, query_vector=vek,
                normalized_query=normalize_for_search(rec.question),
                allowed_doc_scopes=[rec.doc_scope], **ortak)]
            if str(rc.rerank_backend) == "tei":
                sirali = [int(r["chunk_id"])
                          for r in service._tei_rerank(rec.question, havuz, [rec.doc_scope])]
            else:
                sirali = list(havuz)

            h_sira = {cid: i + 1 for i, cid in enumerate(havuz)}
            r_sira = {cid: i + 1 for i, cid in enumerate(sirali)}

            disarida = [c for c in gold if c not in h_sira]
            geride = [c for c in gold if c in r_sira and r_sira[c] > args.ust]
            icerde = [c for c in gold if c in r_sira and r_sira[c] <= args.ust]

            toplam["gold"] += len(gold)
            toplam["havuz_disi"] += len(disarida)
            toplam["geride"] += len(geride)
            toplam["tepede"] += len(icerde)

            # --- 3) Bitişik bölünme ---------------------------------------------
            gruplar = _bitisik_gruplar(yerler, gold)
            bitisik = [g for g in gruplar if len(g) > 1]
            if bitisik:
                bitisik_kayit.append(rec.id)
                toplam["bitisik_chunk"] += sum(len(g) for g in bitisik)

            sisme = [(f, h) for f, h in per_alinti if len(h) > 1]
            if sisme:
                sisen_kayit.append(rec.id)

            # --- rapor ------------------------------------------------------------
            dosya_sayisi = len({yerler[c][0] for c in gold if c in yerler})
            print(f"{rec.id}  {rec.category}  kanıt {len(rec.evidence)} → "
                  f"gold {len(gold)} chunk / {dosya_sayisi} dosya")
            print(f"  tepe({args.ust}) {len(icerde)}/{len(gold)} · "
                  f"havuzda ama geride {len(geride)}/{len(gold)} · "
                  f"HAVUZ DIŞI {len(disarida)}/{len(gold)}")
            for cid in gold:
                dosya, idx, sayfa = yerler.get(cid, ("?", -1, None))
                h = h_sira.get(cid)
                r = r_sira.get(cid)
                print(f"    chunk {cid:>7}  {dosya[:44]:<44} #{idx:<5} s.{sayfa}"
                      f"   hibrit {h if h else '—':>4}  rerank {r if r else '—':>4}")
            for grup in bitisik:
                dosya = yerler[grup[0]][0]
                idxler = ",".join(str(yerler[c][1]) for c in grup)
                print(f"    ⚠ BİTİŞİK: {dosya[:44]} #{idxler} — tek cevap {len(grup)} "
                      f"chunk'a bölünmüş, recall hepsini top-{args.ust}'de ister")
            for dosya, hit in sisme:
                print(f"    ⚠ ALINTI ŞİŞMESİ: {dosya[:44]} tek alıntı {len(hit)} chunk'a "
                      f"eşleşti — recall paydası şişiyor")

            if args.neden and disarida:
                with db.connection() as conn:
                    _neden_raporu(conn, rec, disarida, havuz, h_sira, yerler,
                                  normalize_for_search, toplam)
            print()

        # --- ÖZET + OKUMA ---------------------------------------------------------
        g = toplam["gold"] or 1
        print("--- ÖZET ---")
        print(f"toplam altın chunk {toplam['gold']} · "
              f"tepe({args.ust}) {toplam['tepede']} (%{toplam['tepede']/g*100:.1f}) · "
              f"havuzda ama geride {toplam['geride']} (%{toplam['geride']/g*100:.1f}) · "
              f"HAVUZ DIŞI {toplam['havuz_disi']} (%{toplam['havuz_disi']/g*100:.1f})")
        print(f"bitişik bölünme: {len(bitisik_kayit)} kayıt "
              f"({toplam['bitisik_chunk']} chunk) {bitisik_kayit}")
        print(f"alıntı şişmesi: {len(sisen_kayit)} kayıt {sisen_kayit}")
        if args.neden:
            print(f"havuz dışı kırılımı: belge bulunuyor-yanlış parça {toplam['belge_var']} "
                  f"· belge hiç bulunamıyor {toplam['belge_yok']}")
            hd = toplam["havuz_disi"] or 1
            print("pencere genişletme KAZANIMI (havuz dışı chunk'lardan kaçı kurtulur): "
                  + " · ".join(f"±{e} → {toplam[f'pencere_{e}']}/{toplam['havuz_disi']} "
                               f"(%{toplam[f'pencere_{e}']/hd*100:.0f})" for e in (1, 2, 3, 5)))

        print("\n--- OKUMA ---")
        buyuk = max(toplam["havuz_disi"], toplam["geride"])
        if buyuk == 0:
            print("Altın chunk'ların hepsi zaten tepede — düşük skorun kaynağı burada DEĞİL.")
        elif toplam["havuz_disi"] >= toplam["geride"]:
            print("BASKIN KUSUR: GETİRME (havuz dışı). Altın chunk 200 adaya bile giremiyor;\n"
                  "  rerank'i ne kadar iyileştirsek de bu chunk gelmez. Havuz derinliği,\n"
                  "  füzyon ağırlıkları veya embedding/terim uyuşması aranmalı.")
        else:
            print("BASKIN KUSUR: SIRALAMA (havuzda ama geride). Chunk aday havuzunda VAR,\n"
                  "  top-20'ye çıkamıyor. Rerank'in işi; synthesis'te kıpırdamıyorsa\n"
                  "  cross-encoder bu soru tipinde zayıf demektir.")
        if args.neden and (toplam["belge_var"] or toplam["belge_yok"]):
            if toplam["belge_var"] > toplam["belge_yok"]:
                print("HAVUZ DIŞI KIRILIMI: baskın olan BELGE BULUNUYOR-YANLIŞ PARÇA.\n"
                      "  Doğru dosya havuzda ama kanıt taşıyan chunk'ı değil. Bu bir\n"
                      "  chunk SEÇİMİ sorunu.")
                p2 = toplam["pencere_2"]
                if p2 >= max(1, toplam["havuz_disi"] // 2):
                    print(f"  ⇒ KOMŞU PENCERESİ İŞE YARAR: havuz dışı {toplam['havuz_disi']} "
                          f"chunk'ın {p2}'i ±2 komşulukta. Küçük bir pencere kazancın "
                          f"çoğunu alır.")
                else:
                    print(f"  ⇒ KOMŞU PENCERESİ YETMEZ: ±2 komşulukta yalnız {p2}/"
                          f"{toplam['havuz_disi']} var. Kanıt chunk'ları havuzdaki "
                          f"kardeşlerinden UZAK; pencere yerine belge başına daha çok "
                          f"chunk alma (per-doc kota) düşünülmeli.")
            else:
                print("HAVUZ DIŞI KIRILIMI: baskın olan BELGE HİÇ BULUNAMIYOR.\n"
                      "  Dosyadan tek chunk bile havuza girmiyor ⇒ kusur chunk seçiminde\n"
                      "  değil, sorgu-belge eşleşmesinde. Sparse varyantı/füzyon ağırlığı\n"
                      "  ve sorgu genişletme aranmalı; komşu genişletmenin faydası olmaz.")
        if bitisik_kayit:
            print(f"AYRICA GRANÜLERLİK: {len(bitisik_kayit)} kayıtta tek cevap aynı dosyanın\n"
                  f"  ardışık chunk'larına bölünmüş. recall küme üzerinden hesaplandığı için\n"
                  f"  parçaların HEPSİ top-{args.ust}'ye girmeden 1.0 olmaz — chunk boyutu ya da\n"
                  f"  komşu-chunk birleştirme (lookup_window) bu kayıtları tek başına kurtarabilir.")
        if sisen_kayit:
            print(f"UYARI — ÖLÇÜM ARACI: {len(sisen_kayit)} kayıtta tek alıntı birden çok chunk'a\n"
                  f"  eşleşti; |gold| şişiyor ve recall HAK ETTİĞİNDEN düşük görünüyor.\n"
                  f"  Bu kayıtların düşük skoru sisteme yazılmadan önce golden düzeltilmeli.")
        return 0
    finally:
        db.close()


def _neden_raporu(conn, rec, disarida, havuz, h_sira, yerler, normalize_for_search,
                  toplam) -> None:
    """Havuz DIŞI altın chunk: metni neye benziyor, DOSYASI bulunmuş mu?

    Kritik ayrım — ikisi tamamen farklı çözüme gider, karıştırılırsa yanlış işe
    girişilir:
      · aynı dosyanın BAŞKA chunk'ı havuzda VAR  → belge bulunuyor, yanlış parçası
        seçiliyor. Çare chunk seçimi/komşu genişletme tarafında.
      · dosyadan hiçbir chunk havuzda YOK        → belge hiç bulunamıyor. Çare
        terim/anlam uyuşması (sparse varyantı, füzyon ağırlığı, sorgu genişletme).
    """
    rows = conn.execute(
        "SELECT c.chunk_id, f.file_name, c.chunk_index FROM core_chunks c "
        "JOIN core_files f USING (file_id) WHERE c.chunk_id = ANY(%s);",
        (list(havuz),)).fetchall()
    havuz_dosya: dict[str, list[int]] = defaultdict(list)
    havuz_idx: dict[int, int] = {}
    for cid, fad, cidx in rows:
        havuz_dosya[str(fad)].append(int(cid))
        havuz_idx[int(cid)] = int(cidx)

    q_terim = {t for t in normalize_for_search(rec.question).split() if len(t) > 2}
    for cid in disarida:
        r = conn.execute(
            "SELECT chunk_text, chunk_text_norm, token_count FROM core_chunks "
            "WHERE chunk_id = %s;", (cid,)).fetchone()
        if r is None:
            print(f"    · HAVUZ DIŞI chunk {cid}: DB'de YOK (silinmiş olabilir)")
            continue
        metin, norm, tok = str(r[0] or ""), str(r[1] or ""), r[2]
        dosya, idx, _s = yerler.get(cid, ("?", -1, None))
        kardes = havuz_dosya.get(dosya, [])
        en_iyi = min((h_sira[c] for c in kardes if c in h_sira), default=None)
        ortak = q_terim & set(norm.split())
        if kardes:
            toplam["belge_var"] += 1
            tani = (f"aynı dosyadan havuzda {len(kardes)} chunk (en iyi sıra {en_iyi}) "
                    f"→ BELGE BULUNUYOR, YANLIŞ PARÇA seçiliyor")
        else:
            toplam["belge_yok"] += 1
            tani = "aynı dosyadan havuzda 0 chunk → BELGE HİÇ BULUNAMIYOR"
        ozet = " ".join(metin.split())[:150]
        print(f"    · HAVUZ DIŞI chunk {cid}  {dosya[:44]} #{idx}")
        print(f"        {len(metin)} karakter / {tok} token · soru terimi örtüşmesi "
              f"{len(ortak)}/{len(q_terim)} {sorted(ortak)[:6]}")
        print(f"        {tani}")

        # KOMŞU MESAFESİ: pencere genişletmenin bu chunk'ı kurtarıp kurtarmayacağını
        # kod yazmadan söyleyen tek sayı. Mesafe 1-2 ise ±k pencere işe yarar;
        # 20 ise yaramaz ve o yolu hiç açmamak gerekir.
        komsu_idx = sorted({havuz_idx[c] for c in kardes if c in havuz_idx})
        if komsu_idx and idx >= 0:
            mesafe = min(abs(k - idx) for k in komsu_idx)
            toplam[f"mesafe_{min(mesafe, 9)}"] += 1
            for esik in (1, 2, 3, 5):
                if mesafe <= esik:
                    toplam[f"pencere_{esik}"] += 1
            hukum = ("±1 pencere KURTARIR" if mesafe <= 1 else
                     f"±{mesafe} gerekir" if mesafe <= 5 else
                     "pencere KURTARMAZ (çok uzak)")
            print(f"        havuzdaki kardeş index'ler {komsu_idx[:12]} · "
                  f"en yakın komşu mesafesi {mesafe} → {hukum}")
        else:
            toplam["mesafe_yok"] += 1
        print(f"        metin: {ozet!r}")


def _chunk_yerleri(conn, chunk_ids: list[int]) -> dict[int, tuple[str, int, object]]:
    """chunk_id → (dosya adı, chunk_index, sayfa)."""
    if not chunk_ids:
        return {}
    rows = conn.execute(
        "SELECT c.chunk_id, f.file_name, c.chunk_index, c.page_number "
        "FROM core_chunks c JOIN core_files f USING (file_id) "
        "WHERE c.chunk_id = ANY(%s);", (list(chunk_ids),)).fetchall()
    return {int(r[0]): (str(r[1]), int(r[2]), r[3]) for r in rows}


def _bitisik_gruplar(yerler: dict, gold: list[int]) -> list[list[int]]:
    """Aynı dosyada ardışık chunk_index'e sahip altın chunk'ları gruplar."""
    dosyaya: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for cid in gold:
        if cid in yerler:
            dosya, idx, _s = yerler[cid]
            dosyaya[dosya].append((idx, cid))
    gruplar: list[list[int]] = []
    for kayitlar in dosyaya.values():
        kayitlar.sort()
        grup = [kayitlar[0][1]]
        for onceki, simdi in zip(kayitlar, kayitlar[1:]):
            if simdi[0] - onceki[0] == 1:
                grup.append(simdi[1])
            else:
                gruplar.append(grup)
                grup = [simdi[1]]
        gruplar.append(grup)
    return gruplar


if __name__ == "__main__":
    if os.name == "nt":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

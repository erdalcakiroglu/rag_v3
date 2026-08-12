#!/usr/bin/env python
"""Mükerrer baskı kümelerini bulur ve HANGİSİ KALSIN kararına veri üretir.
SALT-OKUMA — yalnız SELECT. Hiçbir şey SİLMEZ, silme komutlarını YAZDIRIR.

NEDEN
    Golden v1 teşhisi (Golden_v1_BDDK_Ankrali §5b-DÜZELTME) ölçtü: `gs-bddk-e01`
    için aynı cümlenin dört kopyası 10/13/15/21. sıralarda. Bu iki ayrı zarar:
      • ölçüm — recall paydası kopyaları ayrı kanıt sayıyor (ölçülen 0.143,
        oysa cevap 10. sırada bulunmuş),
      • ÜRETİM — `default_top_k=10` ile kullanıcıya giden bağlamın dört slotu
        aynı metin; token bütçesi bir kez ödenip dört kez harcanıyor.
    Yani bu bir eval temizliği değil, erişim kalitesi işi.

NEDEN AD'A BAKILMAZ
    `5411_Guncel_2.pdf` adında "Guncel" geçiyor ama MÜLGA 2005 metnini taşıyor;
    yürürlükteki metin `5411 sayılı Bankacılık Kanunu.pdf`'te. Ad deseniyle
    seçim yapmak bu projede bir kez daha yanıldı (bkz. `mevzuat_1340` vakası).
    Karar İÇERİKTEN verilir.

NEDEN HASH EŞİTLİĞİ YETMEDİ (2026-08-12 ölçüldü)
    İlk sürüm chunk metni hash'iyle çalıştı ve %60 eşikte **tek çift** buldu.
    Oysa `gs-bddk-e01`'in alıntısı 7 dosyada, 5411 altı baskıda duruyor.
    Kök: hash TAM eşitlik ister; farklı PDF'ler farklı chunk sınırı üretir,
    aynı cümle farklı yerlerden bölününce hash tutmaz. Yakalanan tek çift
    (7. ve 8. baskı) aynı dizgiden basıldığı için tutmuştu.
    Çözüm: hash yalnız ADAY üretir, karar İÇERİLME testiyle verilir —
    küçük dosyadan örneklenen metin pencereleri büyük dosyada aranır.
    Bu, golden alıntı eşlemesinin ta kendisidir (`position(... in
    chunk_text_norm)`), yani chunk sınırından bağımsızdır.

ÖLÇTÜĞÜ ŞEYLER
    aday     (a) hash örtüşmesi düşük eşikte + (b) AYNI golden alıntısını
             taşıyan dosya çiftleri (alıntı çözümlemesi bunu zaten ölçtü)
    içerilme küçük dosyadan N metin penceresi, büyük dosyada kaçı bulunuyor —
             kümeyi bu belirler (KÜÇÜK dosyaya oranlıdır: 40 sayfalık özet
             700 sayfalık kanunun içinde erirse küçük tarafta %100'dür)
    güncellik  "(Değişik:", "(Ek:", "(Mülga:" işaretçi sayısı + metinde geçen
             EN GEÇ yıl. Konsolide mevzuat metinlerinde bu işaretçiler
             değişiklikleri taşır; 2005 orijinali ile güncel metni ayırır.
    kapsam   chunk sayısı, sayfa sayısı, quality_score
    golden   dosya v1 evidence'ında geçiyor mu (geçiyorsa elenmesi seti
             değiştirir → üreticiye `--dislanan` eklenmeli)

NEDEN YÖN ÖNEMLİ (2026-08-12, küme 2 vakası)
    İçerilme TEK YÖNLÜ ölçülünce 58 chunk'lık 5464 sayılı kanun ile 399 chunk'lık
    `263_2.pdf` "mükerrer" göründü. Oysa küçük tarafın %100'ü büyüğün içinde
    olması mükerrer BASKI değil KAPSAMA demek olabilir: derleme, kanunu içeriyor.
    Bu durumda büyüğü silmek ilgisiz içeriği de siler. Artık iki yön de ölçülür:
      simetrik (her iki yön yüksek)  -> aynı belgenin iki baskısı
      asimetrik (biri yüksek biri düşük) -> kapsayan/kapsanan, baskı DEĞİL

NEDEN KAPSAM MODU VAR
    Küme 1'de tutulacak aday (`5411 sayılı Bankacılık Kanunu.pdf`, 117 sayfa)
    eleyeceği dosyaların HEPSİNDEN az sayfalı (196-209). Chunk sayıları yakın
    (269 vs 289-309) yani dizgi farkı olabilir — ama "olabilir" ile altı dosya
    silinmez. `--kapsam` her elenecek dosyanın metnini tutulacak dosyada arar,
    oranı verir ve BULUNAMAYAN pencereleri basar: eksik olan gerçek madde
    gövdesi mi, yoksa yayıncı önsözü/dizini mi — göze bakılarak karar verilir.
    Ayrıca golden alıntılarının tutulacak dosyada çözülüp çözülmediğini
    doğrudan DB'de sınar (evidence JSON'undaki yokluk eşleşme kusuru da olabilir,
    kanıt değil).

KARARI KİM VERİR
    Bu probe ADAY gösterir, KARAR VERMEZ. Çıktının sonundaki `dosya_kaldir.py`
    komutları hazır ama koşulmaz — her satır insan onayından geçer.
    Şüpheli küme (güncellik işaretçileri çelişkili / örtüşme eşiğe yakın)
    AYRI başlık altında listelenir ve komutu ÜRETİLMEZ.

KULLANIM
    python scripts/mukerrer_baski_probe.py
    python scripts/mukerrer_baski_probe.py --esik 0.7 --golden eval/golden/v1.jsonl
    python scripts/mukerrer_baski_probe.py --kapsam "5411 sayılı Bankacılık Kanunu.pdf"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

VARSAYILAN_ESIK = 0.60
VARSAYILAN_MIN_UZUNLUK = 200
ADAY_ESIGI = 0.05          # hash örtüşmesi: aday olmaya yeter, karara yetmez
ORNEK_SAYISI = 25          # içerilme testi için pencere sayısı
KAPSAM_ORNEK = 60          # --kapsam modu daha hassas: daha çok pencere
PENCERE = 160              # pencere uzunluğu (karakter)
ASIMETRI_ORANI = 0.60      # ters yön bunun altındaysa "kapsama", baskı değil

# Konsolide mevzuat metninde değişiklik işaretçileri.
# Parantez ŞART DEĞİL: ilk koşumda 203 sayfalık Bankacılık Kanunu metninde
# işaretçi sayısı 0 çıktı — parantez/boşluk deseni PDF'ten PDF'e değişiyor.
# Diyakritikler de bozulmuş olabilir (korpusta ölçülmüş bir kusur), o yüzden
# ğ/g ve ş/s birlikte kabul edilir.
_ISARET = re.compile(r"\(?\s*(?:De[ğg]i[şs]ik|M[üu]lga)\s*:", re.IGNORECASE)
_YIL = re.compile(r"\b(19[89]\d|20[0-2]\d)\b")


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _kumeler(ciftler: list[tuple[int, int]]) -> list[set[int]]:
    """Birleşen çiftleri bağlantılı bileşene toplar (union-find yerine basit)."""
    kume: list[set[int]] = []
    for a, b in ciftler:
        hedef = [k for k in kume if a in k or b in k]
        if not hedef:
            kume.append({a, b})
            continue
        birlesik = {a, b}
        for k in hedef:
            birlesik |= k
            kume.remove(k)
        kume.append(birlesik)
    return kume


def main() -> int:
    global PENCERE
    _force_utf8()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--esik", type=float, default=VARSAYILAN_ESIK,
                    help=f"küçük dosyaya oranlı örtüşme eşiği (vars. {VARSAYILAN_ESIK})")
    ap.add_argument("--min-uzunluk", type=int, default=VARSAYILAN_MIN_UZUNLUK,
                    help="bu uzunluğun altındaki chunk'lar hash'lenmez "
                         "(başlık/boş satır tesadüfi eşleşir)")
    ap.add_argument("--golden", type=Path, default=Path("eval/golden/v1.jsonl"),
                    help="evidence çakışması için golden JSONL (yoksa atlanır)")
    ap.add_argument("--doc-scope", default="default")
    ap.add_argument("--kapsam", metavar="DOSYA",
                    help="TUTULACAK dosya adı (parça eşleşme yeter): kümedeki "
                         "her elenecek dosyanın bu dosyada ne kadar karşılandığı "
                         "ölçülür, bulunamayan pencereler ve golden alıntı "
                         "çözünürlüğü basılır")
    ap.add_argument("--pencere", type=int, default=PENCERE,
                    help=f"içerilme penceresi uzunluğu (vars. {PENCERE}). "
                         "Kısaltmak dipnot gürültüsünü ayırır: TBB baskıları "
                         "madde gövdesine dipnot numarası serpiştiriyor, uzun "
                         "pencere bu yüzden tutmaz. Ters yön kısa pencerede "
                         "sıçrıyorsa fark İÇERİK değil DİZGİ demektir")
    ap.add_argument("--kayip-goster", type=int, default=4,
                    help="--kapsam modunda dosya başına basılacak bulunamayan "
                         "pencere sayısı (vars. 4)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    PENCERE = max(40, int(args.pencere))

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database
    # Golden alıntısı HAM metindir; `chunk_text_norm` NFKC+lowercase+ws-collapse
    # ile üretilir. Ham alıntıyı norm sütununda aramak (ilk sürümün kusuru)
    # büyük harfli her alıntıyı "bulunamadı" gösterir. Eşleme, eval'in
    # kullandığı TEK doğruluk kaynağından geçirilir.
    from ragintel.text.normalize import normalize_for_quote

    golden_dosyalari: dict[str, list[str]] = {}
    golden_alintilari: dict[str, list[tuple[str, str]]] = {}
    if args.golden.exists():
        for satir in args.golden.read_text(encoding="utf-8").splitlines():
            if not satir.strip():
                continue
            kayit = json.loads(satir)
            for ev in kayit.get("gold_evidence", []):
                golden_dosyalari.setdefault(ev["file_name"], []).append(kayit["id"])
                golden_alintilari.setdefault(ev["file_name"], []).append(
                    (kayit["id"], ev["quote"]))

    db = Database(DbSettings()).open()
    try:
        with db.connection() as conn:
            # Dosya-dosya örtüşme: aynı chunk metni hash'ini paylaşan dosya çiftleri.
            ciftler = conn.execute(
                """
                WITH h AS (
                    SELECT c.file_id, md5(c.chunk_text_norm) AS hh
                    FROM core_chunks c JOIN core_files f USING (file_id)
                    WHERE f.doc_scope = %s AND f.status = 'COMPLETED'
                      AND c.chunk_text_norm IS NOT NULL
                      AND length(c.chunk_text_norm) >= %s
                ),
                d AS (SELECT hh FROM h GROUP BY hh HAVING count(DISTINCT file_id) > 1),
                say AS (SELECT file_id, count(DISTINCT hh) AS n FROM h GROUP BY file_id)
                SELECT a.file_id, b.file_id, count(DISTINCT a.hh) AS ortak,
                       s1.n, s2.n
                FROM h a
                JOIN h b ON a.hh = b.hh AND a.file_id < b.file_id
                JOIN d ON d.hh = a.hh
                JOIN say s1 ON s1.file_id = a.file_id
                JOIN say s2 ON s2.file_id = b.file_id
                GROUP BY a.file_id, b.file_id, s1.n, s2.n;
                """,
                (args.doc_scope, args.min_uzunluk),
            ).fetchall()

            # --- ADAY ÜRETİMİ (karar değil) ---
            aday: set[tuple[int, int]] = set()
            for f1, f2, ortak, n1, n2 in ciftler:
                kucuk = min(n1, n2)
                if kucuk and ortak / kucuk >= ADAY_ESIGI:
                    aday.add((f1, f2))

            # Golden alıntı çözümlemesi aynı metni taşıyan dosyaları ZATEN ölçtü.
            # Hash'in kaçırdığı baskılar buradan gelir.
            ad_id = {}
            if golden_dosyalari:
                for fid, ad in conn.execute(
                    "SELECT file_id, file_name FROM core_files "
                    "WHERE file_name = ANY(%s) AND doc_scope = %s "
                    "AND status = 'COMPLETED';",
                    (list(golden_dosyalari), args.doc_scope),
                ).fetchall():
                    ad_id[ad] = fid
                alinti_dosya: dict[str, set[int]] = {}
                for satir in args.golden.read_text(encoding="utf-8").splitlines():
                    if not satir.strip():
                        continue
                    for ev in json.loads(satir).get("gold_evidence", []):
                        fid = ad_id.get(ev["file_name"])
                        if fid is not None:
                            alinti_dosya.setdefault(ev["quote"], set()).add(fid)
                for fidler in alinti_dosya.values():
                    sirali = sorted(fidler)
                    for i, a in enumerate(sirali):
                        for b in sirali[i + 1:]:
                            aday.add((a, b))

            if not aday:
                print("aday mükerrer çift YOK.")
                return 0

            # --- İÇERİLME TESTİ (karar bunun) ---
            boyut = dict(conn.execute(
                "SELECT file_id, count(*) FROM core_chunks "
                "WHERE file_id = ANY(%s) GROUP BY file_id;",
                (sorted({i for c in aday for i in c}),),
            ).fetchall())

            def _pencereler(fid: int, n: int = ORNEK_SAYISI) -> list[str]:
                rows = conn.execute(
                    "SELECT chunk_text_norm FROM core_chunks "
                    "WHERE file_id = %s AND length(chunk_text_norm) >= %s "
                    "ORDER BY chunk_index;",
                    (fid, PENCERE * 2),
                ).fetchall()
                if not rows:
                    return []
                adim = max(1, len(rows) // n)
                secim = rows[::adim][:n]
                # Pencere chunk'ın ORTASINDAN alınır: baş/son sınıra yakın
                # olduğu için karşı dosyada iki chunk'a bölünmüş olabilir.
                return [r[0][len(r[0]) // 2 - PENCERE // 2:][:PENCERE] for r in secim]

            def _var_mi(fid: int, parca: str) -> bool:
                return conn.execute(
                    "SELECT 1 FROM core_chunks WHERE file_id = %s "
                    "AND position(%s in chunk_text_norm) > 0 LIMIT 1;",
                    (fid, parca),
                ).fetchone() is not None

            def _kapsama(kaynak: int, hedef: int, n: int = ORNEK_SAYISI
                         ) -> tuple[int, int, list[str]]:
                """kaynak'ın pencerelerinden kaçı hedef'te bulunuyor + kayıplar."""
                pencereler = _pencereler(kaynak, n)
                bulunan, kayip = 0, []
                for p in pencereler:
                    if _var_mi(hedef, p):
                        bulunan += 1
                    else:
                        kayip.append(p)
                return bulunan, len(pencereler), kayip

            pencere_cache: dict[int, list[str]] = {}
            secili = []
            for f1, f2 in sorted(aday):
                kucuk, buyuk = (f1, f2) if boyut.get(f1, 0) <= boyut.get(f2, 0) else (f2, f1)
                for fid in (kucuk, buyuk):
                    if fid not in pencere_cache:
                        pencere_cache[fid] = _pencereler(fid)
                if not pencere_cache[kucuk]:
                    continue
                isabet = sum(1 for p in pencere_cache[kucuk] if _var_mi(buyuk, p))
                oran = isabet / len(pencere_cache[kucuk])
                if oran < args.esik:
                    continue
                # TERS YÖN: büyük dosyanın metni küçükte var mı? Simetri,
                # "iki baskı" ile "kapsayan derleme"yi ayıran şeydir.
                ters = 0.0
                if pencere_cache[buyuk]:
                    ters = sum(1 for p in pencere_cache[buyuk]
                               if _var_mi(kucuk, p)) / len(pencere_cache[buyuk])
                secili.append((f1, f2, isabet, oran, ters, kucuk, buyuk))

            if not secili:
                print(f"içerilme eşiği %{args.esik * 100:.0f} üzerinde mükerrer "
                      f"çift YOK ({len(aday)} aday sınandı).")
                return 0

            kumeler = _kumeler([(a, b) for a, b, *_ in secili])
            oranlar = {(a, b): (o, r) for a, b, o, r, *_ in secili}
            # Asimetrik çiftler: küçük büyüğün içinde ama tersi değil.
            asimetrik = [(kucuk, buyuk, r, ters)
                         for _, _, _, r, ters, kucuk, buyuk in secili
                         if ters < r * ASIMETRI_ORANI]

            tum_id = sorted({i for k in kumeler for i in k})
            bilgi_rows = conn.execute(
                """
                SELECT f.file_id, f.file_name, f.quality_score AS skor,
                       count(c.chunk_id) AS chunk_n,
                       count(DISTINCT c.page_number) AS sayfa_n,
                       string_agg(c.chunk_text, ' ') AS govde
                FROM core_files f
                LEFT JOIN core_chunks c USING (file_id)
                WHERE f.file_id = ANY(%s)
                GROUP BY f.file_id, f.file_name, f.quality_score;
                """,
                (tum_id,),
            ).fetchall()

            # --- KAPSAM MODU: "tutulacak dosya gerçekten yetiyor mu?" ---
            kapsam = None
            if args.kapsam:
                ad_of = {r[0]: r[1] for r in bilgi_rows}
                esles = [fid for fid, ad in ad_of.items()
                         if args.kapsam.lower() in ad.lower()]
                if len(esles) != 1:
                    print(f"--kapsam '{args.kapsam}' kumelerde "
                          f"{len(esles)} dosyaya uyuyor; tek olmali.")
                    for fid in esles:
                        print(f"  {ad_of[fid]}")
                    return 2
                tut_id = esles[0]
                kume = next((k for k in kumeler if tut_id in k), set())
                kapsam = {"tut": ad_of[tut_id], "satirlar": [], "golden": []}
                for fid in sorted(kume - {tut_id}, key=lambda i: ad_of[i]):
                    bulunan, toplam, kayip = _kapsama(fid, tut_id, KAPSAM_ORNEK)
                    geri, geri_n, _ = _kapsama(tut_id, fid, KAPSAM_ORNEK)
                    kapsam["satirlar"].append({
                        "ad": ad_of[fid], "bulunan": bulunan, "toplam": toplam,
                        "oran": bulunan / toplam if toplam else 0.0,
                        "geri_oran": geri / geri_n if geri_n else 0.0,
                        "kayip": kayip[:args.kayip_goster],
                    })
                # Golden alıntıları: elenecek dosyalara bağlı olanlar
                # TUTULACAK dosyada çözülüyor mu? Evidence JSON'undaki yokluk
                # eşleşme kusuru olabilir; karar DB'den verilir.
                gorulen: set[tuple[str, str]] = set()
                for fid in kume:
                    for kimlik, alinti in golden_alintilari.get(ad_of[fid], []):
                        if (kimlik, alinti) in gorulen:
                            continue
                        gorulen.add((kimlik, alinti))
                        norm = normalize_for_quote(alinti)
                        # Tutulanda yoksa hangi üye çözüyor: kaybın gerçek
                        # boyutu bu (hiç kimse çözmüyorsa alıntı zaten ölü).
                        cozen = [ad_of[o] for o in sorted(kume)
                                 if norm and _var_mi(o, norm)]
                        kapsam["golden"].append({
                            "id": kimlik, "alinti": alinti,
                            "tutta_var": ad_of[tut_id] in cozen,
                            "cozen": cozen, "kaynak": ad_of[fid],
                        })

        bilgi = {}
        for fid, ad, skor, chunk_n, sayfa_n, govde in bilgi_rows:
            metin = govde or ""
            yillar = [int(y) for y in _YIL.findall(metin)]
            bilgi[fid] = {
                "file_id": fid, "file_name": ad, "chunk_n": chunk_n,
                "sayfa_n": sayfa_n, "skor": float(skor) if skor is not None else None,
                "isaret": len(_ISARET.findall(metin)),
                "son_yil": max(yillar) if yillar else None,
                "golden": golden_dosyalari.get(ad, []),
            }

        rapor = []
        for kume in kumeler:
            uyeler = sorted((bilgi[i] for i in kume),
                            key=lambda b: (-(b["isaret"] or 0), -(b["son_yil"] or 0),
                                           -(b["chunk_n"] or 0)))
            en_iyi = uyeler[0]
            # Şüphe testleri — kararı otomatik vermemek için.
            supheler = []
            if len(uyeler) > 1:
                ikinci = uyeler[1]
                if en_iyi["isaret"] == ikinci["isaret"] and \
                        en_iyi["son_yil"] == ikinci["son_yil"]:
                    supheler.append("güncellik işaretçileri EŞİT — ayırt edemiyor")
                if (ikinci["chunk_n"] or 0) > (en_iyi["chunk_n"] or 0) * 1.2:
                    supheler.append("elenecek aday daha KAPSAMLI (chunk sayısı "
                                    "%20+ fazla) — farklı belge olabilir")
            en_dusuk_oran = min((r for (a, b), (_, r) in oranlar.items()
                                 if a in kume and b in kume), default=1.0)
            if en_dusuk_oran < args.esik + 0.10:
                supheler.append(f"içerilme eşiğe yakın (%{en_dusuk_oran * 100:.0f})")
            for kucuk, buyuk, ileri, geri in asimetrik:
                if kucuk in kume and buyuk in kume:
                    supheler.append(
                        f"ASIMETRIK: '{bilgi[kucuk]['file_name'][:30]}' "
                        f"'{bilgi[buyuk]['file_name'][:30]}' icinde "
                        f"(%{ileri * 100:.0f}) ama tersi %{geri * 100:.0f} — "
                        f"mukerrer BASKI degil KAPSAMA; buyugu SILME")
            if all((u["isaret"] or 0) == 0 for u in uyeler):
                supheler.append("HİÇBİR üyede değişiklik işaretçisi yok — "
                                "güncellik yalnız 'son yıl'a dayanıyor, zayıf")
            rapor.append({"uyeler": uyeler, "tut": en_iyi["file_name"],
                          "at": [u["file_name"] for u in uyeler[1:]],
                          "en_dusuk_ortusme": round(en_dusuk_oran, 3),
                          "supheler": supheler})

        rapor.sort(key=lambda k: -len(k["uyeler"]))

        if args.json:
            print(json.dumps({"esik": args.esik, "kume_sayisi": len(rapor),
                              "kumeler": rapor, "kapsam": kapsam},
                             ensure_ascii=False, indent=2))
            return 0

        if kapsam is not None:
            print("=" * 78)
            print(f"KAPSAM TESTI — tutulacak: {kapsam['tut']}")
            print(f"pencere={PENCERE}ch x{KAPSAM_ORNEK}, "
                  f"'kapsanan' = elenecek dosyanin metninin tutulanda bulunma orani")
            print("=" * 78)
            print(f"{'elenecek dosya':<46}{'kapsanan':>10}{'ters':>8}")
            for s in kapsam["satirlar"]:
                print(f"{s['ad'][:44]:<46}"
                      f"{s['bulunan']}/{s['toplam']} %{s['oran'] * 100:.0f}".rjust(10)
                      + f"%{s['geri_oran'] * 100:.0f}".rjust(8))
            for s in kapsam["satirlar"]:
                if not s["kayip"]:
                    continue
                print(f"\n  -- {s['ad']} : tutulanda BULUNAMAYAN ornekler --")
                for p in s["kayip"]:
                    print(f"     {p[:150]}")
            if kapsam["golden"]:
                eksik = [g for g in kapsam["golden"] if not g["tutta_var"]]
                print(f"\nGOLDEN COZUNURLUGU (kumeye bagli {len(kapsam['golden'])} "
                      f"alinti): tutulanda cozulmeyen {len(eksik)}")
                for g in eksik:
                    print(f"  [{g['id']}] {g['alinti'][:110]}")
                    coz = ", ".join(g["cozen"]) if g["cozen"] else \
                        "HICBIR KUME UYESI — alinti kume disinda cozulmeli"
                    print(f"        cozen: {coz}")
            print()

        print(f"doc_scope={args.doc_scope}  icerilme_esigi=%{args.esik * 100:.0f}  "
              f"pencere={PENCERE}ch x{ORNEK_SAYISI}")
        print(f"aday cift: {len(aday)}  (hash + golden alinti ortakligi)  ->  "
              f"esigi gecen: {len(secili)}")
        print(f"mukerrer kume: {len(rapor)}   "
              f"toplam dosya: {sum(len(k['uyeler']) for k in rapor)}\n")

        temiz = [k for k in rapor if not k["supheler"]]
        supheli = [k for k in rapor if k["supheler"]]

        def _yaz(kume: dict, n: int) -> None:
            print(f"\n--- kume {n}  ({len(kume['uyeler'])} dosya, "
                  f"en dusuk icerilme %{kume['en_dusuk_ortusme'] * 100:.0f}) ---")
            print(f"{'':2}{'dosya':<46}{'chunk':>6}{'sayfa':>6}{'skor':>7}"
                  f"{'isaret':>7}{'son_yil':>8}  golden")
            for i, u in enumerate(kume["uyeler"]):
                im = "TUT " if i == 0 else "  at"
                g = ",".join(u["golden"]) if u["golden"] else "-"
                skor = f"{u['skor']:.1f}" if u["skor"] is not None else "-"
                print(f"{im}{u['file_name'][:44]:<46}{u['chunk_n']:>6}"
                      f"{u['sayfa_n']:>6}{skor:>7}{u['isaret']:>7}"
                      f"{u['son_yil'] or '-':>8}  {g}")
            for s in kume["supheler"]:
                print(f"  ?? {s}")

        print("=" * 78)
        print(f"TEMIZ KUMELER ({len(temiz)}) — karar icin veri yeterli")
        print("=" * 78)
        for i, k in enumerate(temiz, 1):
            _yaz(k, i)

        if supheli:
            print("\n" + "=" * 78)
            print(f"SUPHELI KUMELER ({len(supheli)}) — KOMUT URETILMEDI, once bakilmali")
            print("=" * 78)
            for i, k in enumerate(supheli, 1):
                _yaz(k, i)

        etkilenen = sorted({ad for k in temiz for ad in k["at"]
                            if golden_dosyalari.get(ad)})
        print("\n" + "=" * 78)
        print("ONERILEN KOMUTLAR — KOSULMADI, her satir insan onayindan gecer")
        print("=" * 78)
        if not temiz:
            print("(temiz kume yok)")
        for k in temiz:
            for ad in k["at"]:
                print(f"python scripts/dosya_kaldir.py \"{ad}\" --sil")

        if etkilenen:
            print("\nGOLDEN ETKISI: asagidaki dosyalar v1 evidence'inda geciyor.")
            print("Silinirlerse set YENIDEN URETILMELI:")
            for ad in etkilenen:
                print(f"  {ad}  ->  {','.join(golden_dosyalari[ad])}")
            dis = " ".join(f'--dislanan "{ad}"' for ad in etkilenen)
            print(f"\npython scripts/golden_v1_jsonl_uret.py --doc-scope "
                  f"{args.doc_scope} --dislanan mevzuat_1340.pdf {dis}")
            print("ARDINDAN kuru kosum (esleme orani DUSMELI ama unmapped 0 olmali)"
                  " ve `eval load ... --version v1-bddk` tekrar.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

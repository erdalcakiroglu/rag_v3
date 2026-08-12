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

ÖLÇTÜĞÜ ŞEYLER
    küme     chunk metni hash'i üzerinden dosya-dosya örtüşme (Jaccard değil,
             KÜÇÜK dosyaya oranlanır: 40 sayfalık bir özet 700 sayfalık kanunun
             içinde erirse örtüşme küçük tarafta %100'dür, kümeye girmelidir)
    güncellik  "(Değişik:", "(Ek:", "(Mülga:" işaretçi sayısı + metinde geçen
             EN GEÇ yıl. Konsolide mevzuat metinlerinde bu işaretçiler
             değişiklikleri taşır; 2005 orijinali ile güncel metni ayırır.
    kapsam   chunk sayısı, sayfa sayısı, quality_score
    golden   dosya v1 evidence'ında geçiyor mu (geçiyorsa elenmesi seti
             değiştirir → üreticiye `--dislanan` eklenmeli)

KARARI KİM VERİR
    Bu probe ADAY gösterir, KARAR VERMEZ. Çıktının sonundaki `dosya_kaldir.py`
    komutları hazır ama koşulmaz — her satır insan onayından geçer.
    Şüpheli küme (güncellik işaretçileri çelişkili / örtüşme eşiğe yakın)
    AYRI başlık altında listelenir ve komutu ÜRETİLMEZ.

KULLANIM
    python scripts/mukerrer_baski_probe.py
    python scripts/mukerrer_baski_probe.py --esik 0.7 --golden eval/golden/v1.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

VARSAYILAN_ESIK = 0.60
VARSAYILAN_MIN_UZUNLUK = 200

# Konsolide mevzuat metninde değişiklik işaretçileri.
_ISARET = re.compile(r"\(\s*(?:De[ğg]i[şs]ik|Ek|M[üu]lga)\s*[:.]", re.IGNORECASE)
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
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database

    golden_dosyalari: dict[str, list[str]] = {}
    if args.golden.exists():
        for satir in args.golden.read_text(encoding="utf-8").splitlines():
            if not satir.strip():
                continue
            kayit = json.loads(satir)
            for ev in kayit.get("gold_evidence", []):
                golden_dosyalari.setdefault(ev["file_name"], []).append(kayit["id"])

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

            secili = []
            for f1, f2, ortak, n1, n2 in ciftler:
                kucuk = min(n1, n2)
                oran = ortak / kucuk if kucuk else 0.0
                if oran >= args.esik:
                    secili.append((f1, f2, ortak, oran))

            if not secili:
                print(f"eşik %{args.esik * 100:.0f} üzerinde mükerrer çift YOK.")
                return 0

            kumeler = _kumeler([(a, b) for a, b, _, _ in secili])
            oranlar = {(a, b): (o, r) for a, b, o, r in secili}

            tum_id = sorted({i for k in kumeler for i in k})
            bilgi_rows = conn.execute(
                """
                SELECT f.file_id, f.file_name,
                       count(c.chunk_id) AS chunk_n,
                       count(DISTINCT c.page_number) AS sayfa_n,
                       max(m.quality_score) AS skor,
                       string_agg(c.chunk_text, ' ') AS govde
                FROM core_files f
                LEFT JOIN core_chunks c USING (file_id)
                LEFT JOIN metrics_ingestion m USING (file_id)
                WHERE f.file_id = ANY(%s)
                GROUP BY f.file_id, f.file_name;
                """,
                (tum_id,),
            ).fetchall()

        bilgi = {}
        for fid, ad, chunk_n, sayfa_n, skor, govde in bilgi_rows:
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
                supheler.append(f"örtüşme eşiğe yakın (%{en_dusuk_oran * 100:.0f})")
            rapor.append({"uyeler": uyeler, "tut": en_iyi["file_name"],
                          "at": [u["file_name"] for u in uyeler[1:]],
                          "en_dusuk_ortusme": round(en_dusuk_oran, 3),
                          "supheler": supheler})

        rapor.sort(key=lambda k: -len(k["uyeler"]))

        if args.json:
            print(json.dumps({"esik": args.esik, "kume_sayisi": len(rapor),
                              "kumeler": rapor}, ensure_ascii=False, indent=2))
            return 0

        print(f"doc_scope={args.doc_scope}  esik=%{args.esik * 100:.0f}  "
              f"min_chunk_uzunluk={args.min_uzunluk}")
        print(f"mukerrer kume: {len(rapor)}   "
              f"toplam dosya: {sum(len(k['uyeler']) for k in rapor)}\n")

        temiz = [k for k in rapor if not k["supheler"]]
        supheli = [k for k in rapor if k["supheler"]]

        def _yaz(kume: dict, n: int) -> None:
            print(f"\n--- kume {n}  ({len(kume['uyeler'])} dosya, "
                  f"en dusuk ortusme %{kume['en_dusuk_ortusme'] * 100:.0f}) ---")
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

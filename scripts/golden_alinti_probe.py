#!/usr/bin/env python
"""Adım-6 · `gold_evidence` üreteci ve doğrulayıcı — SALT-OKUMA (yalnız SELECT).

NEDEN: Golden setin alıntıları BİLGİDEN yazılamaz, korpustan KESİLİR. Eski setin
retrieval metrikleri 0.000 okuyordu; kök korpus değil, quote eşlemesinin 0/43
tutmasıydı. Ayrıca `gates.evidence_precondition` bir TEK eşleşmeyen alıntıda
çıkış 2 verir ve gate hiç koşmaz -- yani her (file_name, page, quote) üçlüsü
tahminle değil makineyle türetilmelidir.

İKİ MOD:

  --dosya X [--sayfa N]   HAM MADDE: dosyanın chunk'larını basar. Alıntı bu
                          metinden KESİLİR. Yazılan cümle -- ne kadar doğru
                          olursa olsun -- alt dize eşlemesinden geçmez.

  --alinti "..."          ÇÖZÜMLEME: verilen alıntının korpusta HANGİ dosya ve
  --alinti-dosya f.txt    sayfalarda geçtiğini bulur ve doğrudan yapıştırılabilir
                          `gold_evidence` JSON'u basar. Üretimle BİREBİR aynı
                          mantık: normalize_for_quote(quote) ⊂ chunk_text_norm,
                          `status='COMPLETED'` ve doc_scope süzgeçleriyle
                          (`eval/repository.list_candidate_chunks`).

MÜKERRER BASKI: 5411 sayılı Kanun korpusta ALTI ayrı baskıyla duruyor (ölçüldü
2026-08-10). Bir alıntı hepsinde geçer. `map_gold_chunks` gold chunk kümesini
tüm evidence girdilerinin BİRLEŞİMİ olarak kurduğu için doğru çözüm dosyaları
elemek DEĞİL, hepsini evidence olarak yazmaktır -- retriever hangi baskıyı
döndürürse döndürsün isabet sayılır. Tek dosyaya çıpalamak recall'ü sahte
olarak çökertirdi. Bu yüzden çözümleme modu TÜM isabetleri basar; kırpmaz.

ÖLÇÜM-ZEMİNİ / GÜVENLİK:
  • Yalnız SELECT. DB/prod/config'e YAZMAZ, golden'a DOKUNMAZ; JSON'u stdout'a
    basar, yükleme ayrı ve bilinçli bir adımdır (`eval load`).
  • "Eşleşme yok" tek başına okunamaz: alıntı normalize edildikten SONRA da
    basılır, böylece kusurun alıntıda mı normalizasyonda mı olduğu ayrışır.
  • COMPLETED olmayan dosyada geçen alıntı AYRICA uyarıyla işaretlenir --
    gold eşlemesi o dosyayı göremez, sessizce düşerdi.

Bare-metal (H200):
  cd /opt/ragintel && python scripts/golden_alinti_probe.py --dosya mevzuat_0943.pdf
  cd /opt/ragintel && python scripts/golden_alinti_probe.py --alinti "beklenen kredi zarari"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _clip(text: str | None, n: int) -> str:
    if not text:
        return ""
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


# --- SALT-OKUMA sorgular -----------------------------------------------------

_SQL_DOSYA = """
SELECT f.file_name, f.doc_scope, f.status,
       c.chunk_index, c.page_number, c.token_count, c.section_title, c.chunk_text
FROM core_chunks c
JOIN core_files f USING (file_id)
WHERE f.file_name = %(ad)s
  AND (%(sayfa)s::int IS NULL OR c.page_number = %(sayfa)s::int)
ORDER BY c.chunk_index
LIMIT %(limit)s;
"""

# Ad deseniyle dosya arama (tam ad bilinmiyorsa).
_SQL_DOSYA_ARA = """
SELECT f.file_name, f.doc_scope, f.status, count(c.chunk_id) AS chunk
FROM core_files f
LEFT JOIN core_chunks c USING (file_id)
WHERE f.file_name ILIKE %(desen)s
GROUP BY f.file_name, f.doc_scope, f.status
ORDER BY f.file_name
LIMIT 50;
"""

# Çözümleme: alıntının geçtiği HER chunk. Kırpma yok -- mükerrer baskıların
# tamamı görünmeli, yoksa gold kümesi eksik kurulur.
_SQL_COZUM = """
SELECT f.file_name, f.doc_scope, f.status,
       c.page_number, c.chunk_index, c.token_count
FROM core_chunks c
JOIN core_files f USING (file_id)
WHERE c.chunk_text_norm LIKE %(kalip)s
ORDER BY f.file_name, c.chunk_index;
"""


def _alintilari_topla(args) -> list[str]:
    alintilar = list(args.alinti or [])
    if args.alinti_dosya:
        ham = Path(args.alinti_dosya).read_text(encoding="utf-8")
        alintilar += [s.strip() for s in ham.splitlines() if s.strip()]
    return alintilar


def _cozumle(conn, alintilar: list[str]) -> list[dict]:
    from ragintel.text import normalize_for_quote

    sonuc = []
    for ham in alintilar:
        norm = normalize_for_quote(ham)
        satirlar = conn.execute(_SQL_COZUM, {"kalip": "%" + norm + "%"}).fetchall()
        isabetler = [
            {"file_name": fn, "doc_scope": ds, "status": st,
             "page": pg, "chunk_index": ix, "token_count": tk}
            for fn, ds, st, pg, ix, tk in satirlar
        ]
        sonuc.append({"alinti": ham, "normalize": norm, "isabetler": isabetler})
    return sonuc


def _evidence_json(cozum: list[dict]) -> list[dict]:
    """Doğrudan `gold_evidence`'a yapıştırılabilir liste.

    Yalnız COMPLETED ve page'i olan isabetler yazılır: gold eşlemesi
    (`list_candidate_chunks`) diğerlerini zaten göremez, yazmak sahte
    genişlik olurdu. Aynı (dosya, sayfa) ikilisi bir kez geçer.
    """
    cikti = []
    for c in cozum:
        gorulen = set()
        for i in c["isabetler"]:
            if i["status"] != "COMPLETED" or i["page"] is None:
                continue
            anahtar = (i["file_name"], i["page"])
            if anahtar in gorulen:
                continue
            gorulen.add(anahtar)
            cikti.append({"file_name": i["file_name"], "page": i["page"],
                          "quote": c["alinti"]})
    return cikti


def _print_dosya(satirlar: list, *, metin_uzunluk: int) -> None:
    if not satirlar:
        print("  (bu ad/sayfa icin chunk yok -- --ara ile adi dogrulayin)")
        return
    fn, ds, st = satirlar[0][0], satirlar[0][1], satirlar[0][2]
    print(f"  dosya     : {fn}")
    print(f"  doc_scope : {ds}    status : {st}"
          + ("" if st == "COMPLETED" else "   <-- gold eslemesi bu dosyayi GOREMEZ"))
    print(f"  chunk     : {len(satirlar)} (bu listede)")
    print()
    for _, _, _, ix, pg, tk, sec, txt in satirlar:
        print(f"  --- chunk#{ix}  s.{pg if pg is not None else '-'}  {tk} tok"
              + ("" if pg is not None else "   <-- SAYFASIZ: gold_evidence'a yazilamaz"))
        if sec:
            print(f"      bolum: {_clip(sec, 100)}")
        print(f"      {_clip(txt, metin_uzunluk)}")
    print()


def _print_cozum(cozum: list[dict]) -> None:
    for c in cozum:
        print("-" * 100)
        print(f"  ALINTI     : {_clip(c['alinti'], 160)}")
        print(f"  normalize  : {_clip(c['normalize'], 160)}")
        isabetler = c["isabetler"]
        if not isabetler:
            print("  ISABET     : 0   <-- BU ALINTI KULLANILAMAZ.")
            print("               Kok iki turlu olabilir: (a) alinti korpusta yok --")
            print("               bilgiden yazilmis; (b) desen normalize edilince degisti.")
            print("               Yukaridaki 'normalize' satiri ikisini ayirir; metni")
            print("               --dosya modundan KESEREK alin.")
            continue
        tamam = [i for i in isabetler if i["status"] == "COMPLETED"]
        sayfasiz = [i for i in tamam if i["page"] is None]
        dosyalar = sorted({i["file_name"] for i in isabetler})
        print(f"  ISABET     : {len(isabetler)} chunk / {len(dosyalar)} dosya"
              f"   (COMPLETED: {len(tamam)})")
        if len(tamam) < len(isabetler):
            print(f"  UYARI      : {len(isabetler) - len(tamam)} isabet COMPLETED OLMAYAN")
            print("               dosyada -- gold eslemesi bunlari sessizce dusurur.")
        if sayfasiz:
            print(f"  UYARI      : {len(sayfasiz)} isabetin page_number'i YOK --")
            print("               gold_evidence page ya da sheet ister, bunlar yazilamaz.")
        if len(dosyalar) > 1:
            print(f"  MUKERRER   : alinti {len(dosyalar)} ayri dosyada geciyor. Tek dosyaya")
            print("               capalamak recall'u SAHTE olarak cokertir; asagidaki JSON")
            print("               hepsini evidence olarak yazar (birlesim gold kumesi).")
        for i in isabetler:
            bayrak = "" if i["status"] == "COMPLETED" else f"  [{i['status']}]"
            print(f"      {_clip(i['file_name'], 58):<58} s.{str(i['page'] or '-'):>4} "
                  f"chunk#{i['chunk_index']:<5} {i['token_count']:>5} tok{bayrak}")
    print()


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dosya", help="HAM MADDE modu: bu dosyanin chunk'larini bas")
    ap.add_argument("--sayfa", type=int, default=None, help="--dosya icin sayfa suzgeci")
    ap.add_argument("--ara", help="Ad deseniyle dosya ara (ILIKE), ornek: %%Kanun%%")
    ap.add_argument("--alinti", action="append", default=None,
                    help="COZUMLEME modu: alinti (tekrarlanabilir)")
    ap.add_argument("--alinti-dosya", help="Her satiri bir alinti olan dosya")
    ap.add_argument("--limit", type=int, default=40, help="--dosya modunda chunk siniri")
    ap.add_argument("--metin-uzunluk", type=int, default=900,
                    help="--dosya modunda chunk basina basilacak karakter")
    ap.add_argument("--json", action="store_true", help="Ham JSON bas")
    args = ap.parse_args(argv)

    if not (args.dosya or args.ara or args.alinti or args.alinti_dosya):
        ap.error("En az bir mod secin: --dosya / --ara / --alinti / --alinti-dosya")

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database

    db = Database(DbSettings()).open()
    try:
        with db.connection() as conn:
            arama = satirlar = None
            cozum: list[dict] = []
            if args.ara:
                arama = conn.execute(_SQL_DOSYA_ARA, {"desen": args.ara}).fetchall()
            if args.dosya:
                satirlar = conn.execute(
                    _SQL_DOSYA,
                    {"ad": args.dosya, "sayfa": args.sayfa, "limit": args.limit},
                ).fetchall()
            alintilar = _alintilari_topla(args)
            if alintilar:
                cozum = _cozumle(conn, alintilar)
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()

    evidence = _evidence_json(cozum) if cozum else []

    if args.json:
        print(json.dumps({"cozum": cozum, "gold_evidence": evidence},
                         ensure_ascii=False, indent=2))
        return 0

    if arama is not None:
        print("BOLUM 0  DOSYA ARAMA")
        print("=" * 100)
        if not arama:
            print("  (eslesme yok)")
        for fn, ds, st, ch in arama:
            print(f"  {_clip(fn, 64):<64} {ds:<14} {st:<10} chunk={ch:>5,}")
        print()

    if satirlar is not None:
        print("BOLUM 1  HAM MADDE -- alinti bu metinden KESILIR")
        print("=" * 100)
        _print_dosya(satirlar, metin_uzunluk=args.metin_uzunluk)

    if cozum:
        print("BOLUM 2  COZUMLEME -- alinti korpusta nerede geciyor?")
        print("=" * 100)
        _print_cozum(cozum)
        print("BOLUM 3  gold_evidence (dogrudan yapistirilabilir)")
        print("=" * 100)
        if not evidence:
            print("  (yazilabilir isabet yok -- Bolum 2'deki uyarilara bakin)")
        else:
            print(json.dumps(evidence, ensure_ascii=False, indent=2))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

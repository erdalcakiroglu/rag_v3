#!/usr/bin/env python
"""Golden v1 (BDDK) JSONL üreticisi — `docs/Golden_v1_BDDK_Ankrali.md` → `eval/golden/v1.jsonl`.

NEDEN ÜRETİCİ, NEDEN ELLE DEĞİL
    Set 30 soru ama **77 gold_evidence girdisi** taşıyor: 5411 korpusta 6,
    6361 2 baskıda duruyor ve mükerrer-baskı kuralı gereği alıntı TÜM
    baskılara evidence olarak yazılıyor (tek dosyaya çıpalamak recall'ü
    sahte olarak çökertir — bkz. `golden_alinti_probe` docstring).
    77 satırı elle kopyalamak transkripsiyon hatası davetidir; dahası
    havuz iki bilinen sebeple değişecek:
      - `mevzuat_1340` kararı (M10'un atıf alıntısı oraya da düşüyor),
      - `5411_Guncel_2.pdf` korpus kusuru kalemi (mülga m.33 metni).
    Her ikisi de `--dislanan` ile tek komutta yeniden üretilir.

VERİ AKIŞI (üçü de makineden geldi, hiçbiri bilgiden yazılmadı)
    docs/golden_v1_alintilar.txt   31 alıntı, korpus metninden kesildi
    docs/golden_v1_evidence.json   `golden_alinti_probe --alinti-dosya` çıktısı
                                   (dosya+sayfa çözümlemesi; 77 girdi)
    docs/Golden_v1_BDDK_Ankrali.md soru + ideal_answer (insan tarafı)
    docs/golden_v1_unanswerable_adaylar.json
                                   unanswerable kolu; YALNIZ `karar=onaylandi`
                                   olanlar yazılır. Onay, `unanswerable_aday_probe`
                                   çıktısındaki madde GÖVDELERİ okunarak verilir —
                                   dosya adı deseniyle verilen yokluk hükmü daha
                                   önce çürüdü (mevzuat_1340).

    Bu script hiçbir metni KENDİ yazmaz; yalnız birleştirir. Soru/cevap
    düzeltmesi md'ye, alıntı düzeltmesi probe'a gider — tek kaynak korunur.

DB'YE DOKUNMAZ. Bağlantı açmaz, sorgu atmaz; saf dosya dönüşümüdür.
Yazdığı tek yer `--cikti` (vars. eval/golden/v1.jsonl). `eval load` DEĞİLDİR —
yükleme ayrı ve bilinçli bir adımdır.

DOC_SCOPE UYARISI
    `map_gold_chunks` adayları `f.doc_scope = rec.doc_scope` ile süzer
    (ragintel/eval/repository.py). Scope yanlışsa aday kümesi BOŞ döner ve
    77 evidence'ın 77'si unmapped olur — eski golden'ın 0/43 arızasının
    birebir aynısı, üstelik sessizce. Bu yüzden scope varsayılana bırakılmaz;
    `golden_aday_tarama_probe` dağılımından teyit edilip verilir.

KULLANIM
    python scripts/golden_v1_jsonl_uret.py --doc-scope default
    python scripts/golden_v1_jsonl_uret.py --doc-scope default \
        --dislanan mevzuat_1340.pdf
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent

MD = KOK / "docs" / "Golden_v1_BDDK_Ankrali.md"
ALINTILAR = KOK / "docs" / "golden_v1_alintilar.txt"
EVIDENCE = KOK / "docs" / "golden_v1_evidence.json"
UNANS = KOK / "docs" / "golden_v1_unanswerable_adaylar.json"
CIKTI = KOK / "eval" / "golden" / "v1.jsonl"

# Soru -> alıntı sıra no (docs/golden_v1_alintilar.txt, 1 tabanlı).
# Zor katman iki çıpa taşır; çok-dokümanlılık sorunun parçalarından değil
# cevabın DAYANAĞINDAN gelir (iki parçalı soru yazılmadı — bkz. md §4).
CIPALAR: dict[str, tuple[int, ...]] = {
    "E01": (1,),   "E02": (2,),   "E03": (3,),   "E04": (4,),   "E05": (5,),
    "E06": (6,),   "E07": (7,),   "E08": (8,),   "E09": (9,),   "E10": (10,),
    "M01": (11,),  "M02": (12,),  "M03": (13,),  "M04": (14,),  "M05": (15,),
    "M06": (16,),  "M07": (17,),  "M08": (18,),  "M09": (19,),  "M10": (20,),
    "H01": (21, 22), "H02": (5, 6),   "H03": (23, 11), "H04": (19, 14),
    "H05": (18, 24), "H06": (16, 24), "H07": (25, 26), "H08": (27, 28),
    "H09": (29, 30), "H10": (15, 31),
}

# Kategori seçimi kanıt yapısına göre; iki kanıt birleştiriliyorsa synthesis
# (eval/golden/v1.sample.jsonl g2 emsali: aynı dokümanda iki kanıt = synthesis).
# M10 citation_sensitive: cevabın kendisi bir ATIF ("...Yönetmeliğin 3 üncü
# maddesinin (o) bendi"), anılan yönetmelik metni korpusta yok.
KATEGORI: dict[str, str] = {**{f"E{i:02d}": "single_fact" for i in range(1, 11)},
                            **{f"M{i:02d}": "single_fact" for i in range(1, 11)},
                            **{f"H{i:02d}": "synthesis" for i in range(1, 11)}}
KATEGORI["M10"] = "citation_sensitive"

ZORLUK = {"E": 1, "M": 2, "H": 3}

_SATIR = re.compile(r"^\|\s*([EMH]\d{2})\s*\|(.+)$")


def _md_sorulari(yol: Path) -> dict[str, dict[str, str]]:
    """md tablolarından id/question/ideal_answer çeker (4. sütun prozadır, alınmaz)."""
    kayitlar: dict[str, dict[str, str]] = {}
    for ham in yol.read_text(encoding="utf-8").splitlines():
        m = _SATIR.match(ham.strip())
        if not m:
            continue
        kimlik = m.group(1)
        hucreler = [h.strip() for h in m.group(2).split("|")]
        if len(hucreler) < 3:
            raise SystemExit(f"{kimlik}: tablo satırı eksik sütunlu ({len(hucreler)})")
        soru, cevap = hucreler[0], hucreler[1]
        if not soru or not cevap:
            raise SystemExit(f"{kimlik}: soru veya golden answer boş")
        if kimlik in kayitlar:
            raise SystemExit(f"{kimlik}: md'de iki kez geçiyor")
        kayitlar[kimlik] = {"question": soru, "ideal_answer": cevap}
    return kayitlar


def _vurgu_sil(metin: str) -> str:
    """md vurgusunu ayıklar; ideal_answer düz metin olmalı."""
    metin = re.sub(r"\*\*(.+?)\*\*", r"\1", metin)
    metin = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", metin)
    return re.sub(r"`(.+?)`", r"\1", metin).strip()


def _unanswerable(yol: Path, doc_scope: str, created_by: str) -> list[dict]:
    """Onaylanmış unanswerable adaylarını kayda çevirir.

    KAPI: yalnız `karar == "onaylandi"` geçer. 'beklemede' bir aday, yokluğu
    HENÜZ gövdeden kanıtlanmamış demektir; sete girerse dürüstlük kapısı
    aslında cevaplanabilir bir soruyu "reddetmeliydi" diye puanlar — kapı
    modeli ölçmek yerine kendi hatasını ölçer. 'kontrol' kayıtları da girmez;
    onlar probe'un tarama yeteneğini sınamak içindir, ölçüm nesnesi değil.

    `gold_evidence` BOŞ kalır: modeller.py `answerable=false` kaydın kanıt
    taşımasını reddeder (taşısaydı zaten cevaplanabilir olurdu).
    """
    if not yol.exists():
        return []
    veri = json.loads(yol.read_text(encoding="utf-8"))
    kayitlar = []
    for aday in veri["adaylar"]:
        if aday.get("karar") != "onaylandi":
            continue
        if not aday.get("ideal_answer"):
            raise SystemExit(f"{aday['id']}: onaylandi ama ideal_answer bos")
        kayitlar.append({
            "id": f"gs-bddk-{aday['id'].lower()}",
            "question": aday["soru"],
            "ideal_answer": aday["ideal_answer"],
            "category": "unanswerable",
            "difficulty": 3,
            "gold_evidence": [],
            "doc_scope": doc_scope,
            "answerable": False,
            "created_by": created_by,
            "notes": f"{aday['id']}; yokluk unanswerable_aday_probe ile GOVDEDEN "
                     f"dogrulandi (ad deseniyle degil)",
        })
    return kayitlar


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doc-scope", required=True,
                    help="core_files.doc_scope; map_gold_chunks bununla süzer "
                         "(golden_aday_tarama_probe dağılımından teyit edin)")
    ap.add_argument("--dislanan", action="append", default=[], metavar="DOSYA",
                    help="evidence'tan çıkarılacak file_name (yinelenebilir)")
    ap.add_argument("--created-by", default="erdal")
    ap.add_argument("--cikti", type=Path, default=CIKTI)
    args = ap.parse_args()

    alintilar = [s for s in ALINTILAR.read_text(encoding="utf-8").splitlines() if s.strip()]
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    sorular = _md_sorulari(MD)

    eksik = set(CIPALAR) - set(sorular)
    if eksik:
        raise SystemExit(f"md'de bulunmayan soru kimliği: {sorted(eksik)}")
    fazla = set(sorular) - set(CIPALAR)
    if fazla:
        raise SystemExit(f"md'de olup çıpası olmayan soru: {sorted(fazla)}")

    # alıntı -> evidence girdileri (probe sırası korunur)
    alinti_evidence: dict[str, list[dict]] = {}
    for e in evidence:
        alinti_evidence.setdefault(e["quote"], []).append(e)

    kullanilmayan = set(range(1, len(alintilar) + 1)) - {i for t in CIPALAR.values() for i in t}
    if kullanilmayan:
        raise SystemExit(f"hiçbir soruya bağlanmayan alıntı: {sorted(kullanilmayan)}")

    dislanan = set(args.dislanan)
    dusen = 0
    kayitlar = []
    for kimlik in sorted(CIPALAR, key=lambda k: (k[0] not in "EMH", "EMH".index(k[0]), k)):
        kanit: list[dict] = []
        for sira in CIPALAR[kimlik]:
            if not 1 <= sira <= len(alintilar):
                raise SystemExit(f"{kimlik}: alıntı sırası {sira} dosyada yok")
            alinti = alintilar[sira - 1]
            girdiler = alinti_evidence.get(alinti)
            if not girdiler:
                raise SystemExit(
                    f"{kimlik}: alıntı #{sira} evidence JSON'da yok — "
                    "golden_alinti_probe yeniden koşturulmalı")
            for g in girdiler:
                if g["file_name"] in dislanan:
                    dusen += 1
                    continue
                kanit.append({"file_name": g["file_name"], "page": g["page"],
                              "quote": g["quote"]})
        if not kanit:
            raise SystemExit(f"{kimlik}: dışlamalardan sonra kanıt kalmadı")
        kayitlar.append({
            "id": f"gs-bddk-{kimlik.lower()}",
            "question": _vurgu_sil(sorular[kimlik]["question"]),
            "ideal_answer": _vurgu_sil(sorular[kimlik]["ideal_answer"]),
            "category": KATEGORI[kimlik],
            "difficulty": ZORLUK[kimlik[0]],
            "gold_evidence": kanit,
            "doc_scope": args.doc_scope,
            "answerable": True,
            "created_by": args.created_by,
            "notes": f"{kimlik}; alinti korpustan kesildi, golden_alinti_probe ile "
                     f"cozumlendi; {len(kanit)} evidence "
                     f"({len({k['file_name'] for k in kanit})} dosya)",
        })

    kayitlar.extend(_unanswerable(UNANS, args.doc_scope, args.created_by))

    args.cikti.parent.mkdir(parents=True, exist_ok=True)
    with args.cikti.open("w", encoding="utf-8", newline="\n") as fh:
        for k in kayitlar:
            fh.write(json.dumps(k, ensure_ascii=False) + "\n")

    toplam = sum(len(k["gold_evidence"]) for k in kayitlar)
    dosyalar = {e["file_name"] for k in kayitlar for e in k["gold_evidence"]}
    n_unans = sum(1 for k in kayitlar if not k["answerable"])
    print(f"yazildi     : {args.cikti}")
    print(f"kayit       : {len(kayitlar)}   (answerable {len(kayitlar) - n_unans} / "
          f"unanswerable {n_unans})")
    if n_unans == 0:
        print("UYARI      : unanswerable kol BOS — M-17 durustluk kapisi bu sette KOSAMAZ "
              "(onaylanmis aday yok; unanswerable_aday_probe cikti bekliyor)")
    print(f"evidence    : {toplam}   ayri dosya: {len(dosyalar)}")
    print(f"doc_scope   : {args.doc_scope}")
    if dislanan:
        print(f"dislanan    : {sorted(dislanan)}  ({dusen} evidence girdisi dustu)")

    # Şema + benzersizlik: yükleyicinin kendi doğrulayıcısı, DB'siz.
    try:
        sys.path.insert(0, str(KOK))
        from ragintel.eval.models import load_golden_jsonl
    except ImportError as exc:
        print(f"UYARI: sema dogrulamasi atlandi ({exc})")
        return 0
    kayit_nesneleri = load_golden_jsonl(args.cikti)
    print(f"sema        : GECTI ({len(kayit_nesneleri)} kayit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

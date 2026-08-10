#!/usr/bin/env python
"""Adım-6 · golden v1'in HAM KAYNAK METNİ — SALT-OKUMA (yalnız SELECT).

KARAR (2026-08-10, kullanıcı): golden yalnız korpusta BULUNAN kaynaklara
oturacak. Gerekçe ölçüldü — `golden_mevzuat_kimlik_probe` Bölüm D, 14 temel
BDDK yönetmeliğinin HİÇBİRİNİN kaynak metninin korpusta olmadığını gösterdi;
bulunan her "kapak" isabeti yönetmeliğe ATIF yapan başka bir belge türüydü
(rehber, Kurul kararı, genelge, tebliğ taslağı). Korpusun gerçek bileşimi:
kanunlar (5411/5464/6361) + Kurul kararları + BDDK rehberleri + TBB yayınları.

BU PROB NE İŞE YARAR: golden sorularının alıntıları BİLGİDEN YAZILMAZ, korpusun
metninden KESİLİR. Aksi hâlde ölçüm aracı ile ölçülen şey birbirinden kopar —
eski golden'ın başına gelen tam olarak buydu (quote eşleme 0/43, tüm metrikler
0.000; korpus kusuru değil, bayat ölçüm aracı). Prob her konu başlığı için
korpustaki gerçek chunk metnini basar; alıntı oradan kesilir ve
`golden_alinti_probe --alinti` ile doğrulanır.

ARAMA ÜRETİMLE AYNI YOLDAN GEÇER: desenler `normalize_for_quote`ten geçirilip
`chunk_text_norm` içinde aranır — yani probun bulduğu şey, benchmark'ın
`map_gold_chunks` ile bulacağı şeyle AYNI kuralla bulunur. Kendi arama
kuralını yazan bir prob, var olmayan bir eşleşmeyi vaat eder.

DIŞARIDA BIRAKILANLAR (sessizce değil, sayıyla raporlanır):
  • C0 kontrol karakteri taşıyan dosyalar (2026-08-10: 57 dosya). Glif ölçütü
    düzeltilince bu dosyalar reprocess edilecek ve chunk metinleri DEĞİŞECEK;
    bugün oradan kesilen alıntı yarın tutmaz. Bayat alıntı üretmemek için
    şimdilik havuz dışı.
  • Görüşe açılmış TASLAK metinler ("Görüşlerinizi ... iletebilirsiniz"
    boilerplate'i; ölçüldü: 11 dosya). Yürürlükte olmayan metin golden'da
    doğru cevap OLAMAZ.
Havuzun kaç dosya olduğu ve her elemenin kaç dosya götürdüğü başlıkta basılır.

ÖLÇÜM-ZEMİNİ / GÜVENLİK: yalnız SELECT. DB/prod/config'e YAZMAZ, golden
tablolarına (eval_golden_*) DOKUNMAZ.

Bare-metal (H200):
  cd /opt/ragintel && python scripts/golden_v1_kaynak_probe.py > docs/kaynak.txt 2>&1
Tek konu:
  python scripts/golden_v1_kaynak_probe.py --konu 5411-sir --metin 1200
"""

from __future__ import annotations

import argparse
import json
import sys


def _force_utf8() -> None:
    for akis in (sys.stdout, sys.stderr):
        yeniden = getattr(akis, "reconfigure", None)
        if callable(yeniden):
            try:
                yeniden(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _clip(text: str | None, n: int) -> str:
    if not text:
        return ""
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


# --- Konu haritası -----------------------------------------------------------
# Desenler TAM BAŞLIK DEĞİL, ayırt edici parçadır: chunk sınırı başlığı ikiye
# bölebilir ve OCR tek harfi kaçırabilir. `beklenen` alanı bir TAHMİNDİR ve
# hükmü belirlemez -- hüküm basılan metinden okunur.
KONULAR: list[dict] = [
    # -- 5411 sayılı Bankacılık Kanunu (korpusta 7 baskı; en temizi seçilir) --
    {"id": "5411-izin", "ad": "Kuruluş ve faaliyet izni",
     "desen": "faaliyet izni", "beklenen": "5411"},
    {"id": "5411-kurulus-sart", "ad": "Kuruluş şartları",
     "desen": "kurucularının bu kanunun", "beklenen": "5411"},
    {"id": "5411-sir", "ad": "Sırların saklanması (m.73)",
     "desen": "sırların saklanması", "beklenen": "5411"},
    {"id": "5411-musteri-sirri", "ad": "Müşteri sırrı tanımı",
     "desen": "müşteri sırrı", "beklenen": "5411"},
    {"id": "5411-kredi-sinir", "ad": "Kredi sınırları (m.54)",
     "desen": "kredi sınırları", "beklenen": "5411"},
    {"id": "5411-ozkaynak", "ad": "Özkaynak (m.44)",
     "desen": "özkaynak", "beklenen": "5411"},
    {"id": "5411-karsilik", "ad": "Karşılıklar ve teminatlar (m.53)",
     "desen": "karşılıklar ve teminatlar", "beklenen": "5411"},
    {"id": "5411-faaliyet-konu", "ad": "Faaliyet konuları (m.4)",
     "desen": "mevduat kabulü", "beklenen": "5411"},
    {"id": "5411-ic-sistem", "ad": "İç sistemler (m.29)",
     "desen": "iç kontrol sistemi", "beklenen": "5411"},
    {"id": "5411-bagimsiz-denetim", "ad": "Bağımsız denetim (m.33)",
     "desen": "bağımsız denetim kuruluşları", "beklenen": "5411"},
    {"id": "5411-tmsf", "ad": "Mevduat sigortası / TMSF (m.63)",
     "desen": "sigortaya tabi mevduat", "beklenen": "5411"},
    {"id": "5411-faaliyet-izni-iptal", "ad": "Faaliyet izninin kaldırılması (m.71)",
     "desen": "faaliyet izninin kaldırılması", "beklenen": "5411"},

    # -- 5464 Banka Kartları ve Kredi Kartları Kanunu ------------------------
    {"id": "5464-kart", "ad": "Kart çıkarma yetkisi / kart hamili",
     "desen": "kart hamili", "beklenen": "5464"},
    {"id": "5464-sozlesme", "ad": "Kart sözleşmesi şekil şartı",
     "desen": "sözleşmenin bir örneği", "beklenen": "5464"},

    # -- 6361 Finansal Kiralama, Faktoring, Finansman ------------------------
    {"id": "6361-kiralama", "ad": "Finansal kiralama sözleşmesi",
     "desen": "finansal kiralama sözleşmesi", "beklenen": "6361"},
    {"id": "6361-faktoring", "ad": "Faktoring sözleşmesi",
     "desen": "faktoring sözleşmesi", "beklenen": "6361"},
    {"id": "6361-tasarruf", "ad": "Tasarruf finansman sözleşmesi",
     "desen": "tasarruf finansman", "beklenen": "6361"},

    # -- BDDK rehberleri (normatif olmayan ama KAYNAK METNİ korpusta olan) ---
    {"id": "tfrs9-onemli-artis", "ad": "TFRS 9 · kredi riskinde önemli artış",
     "desen": "kredi riskinde önemli artış", "beklenen": "mevzuat_0943"},
    {"id": "tfrs9-ecl", "ad": "TFRS 9 · beklenen kredi zararı",
     "desen": "beklenen kredi zararı", "beklenen": "mevzuat_0943"},
    {"id": "sorunlu-yapilandirma", "ad": "Sorunlu alacak · yeniden yapılandırma",
     "desen": "yeniden yapılandırma", "beklenen": "mevzuat_1040"},
    {"id": "sorunlu-erken-uyari", "ad": "Sorunlu alacak · erken uyarı",
     "desen": "erken uyarı", "beklenen": "mevzuat_1040"},
    {"id": "likidite-lcr", "ad": "Likidite · karşılama oranı / kaçış",
     "desen": "likidite karşılama oranı", "beklenen": "mevzuat_0954"},
    {"id": "isedes", "ad": "İSEDES · içsel sermaye yeterliliği",
     "desen": "içsel sermaye yeterliliği", "beklenen": "mevzuat_1291"},
    {"id": "sistemik-onemli", "ad": "Sistemik önemli banka · rehber",
     "desen": "sistemik önemli banka", "beklenen": "mevzuat_1167"},
    {"id": "gercege-uygun", "ad": "Gerçeğe uygun değer · rehber",
     "desen": "gerçeğe uygun değer", "beklenen": "mevzuat_0945"},
    {"id": "faizsiz", "ad": "Faizsiz bankacılık ilke ve standartları",
     "desen": "faizsiz bankacılık", "beklenen": "mevzuat_1323"},

    # -- Genelgeler (yürürlükte, kaynak metni korpusta) ----------------------
    {"id": "genelge-sir", "ad": "GENELGE 2022/1 · sır paylaşımı",
     "desen": "sır niteliğindeki bilgilerin paylaşılması", "beklenen": "mevzuat_1135"},
    {"id": "genelge-bseby", "ad": "GENELGE 2023/1 · bilgi sistemleri",
     "desen": "elektronik bankacılık hizmetleri", "beklenen": "mevzuat_1171"},
]


# --- SALT-OKUMA sorgular -----------------------------------------------------

_C0 = r"chunk_text ~ E'[\\x01-\\x08\\x0B\\x0C\\x0E-\\x1F]'"

# Havuz: COMPLETED, C0 artığı olmayan, taslak boilerplate'i taşımayan dosyalar.
_SQL_HAVUZ = f"""
WITH d AS (
    SELECT file_id,
           bool_or({_C0})                                     AS c0,
           bool_or(chunk_text_norm LIKE '%%görüşlerinizi%%')   AS taslak
    FROM core_chunks GROUP BY file_id
)
SELECT count(*)                                                AS toplam,
       count(*) FILTER (WHERE d.c0)                            AS elenen_c0,
       count(*) FILTER (WHERE d.taslak AND NOT d.c0)           AS elenen_taslak,
       count(*) FILTER (WHERE f.status <> 'COMPLETED')         AS elenen_durum,
       count(*) FILTER (WHERE NOT d.c0 AND NOT d.taslak
                          AND f.status = 'COMPLETED')          AS havuz
FROM d JOIN core_files f USING (file_id);
"""

_SQL_KONU = f"""
WITH d AS (
    SELECT file_id,
           bool_or({_C0})                                     AS c0,
           bool_or(chunk_text_norm LIKE '%%görüşlerinizi%%')   AS taslak
    FROM core_chunks GROUP BY file_id
), uygun AS (
    SELECT d.file_id FROM d JOIN core_files f USING (file_id)
    WHERE NOT d.c0 AND NOT d.taslak AND f.status = 'COMPLETED'
), m AS (
    SELECT c.file_id, c.chunk_index, c.page_number, c.token_count,
           c.section_title, c.chunk_text,
           count(*)     OVER (PARTITION BY c.file_id)                          AS dosya_vurus,
           row_number() OVER (PARTITION BY c.file_id ORDER BY c.chunk_index)   AS sira
    FROM core_chunks c JOIN uygun USING (file_id)
    WHERE c.chunk_text_norm LIKE %(kalip)s
)
SELECT f.file_name, m.chunk_index, m.page_number, m.token_count,
       m.section_title, m.dosya_vurus, m.chunk_text
FROM m JOIN core_files f USING (file_id)
WHERE m.sira <= %(dosya_basi)s
ORDER BY m.dosya_vurus DESC, f.file_name, m.chunk_index
LIMIT %(limit)s;
"""

# Konu başına kaç DOSYA vuruyor? Kırpılan kısmın büyüklüğü sessiz kalmasın.
_SQL_KONU_SAYIM = f"""
WITH d AS (
    SELECT file_id,
           bool_or({_C0})                                     AS c0,
           bool_or(chunk_text_norm LIKE '%%görüşlerinizi%%')   AS taslak
    FROM core_chunks GROUP BY file_id
), uygun AS (
    SELECT d.file_id FROM d JOIN core_files f USING (file_id)
    WHERE NOT d.c0 AND NOT d.taslak AND f.status = 'COMPLETED'
)
SELECT count(DISTINCT c.file_id) AS dosya, count(*) AS chunk
FROM core_chunks c JOIN uygun USING (file_id)
WHERE c.chunk_text_norm LIKE %(kalip)s;
"""


def _tara(conn, konular: list[dict], *, dosya_basi: int, limit: int) -> list[dict]:
    from ragintel.text import normalize_for_quote

    out = []
    for k in konular:
        norm = normalize_for_quote(k["desen"])
        kalip = "%" + norm + "%"
        sayim = conn.execute(_SQL_KONU_SAYIM, {"kalip": kalip}).fetchone()
        satirlar = conn.execute(
            _SQL_KONU, {"kalip": kalip, "dosya_basi": dosya_basi, "limit": limit}
        ).fetchall()
        out.append({**k, "normalize": norm,
                    "dosya": sayim[0], "chunk": sayim[1],
                    "isabetler": [
                        {"file_name": fn, "chunk_index": ci, "page_number": pn,
                         "token_count": tc, "section_title": st,
                         "dosya_vurus": dv, "chunk_text": txt}
                        for fn, ci, pn, tc, st, dv, txt in satirlar]})
    return out


def _print_human(havuz, konular: list[dict], *, metin: int, limit: int) -> None:
    toplam, e_c0, e_taslak, e_durum, h = havuz
    print("=" * 100)
    print("GOLDEN v1 KAYNAK PROBU  (alinti BILGIDEN yazilmaz, METINDEN kesilir)")
    print("=" * 100)
    print(f"  havuz: {h} dosya / {toplam}")
    print(f"    elenen -- C0 artigi (reprocess bekliyor) : {e_c0}")
    print(f"    elenen -- gorse acilmis TASLAK           : {e_taslak}")
    print(f"    elenen -- COMPLETED degil                : {e_durum}")
    print("  arama uretimle AYNI yoldan: normalize_for_quote -> chunk_text_norm LIKE")

    for k in konular:
        print()
        print("-" * 100)
        print(f"### [{k['id']}] {k['ad']}")
        print(f"    desen  : {k['desen']}   (beklenen: {k['beklenen']})")
        if not k["isabetler"]:
            print("    HUKUM  : ISABET YOK -- bu konu golden'da SORULAMAZ.")
            print("             Desen yanlis olabilir; once desen degistirilip tekrar")
            print("             olculmeli, 'korpusta yok' hukmu ondan sonra verilmeli.")
            continue
        kirpik = k["dosya"] - len({i["file_name"] for i in k["isabetler"]})
        print(f"    havuzda: {k['dosya']} dosya / {k['chunk']} chunk"
              + (f"   (basilan {limit} satir, {kirpik} dosya kirpildi)" if kirpik > 0 else ""))
        for i in k["isabetler"]:
            print(f"      · {_clip(i['file_name'], 52):<52} chunk={i['chunk_index']:<5} "
                  f"s.{i['page_number'] or 0:<4} {i['token_count']:>4} tok  "
                  f"dosya_vurus={i['dosya_vurus']}")
            if i["section_title"]:
                print(f"        bolum: {_clip(i['section_title'], 80)}")
            print(f"        {_clip(i['chunk_text'], metin)}")


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--konu", action="append", default=None,
                    help="Yalniz bu konu id'leri (birden cok kez verilebilir)")
    ap.add_argument("--dosya-basi", type=int, default=1,
                    help="Konu basina her DOSYADAN kac chunk basilsin")
    ap.add_argument("--limit", type=int, default=3,
                    help="Konu basina toplam kac satir basilsin")
    ap.add_argument("--metin", type=int, default=500,
                    help="Her chunk'tan basilacak karakter (alinti bundan kesilir)")
    ap.add_argument("--liste", action="store_true", help="Konu id'lerini bas ve cik")
    ap.add_argument("--json", action="store_true", help="Ham JSON bas")
    args = ap.parse_args()

    if args.liste:
        for k in KONULAR:
            print(f"{k['id']:<26} {k['ad']}")
        return 0

    konular = KONULAR
    if args.konu:
        istenen = set(args.konu)
        konular = [k for k in KONULAR if k["id"] in istenen]
        bilinmeyen = istenen - {k["id"] for k in KONULAR}
        if bilinmeyen:
            print(f"bilinmeyen konu: {sorted(bilinmeyen)} (--liste ile bakin)",
                  file=sys.stderr)
            return 2

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database

    db = Database(DbSettings()).open()
    try:
        with db.connection() as conn:
            havuz = conn.execute(_SQL_HAVUZ).fetchone()
            veri = _tara(conn, konular, dosya_basi=args.dosya_basi, limit=args.limit)
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()

    if args.json:
        print(json.dumps({"havuz": list(havuz), "konular": veri},
                         ensure_ascii=False, indent=2, default=str))
    else:
        _print_human(havuz, veri, metin=args.metin, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

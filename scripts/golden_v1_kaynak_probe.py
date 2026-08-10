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
    boilerplate'i; ölçüldü: 15 dosya). Yürürlükte olmayan metin golden'da
    doğru cevap OLAMAZ.
  • Adı sayılarak elenenler (ELENEN_DOSYA): mülga 4389. Gerekçesi basılır.
Havuzun kaç dosya olduğu ve her elemenin kaç dosya götürdüğü başlıkta basılır.

KEŞİF MODU (--dosya): bir konu deseni yetkili kaynağı getirmediğinde, o kaynağın
"korpusta yok" mu yoksa "desene takılmadı" mı olduğu ancak dosyanın metnine
bakılarak ayrılır. --dosya mevzuat_1291 gibi bir çağrı dosyanın chunk'larını ve
havuz durumunu (C0/TASLAK/ADLA-ELENEN) basar.

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
     "desen": "kurucu ortaklarının", "beklenen": "5411"},
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
     "desen": "faaliyet konuları", "beklenen": "5411"},
    {"id": "5411-ic-sistem", "ad": "İç sistemler (m.29)",
     "desen": "iç sistemlere ilişkin", "beklenen": "5411"},
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


# Adı sayılarak dışlananlar. Sessiz eleme YOK -- gerekçe basılır.
ELENEN_DOSYA: dict[str, str] = {
    # 4389 sayılı Bankalar Kanunu: 5411 ile MÜLGA. Metni korpusta duruyor ve
    # "Tanımlar MADDE 2" gibi bölümleri 5411'inkine birebir benziyor; golden'da
    # yürürlükteki hüküm diye eşleşirse ölçüm aracı YANLIŞ cevabı doğru sayar.
    "mevzuat_1230": "MÜLGA 4389 sayılı Bankalar Kanunu (5411 ile yürürlükten kalktı)",
}


# --- SALT-OKUMA sorgular -----------------------------------------------------

_C0 = r"chunk_text ~ E'[\\x01-\\x08\\x0B\\x0C\\x0E-\\x1F]'"

# Havuz: COMPLETED, C0 artığı olmayan, taslak boilerplate'i taşımayan,
# adı sayılarak elenmemiş dosyalar.
_UYGUN = f"""
WITH d AS (
    SELECT file_id,
           bool_or({_C0})                                     AS c0,
           bool_or(chunk_text_norm LIKE '%%görüşlerinizi%%')   AS taslak
    FROM core_chunks GROUP BY file_id
), s AS (
    SELECT d.file_id, d.c0, d.taslak, f.status, f.file_name,
           (f.file_name ILIKE ANY(%(elenen)s))                AS adla_elenen
    FROM d JOIN core_files f USING (file_id)
), uygun AS (
    SELECT file_id FROM s
    WHERE NOT c0 AND NOT taslak AND NOT adla_elenen AND status = 'COMPLETED'
)"""

_SQL_HAVUZ = _UYGUN + """
SELECT count(*)                                                AS toplam,
       count(*) FILTER (WHERE c0)                              AS elenen_c0,
       count(*) FILTER (WHERE taslak AND NOT c0)               AS elenen_taslak,
       count(*) FILTER (WHERE adla_elenen)                     AS elenen_ad,
       count(*) FILTER (WHERE status <> 'COMPLETED')           AS elenen_durum,
       (SELECT count(*) FROM uygun)                            AS havuz
FROM s;
"""

# SIRALAMA -- ilk sürümün kusuru buydu ve düzeltildi: yalnız `dosya_vurus DESC`
# (dosya içi toplam vuruş) sıralaması, ifadeyi EN ÇOK ANAN dosyayı öne çıkarıyor,
# hükmü KOYAN dosyayı değil. Ölçüldü: "özkaynak" deseninde tepe isabet hesap
# planı dosyası (290 vuruş), 5411 m.44 ise listede yok. Birincil anahtar artık
# BÖLÜM BAŞLIĞI eşleşmesi: bir maddenin başlığı desenle örtüşüyorsa o chunk
# maddenin metnidir, ondan söz eden paragraf değil.
# SINIRI: ILIKE Türkçe 'İ' harfinde güvenilir küçültme yapmaz (locale'e bağlı),
# yani İ ile başlayan başlıklarda bu ipucu SESSİZCE çalışmayabilir. Yalnız bir
# SIRALAMA ipucudur, süzgeç değil -- eşleşmemesi "başlık yok" demek DEĞİLDİR.
_SQL_KONU = _UYGUN + """, m AS (
    SELECT c.file_id, c.chunk_index, c.page_number, c.token_count,
           c.section_title, c.chunk_text,
           (c.section_title ILIKE %(baslik)s)                                  AS baslik_isabeti,
           count(*)     OVER (PARTITION BY c.file_id)                          AS dosya_vurus,
           row_number() OVER (PARTITION BY c.file_id
                              ORDER BY (c.section_title ILIKE %(baslik)s) DESC,
                                       c.chunk_index)                          AS sira
    FROM core_chunks c JOIN uygun USING (file_id)
    WHERE c.chunk_text_norm LIKE %(kalip)s
)
SELECT f.file_name, m.chunk_index, m.page_number, m.token_count,
       m.section_title, m.dosya_vurus, m.baslik_isabeti, m.chunk_text
FROM m JOIN core_files f USING (file_id)
WHERE m.sira <= %(dosya_basi)s
ORDER BY m.baslik_isabeti DESC, m.dosya_vurus DESC, f.file_name, m.chunk_index
LIMIT %(limit)s;
"""

# Konu başına DOSYA DAĞILIMI. İlk sürümde yalnız "N dosya kirpildi" yazıyordu --
# hangi dosyanın kırpıldığı görünmüyordu, dolayısıyla yetkili kaynağın havuzda
# olup da basılmamış olması ile hiç olmaması ayırt EDİLEMİYORDU.
_SQL_KONU_DAGILIM = _UYGUN + """
SELECT f.file_name, count(*) AS vurus,
       bool_or(c.section_title ILIKE %(baslik)s) AS baslikta
FROM core_chunks c JOIN uygun USING (file_id) JOIN core_files f USING (file_id)
WHERE c.chunk_text_norm LIKE %(kalip)s
GROUP BY f.file_name
ORDER BY bool_or(c.section_title ILIKE %(baslik)s) DESC, count(*) DESC, f.file_name;
"""

# Keşif modu: adı verilen dosyanın metnini bas. "Yetkili kaynak desene takılmadı"
# ile "korpusta yok" ancak böyle ayrılır.
_SQL_DOSYA = _UYGUN + """
SELECT f.file_name, f.status, s.c0, s.taslak, s.adla_elenen,
       c.chunk_index, c.page_number, c.token_count, c.section_title, c.chunk_text
FROM core_chunks c JOIN core_files f USING (file_id) JOIN s USING (file_id)
WHERE f.file_name ILIKE %(ad)s AND c.chunk_index >= %(bas)s
ORDER BY f.file_name, c.chunk_index
LIMIT %(limit)s;
"""


def _elenen_kalip() -> list[str]:
    return [f"%{ad}%" for ad in ELENEN_DOSYA]


def _tara(conn, konular: list[dict], *, dosya_basi: int, limit: int,
          dagilim: int) -> list[dict]:
    from ragintel.text import normalize_for_quote

    elenen = _elenen_kalip()
    out = []
    for k in konular:
        norm = normalize_for_quote(k["desen"])
        p = {"kalip": "%" + norm + "%", "baslik": "%" + k["desen"] + "%",
             "elenen": elenen}
        dag = conn.execute(_SQL_KONU_DAGILIM, p).fetchall()
        satirlar = conn.execute(
            _SQL_KONU, {**p, "dosya_basi": dosya_basi, "limit": limit}).fetchall()
        out.append({**k, "normalize": norm,
                    "dosya": len(dag), "chunk": sum(v for _, v, _ in dag),
                    "dagilim": [{"file_name": fn, "vurus": v, "baslikta": b}
                                for fn, v, b in dag[:dagilim]],
                    "dagilim_kirpik": max(0, len(dag) - dagilim),
                    "isabetler": [
                        {"file_name": fn, "chunk_index": ci, "page_number": pn,
                         "token_count": tc, "section_title": st,
                         "dosya_vurus": dv, "baslik_isabeti": bi,
                         "chunk_text": txt}
                        for fn, ci, pn, tc, st, dv, bi, txt in satirlar]})
    return out


def _dosya_kesfi(conn, adlar: list[str], *, bas: int, limit: int,
                 metin: int) -> None:
    elenen = _elenen_kalip()
    for ad in adlar:
        print()
        print("=" * 100)
        print(f"### DOSYA KESFI: {ad}   (chunk_index >= {bas}, ilk {limit})")
        print("=" * 100)
        satirlar = conn.execute(
            _SQL_DOSYA, {"ad": f"%{ad}%", "bas": bas, "limit": limit,
                         "elenen": elenen}).fetchall()
        if not satirlar:
            print("    ISABET YOK -- bu adla eslesen dosya korpusta yok.")
            continue
        gorulen = None
        for (fn, durum, c0, taslak, adla, ci, pn, tc, st, txt) in satirlar:
            if fn != gorulen:
                gorulen = fn
                bayrak = [x for x, v in
                          (("C0-BOZUK", c0), ("TASLAK", taslak),
                           ("ADLA-ELENEN", adla), (f"durum={durum}",
                                                   durum != "COMPLETED")) if v]
                print(f"\n  -- {fn}   [{', '.join(bayrak) or 'HAVUZDA'}]")
            print(f"      chunk={ci:<5} s.{pn or 0:<4} {tc:>4} tok  "
                  f"| {_clip(st, 70)}")
            print(f"        {_clip(txt, metin)}")


def _print_human(havuz, konular: list[dict], *, metin: int, limit: int) -> None:
    toplam, e_c0, e_taslak, e_ad, e_durum, h = havuz
    print("=" * 100)
    print("GOLDEN v1 KAYNAK PROBU  (alinti BILGIDEN yazilmaz, METINDEN kesilir)")
    print("=" * 100)
    print(f"  havuz: {h} dosya / {toplam}")
    print(f"    elenen -- C0 artigi (reprocess bekliyor) : {e_c0}")
    print(f"    elenen -- gorse acilmis TASLAK           : {e_taslak}")
    print(f"    elenen -- adla (mulga vb.)               : {e_ad}")
    for ad, gerekce in ELENEN_DOSYA.items():
        print(f"         · {ad}: {gerekce}")
    print(f"    elenen -- COMPLETED degil                : {e_durum}")
    print("  arama uretimle AYNI yoldan: normalize_for_quote -> chunk_text_norm LIKE")
    print("  siralama: BOLUM BASLIGI eslesmesi > dosya vurusu  (maddeyi KOYAN metin")
    print("            onde; ondan SOZ EDEN paragraf arkada)")

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
        print(f"    havuzda: {k['dosya']} dosya / {k['chunk']} chunk")
        # Dagilim, "yetkili kaynak havuzda var ama basilmadi" halini gorunur kilar.
        pay = ", ".join(f"{_clip(d['file_name'], 34)}"
                        f"{'*' if d['baslikta'] else ''}={d['vurus']}"
                        for d in k["dagilim"])
        print(f"    dosyalar (*=bolum basliginda): {pay}"
              + (f" ... +{k['dagilim_kirpik']}" if k["dagilim_kirpik"] else ""))
        for i in k["isabetler"]:
            print(f"      · {_clip(i['file_name'], 52):<52} chunk={i['chunk_index']:<5} "
                  f"s.{i['page_number'] or 0:<4} {i['token_count']:>4} tok  "
                  f"dosya_vurus={i['dosya_vurus']}"
                  f"{'  [BASLIK]' if i['baslik_isabeti'] else ''}")
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
    ap.add_argument("--dagilim", type=int, default=6,
                    help="Konu basina kac dosya adi tek satirda listelensin")
    ap.add_argument("--dosya", action="append", default=None,
                    help="KESFI MODU: adi verilen dosyanin metnini bas (konu taramasi "
                         "yapilmaz). Yetkili kaynak desene takilmadiysa buradan bakilir")
    ap.add_argument("--dosya-bas", type=int, default=0,
                    help="Kesfi modu: bu chunk_index'ten itibaren bas")
    ap.add_argument("--dosya-limit", type=int, default=8,
                    help="Kesfi modu: kac chunk basilsin")
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
            if args.dosya:
                _dosya_kesfi(conn, args.dosya, bas=args.dosya_bas,
                             limit=args.dosya_limit, metin=args.metin)
                return 0
            havuz = conn.execute(
                _SQL_HAVUZ, {"elenen": _elenen_kalip()}).fetchone()
            veri = _tara(conn, konular, dosya_basi=args.dosya_basi,
                         limit=args.limit, dagilim=args.dagilim)
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

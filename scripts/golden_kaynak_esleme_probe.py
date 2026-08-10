#!/usr/bin/env python
"""Adım-6 ÖN-VERİ · golden taslağının kaynak-çapa kontrolü — SALT-OKUMA (yalnız SELECT).

Amaç: `RagIntel_Turk_Bankacilik_Golden_Dataset_v1.md` taslağındaki 30 soru ~13 ayrı
mevzuata dayanıyor. Taslak mevzuatı ADIYLA anıyor ("Bankaların Sermaye Yeterliliğinin
Ölçülmesine ... Yönetmelik"); `gold_evidence` ise korpustaki GERÇEK `file_name` +
`page` + BİREBİR `quote` istiyor. Bu prob, dönüştürme kodu yazılmadan ÖNCE tek soruyu
cevaplar: her mevzuat korpusta var mı, hangi dosyada, hangi sayfada?

NEDEN ÖNCE BU: eski golden setin retrieval metrikleri 0.000 okuyordu; kök korpus değil,
quote eşlemesinin 0/43 tutmasıydı. Aynı hataya düşmemenin tek yolu alıntıları KORPUSTAN
çıkarmak. Bilgiden yazılan cümle -- ne kadar doğru olursa olsun -- eşleşmez.

EŞLEME KURALI (üretimle aynı, uydurulmadı):
  `retrieval_benchmark` → normalize_for_quote(quote) ⊂ chunk_text_norm  (alt dize)
  Bu yüzden arama desenleri de `normalize_for_quote`'tan geçirilir; probun bulduğu
  chunk, gerçek eşlemenin de bulacağı chunk'tır.

ÖLÇÜM-ZEMİNİ / GÜVENLİK:
  • Yalnız SELECT. DB/prod/config'e YAZMAZ, golden'a DOKUNMAZ.
  • Aday olmanın ön-koşulu RETRIEVABLE olmak: chunk'ın `core_vectors`'ta karşılığı
    yoksa A/B'de zaten çekilemez → sayım ayrıca raporlanır.
  • "Bulundu = 0" tek başına okunamaz: her mevzuatın HANGİ desenle kaç vuruş aldığı
    ayrı basılır, böylece sıfırın kökü (mevzuat yok / desenim yanlış) ayrışır.

Bare-metal (H200):
  cd /opt/ragintel && python scripts/golden_kaynak_esleme_probe.py
Konteynerde:
  docker cp scripts/golden_kaynak_esleme_probe.py ragintel-api:/app/p.py
  docker exec ragintel-api python /app/p.py
"""

from __future__ import annotations

import argparse
import json
import sys


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _clip(text: str | None, n: int = 300) -> str:
    if not text:
        return ""
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


# --- Taslaktan çıkarılan mevzuat kataloğu ------------------------------------
# (anahtar, insan adı, arama desenleri, taslakta buna dayanan soru id'leri)
# Desenler mevzuatın BAŞLIĞINDAN veya tanımlayıcı terimlerinden seçildi; birden
# çok desen OR'lanır ve her birinin vuruşu AYRI sayılır (sıfırın kökü ayrışsın).
KATALOG: list[dict] = [
    {"anahtar": "5411", "ad": "5411 sayılı Bankacılık Kanunu",
     "desenler": ["5411 sayılı bankacılık kanunu", "5411 sayılı kanun"],
     "sorular": ["E01", "E02", "E07", "H04"]},
    {"anahtar": "sermaye_yeterlilik", "ad": "Sermaye Yeterliliği Yönetmeliği",
     "desenler": ["sermaye yeterliliğinin ölçülmesine", "sermaye yeterliliği standart oranı"],
     "sorular": ["E03", "M01", "M04", "M05", "H01", "H02", "H06", "H07", "H09", "H10"]},
    {"anahtar": "likidite", "ad": "Likidite Yeterliliği Yönetmeliği",
     "desenler": ["likidite yeterliliğinin ölçülmesine", "likidite karşılama oranı"],
     "sorular": ["E04", "M05", "H01", "H06", "H07"]},
    {"anahtar": "karsilik", "ad": "Kredilerin Sınıflandırılması ve Karşılıklar Yönetmeliği",
     "desenler": ["kredilerin sınıflandırılması", "ayrılacak karşılıklara ilişkin"],
     "sorular": ["E05", "M03", "H02", "H03", "H06", "H08"]},
    {"anahtar": "sir", "ad": "Sır Niteliğindeki Bilgilerin Paylaşılması Yönetmeliği",
     "desenler": ["sır niteliğindeki bilgilerin paylaşılması", "müşteri sırrı"],
     "sorular": ["E06", "E07", "M06", "H04"]},
    {"anahtar": "bilgi_sistemleri", "ad": "Bilgi Sistemleri ve Elektronik Bankacılık Yönetmeliği",
     "desenler": ["bilgi sistemleri ve elektronik bankacılık hizmetleri",
                  "elektronik bankacılık hizmetleri"],
     "sorular": ["E08", "M07", "H04", "H05"]},
    {"anahtar": "kaldirac", "ad": "Kaldıraç Düzeyi Yönetmeliği",
     "desenler": ["kaldıraç düzeyinin ölçülmesine", "kaldıraç oranı"],
     "sorular": ["E09", "M01", "H01", "H09"]},
    {"anahtar": "sistemik", "ad": "Sistemik Önemli Bankalar Yönetmeliği",
     "desenler": ["sistemik önemli banka", "sistemik önemli bankalar hakkında"],
     "sorular": ["E10", "M10", "H09"]},
    {"anahtar": "tfrs9", "ad": "TFRS 9 Beklenen Kredi Zararı Rehberi",
     "desenler": ["beklenen kredi zararı", "tfrs 9"],
     "sorular": ["M02", "H02", "H08"]},
    {"anahtar": "tampon", "ad": "Sermaye Koruma ve Döngüsel Sermaye Tamponları Yönetmeliği",
     "desenler": ["sermaye koruma tamponu", "döngüsel sermaye tamponu"],
     "sorular": ["M04", "H09"]},
    {"anahtar": "sorunlu_alacak", "ad": "Sorunlu Alacak Çözümleme Rehberi",
     "desenler": ["sorunlu alacak", "yeniden yapılandırma"],
     "sorular": ["M08", "H03"]},
    {"anahtar": "yp_pozisyon", "ad": "Yabancı Para Net Genel Pozisyon Yönetmeliği",
     "desenler": ["yabancı para net genel pozisyon", "net genel pozisyon"],
     "sorular": ["M09"]},
    {"anahtar": "ozkaynak", "ad": "Bankaların Özkaynaklarına İlişkin Yönetmelik",
     "desenler": ["bankaların özkaynaklarına ilişkin", "çekirdek sermaye"],
     "sorular": ["H09"]},
]


# --- SALT-OKUMA sorgular -----------------------------------------------------

_SQL_ENVANTER = """
SELECT count(DISTINCT f.file_id)                          AS dosya,
       count(c.chunk_id)                                  AS chunk,
       count(v.chunk_id)                                  AS vektorlu,
       count(DISTINCT f.file_id) FILTER (WHERE f.status <> 'COMPLETED') AS tamamlanmamis
FROM core_files f
LEFT JOIN core_chunks  c USING (file_id)
LEFT JOIN core_vectors v USING (chunk_id);
"""

# Desen başına vuruş: sıfırın kökünü ayırmak için AYRI sayılır.
_SQL_DESEN = """
SELECT count(*)                        AS chunk,
       count(DISTINCT c.file_id)       AS dosya,
       count(v.chunk_id)               AS vektorlu
FROM core_chunks c
LEFT JOIN core_vectors v USING (chunk_id)
WHERE c.chunk_text_norm LIKE %(kalip)s;
"""

# Mevzuat başına en iyi dosyalar (tüm desenlerin OR'u).
_SQL_DOSYALAR = """
SELECT f.file_name,
       count(*)                        AS vurus,
       min(c.page_number)              AS ilk_sayfa,
       count(v.chunk_id)               AS vektorlu
FROM core_chunks c
JOIN core_files f USING (file_id)
LEFT JOIN core_vectors v USING (chunk_id)
WHERE c.chunk_text_norm LIKE ANY(%(kaliplar)s)
GROUP BY f.file_name
ORDER BY vurus DESC, f.file_name
LIMIT %(limit)s;
"""

# Çapa adayları: alıntı ham maddesi. Uzun ve vektörlü chunk'lar önce.
_SQL_CAPA = """
SELECT f.file_name, c.page_number, c.chunk_index, c.token_count,
       (v.chunk_id IS NOT NULL) AS vektorlu,
       c.section_title, c.chunk_text
FROM core_chunks c
JOIN core_files f USING (file_id)
LEFT JOIN core_vectors v USING (chunk_id)
WHERE c.chunk_text_norm LIKE ANY(%(kaliplar)s)
  AND c.page_number IS NOT NULL
  AND v.chunk_id IS NOT NULL
ORDER BY c.token_count DESC
LIMIT %(limit)s;
"""


def _tara(conn, katalog: list[dict], *, dosya_limit: int, capa_limit: int) -> list[dict]:
    """Her mevzuat için desen-bazlı vuruş + dosya dağılımı + çapa adayları."""
    from ragintel.text import normalize_for_quote

    sonuc = []
    for m in katalog:
        # Üretim eşlemesiyle AYNI normalizasyon: prob neyi bulursa benchmark da onu bulur.
        kaliplar = ["%" + normalize_for_quote(d) + "%" for d in m["desenler"]]
        desen_detay = []
        for ham, kalip in zip(m["desenler"], kaliplar, strict=True):
            ch, ds, vk = conn.execute(_SQL_DESEN, {"kalip": kalip}).fetchone()
            desen_detay.append({"desen": ham, "chunk": ch, "dosya": ds, "vektorlu": vk})

        dosyalar = [
            {"file_name": fn, "vurus": v, "ilk_sayfa": sp, "vektorlu": vk}
            for fn, v, sp, vk in conn.execute(
                _SQL_DOSYALAR, {"kaliplar": kaliplar, "limit": dosya_limit}).fetchall()
        ]
        capalar = [
            {"file_name": fn, "page": pg, "chunk_index": ix, "token_count": tk,
             "vektorlu": vk, "section_title": st, "metin": txt}
            for fn, pg, ix, tk, vk, st, txt in conn.execute(
                _SQL_CAPA, {"kaliplar": kaliplar, "limit": capa_limit}).fetchall()
        ]
        toplam = sum(d["chunk"] for d in desen_detay)
        sonuc.append({**m, "desen_detay": desen_detay, "toplam_vurus": toplam,
                      "dosyalar": dosyalar, "capalar": capalar})
    return sonuc


def _print_human(env, kayitlar: list[dict], capa_goster: int) -> None:
    dosya, chunk, vektorlu, tamamlanmamis = env
    print("BOLUM A  KORPUS ENVANTERI")
    print("=" * 100)
    print(f"  dosya            : {dosya:,}")
    print(f"  chunk            : {chunk:,}")
    print(f"  vektorlu chunk   : {vektorlu:,}"
          + ("" if vektorlu == chunk else f"   <-- {chunk - vektorlu:,} chunk CEKILEMEZ"))
    if tamamlanmamis:
        print(f"  UYARI: {tamamlanmamis} dosya COMPLETED degil -- korpus su an DEGISIYOR,")
        print("         asagidaki sayimlar ara-durumdur, kosum bitince tekrarlayin.")
    print()

    print("-" * 100)
    print("BOLUM B  MEVZUAT CAPA KONTROLU -- taslaktaki her kaynak korpusta var mi?")
    print("-" * 100)
    print(f"  {'mevzuat':<52} {'vurus':>7} {'dosya':>6}  soru")
    for k in kayitlar:
        ds = len({d["file_name"] for d in k["dosyalar"]})
        isaret = "" if k["toplam_vurus"] else "   <-- CAPA YOK"
        print(f"  {_clip(k['ad'], 52):<52} {k['toplam_vurus']:>7,} {ds:>6}  "
              f"{','.join(k['sorular'])}{isaret}")
    print()
    print("  Desen kirilimi (sifirin koku: mevzuat mi yok, desen mi yanlis?):")
    for k in kayitlar:
        print(f"    {k['ad']}")
        for d in k["desen_detay"]:
            print(f"      {d['desen']:<48} chunk={d['chunk']:>6,} "
                  f"dosya={d['dosya']:>4} vektorlu={d['vektorlu']:>6,}")
    print()

    print("-" * 100)
    print("BOLUM C  HUKUM -- hangi sorular dayanaksiz?")
    print("-" * 100)
    bos = [k for k in kayitlar if not k["toplam_vurus"]]
    if not bos:
        print("  Taslaktaki 13 mevzuatin TAMAMI korpusta capa buluyor.")
    else:
        etkilenen = sorted({s for k in bos for s in k["sorular"]})
        print(f"  CAPASIZ mevzuat : {len(bos)}")
        for k in bos:
            print(f"    - {k['ad']}  (sorular: {','.join(k['sorular'])})")
        print(f"  ETKILENEN SORU  : {len(etkilenen)} / 30  -> {','.join(etkilenen)}")
        print("  Bu sorular ya DUSURULMELI ya da korpusta gercekten bulunan bir")
        print("  mevzuata yeniden capalanmalidir. Bilgiden alinti yazilmaz.")
    print()

    print("-" * 100)
    print("BOLUM D  CAPA ADAYLARI -- birebir alinti ham maddesi")
    print("-" * 100)
    print("  Alinti bu metinden KESILEREK alinir (normalize edilmis hali")
    print("  chunk_text_norm icinde alt dize olmali). file_name + page dogrudan")
    print("  gold_evidence'a yazilir.")
    for k in kayitlar:
        if not k["capalar"]:
            continue
        print()
        print(f"  ### {k['ad']}   (sorular: {','.join(k['sorular'])})")
        for c in k["capalar"][:capa_goster]:
            print(f"    {c['file_name']}  s.{c['page']}  chunk#{c['chunk_index']}  "
                  f"{c['token_count']} tok" + ("" if c["vektorlu"] else "  [VEKTORSUZ]"))
            if c["section_title"]:
                print(f"      bolum: {_clip(c['section_title'], 90)}")
            print(f"      {_clip(c['metin'], 300)}")
    print()


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(prog="golden_kaynak_esleme_probe")
    ap.add_argument("--mevzuat", action="append", default=None,
                    help="Yalniz bu anahtar(lar)i tara (yinelenebilir). Orn: --mevzuat tfrs9")
    ap.add_argument("--dosya-limit", type=int, default=5, help="Mevzuat basi listelenecek dosya")
    ap.add_argument("--capa-limit", type=int, default=6, help="Mevzuat basi cekilecek capa adayi")
    ap.add_argument("--capa-goster", type=int, default=2, help="Insan ciktisinda basilacak capa")
    ap.add_argument("--json", action="store_true", help="Ham JSON bas")
    args = ap.parse_args(argv)

    katalog = KATALOG
    if args.mevzuat:
        istenen = set(args.mevzuat)
        katalog = [m for m in KATALOG if m["anahtar"] in istenen]
        if not katalog:
            print(f"Bilinmeyen mevzuat anahtari: {sorted(istenen)}", file=sys.stderr)
            print(f"Gecerli: {[m['anahtar'] for m in KATALOG]}", file=sys.stderr)
            return 2

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database

    db = Database(DbSettings()).open()
    try:
        with db.connection() as conn:
            env = conn.execute(_SQL_ENVANTER).fetchone()
            kayitlar = _tara(conn, katalog,
                             dosya_limit=args.dosya_limit, capa_limit=args.capa_limit)
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()

    if args.json:
        print(json.dumps(
            {"envanter": {"dosya": env[0], "chunk": env[1], "vektorlu": env[2],
                          "tamamlanmamis": env[3]},
             "mevzuat": kayitlar},
            ensure_ascii=False, indent=2))
    else:
        _print_human(env, kayitlar, args.capa_goster)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Metin korunumu: parse → clean → chunk boyunca metin nerede kayboluyor? — SALT-OKUMA.

NEDEN: skor_tanimi_probe §4, en düşük skorlu 10 dosyanın TAMAMININ n=2 veya n=4
chunk ürettiğini gösterdi (mevzuat_*.pdf). Korpusun medyan chunk alt skoru 50.00,
yani medyan dosyada chunk'ların YARISI 30 token altında. Bir dosya 2 chunk
üretiyorsa ve biri 30 token altındaysa skor mekanik olarak 50 çıkar — bu bir
kalite ölçüsü değil, DOSYA KÜÇÜKLÜĞÜNÜN artefaktı olur.

Ama asıl soru şu: bu dosyalar gerçekten küçük mü? ~10 sayfalık bir BDDK mevzuat
metni 2 chunk'a (≤1024 token) SIĞMAZ. Sığıyor görünüyorsa iki ihtimal var:
  (a) dosyalar gerçekten kısa (1-2 sayfalık tebliğ/değişiklik) → skor kusuru,
      ama korpus sağlam; düzeltilecek yer skorun paydası,
  (b) metin parse veya clean aşamasında KAYBOLUYOR → korpus kusuru, ve bu
      listedeki her şeyden büyük: cevaplanamayan sorular buradan gelir.
İkisi ölçülmeden ayrılamaz. Bu probe ayırır.

NE ÖLÇER (hiçbir şey değiştirmez):
  §1 Zincir denetimi — dosya başına parse char_count → clean cleaned_chars →
     chunk'lara giren toplam karakter. Chunk'lar overlap'li (64 token), yani
     SAĞLIKLI durumda chunk toplamı cleaned_chars'tan BÜYÜK olmalı. Küçükse
     metin düşüyor demektir; oran ne kadar düşük, o kadar kayıp.
  §2 Sayfa başına verim — sayfa sayısı vs chunk sayısı vs token. Küçük dosya
     hipotezini (a) doğrudan sınar: 2 chunk üreten dosyalar 1-2 sayfalık mı?
  §3 En düşük skorlu 10 dosyanın zinciri tek tek (skor_tanimi §4 ile aynı liste)
  §4 Kayıp şüphelisi dosyalar — chunk/clean karakter oranı en düşük 15
  §5 min-altı (<30 token) chunk'lar NEREDE? İlk chunk mı, son chunk mı, dağınık
     mı? _min_merge yalnız GERİYE birleştiriyor (chunker.py), yani bir dosyanın
     İLK chunk'ı asla birleşemez — bu hipotez chunk_index=0 payıyla sınanır.

KOŞUM (H200, venv + .env.h200 yüklü):
    python scripts/metin_korunumu_probe.py
"""

from __future__ import annotations

import sys

# Chunk'lara giren metnin gerçek boyu: chunk_text (orijinal), norm değil.
_SQL_CHUNK = """
SELECT c.file_id,
       count(*)                          AS n,
       sum(length(c.chunk_text))         AS kar,
       sum(c.token_count)                AS tok,
       count(*) FILTER (WHERE c.token_count < %(min_t)s)                          AS kucuk,
       count(*) FILTER (WHERE c.token_count < %(min_t)s AND c.chunk_index = 0)    AS kucuk_ilk,
       count(*) FILTER (WHERE c.table_id IS NOT NULL)                             AS tablo,
       max(c.chunk_index)                                                         AS son_idx,
       count(*) FILTER (WHERE c.token_count < %(min_t)s
                        AND c.chunk_index = (SELECT max(x.chunk_index)
                                             FROM core_chunks x
                                             WHERE x.file_id = c.file_id))        AS kucuk_son
FROM core_chunks c
GROUP BY c.file_id;
"""

_SQL_DOSYA = """
SELECT f.file_id, f.file_name, f.quality_score
FROM core_files f
WHERE f.status = 'COMPLETED'
ORDER BY f.file_id;
"""

_SQL_METRIK = """
SELECT m.file_id, m.step, m.detail
FROM metrics_ingestion m
JOIN core_files f ON f.file_id = m.file_id AND f.status = 'COMPLETED'
ORDER BY m.file_id, m.metric_id;
"""


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _dagilim(vals: list[float], birim: str = "") -> str:
    if not vals:
        return "(boş)"
    s = sorted(vals)
    p = lambda q: s[min(len(s) - 1, int(len(s) * q))]          # noqa: E731
    return (f"min {s[0]:8.2f} | p05 {p(0.05):8.2f} | p50 {p(0.50):8.2f} "
            f"| ort {sum(s)/len(s):8.2f} | p95 {p(0.95):8.2f} | max {s[-1]:8.2f}{birim}")


def main() -> int:
    _force_utf8()

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database

    db = Database(DbSettings()).open()
    try:
        from ragintel.config.loader import load_config
        from ragintel.database.config_store import make_db_reader
        cfg = load_config(db_reader=make_db_reader(db))
        ch = cfg.group("chunking")
        min_t, max_t = int(ch.min_tokens), int(ch.max_tokens)

        print("=" * 96)
        print("§0 EFEKTİF CONFIG")
        print("=" * 96)
        print(f"  chunking: strategy={ch.strategy} max_tokens={max_t} "
              f"min_tokens={min_t} overlap={ch.overlap_tokens}")

        with db.connection() as conn:
            cur = conn.cursor()
            chunklar = {r[0]: r[1:] for r in cur.execute(_SQL_CHUNK, {"min_t": min_t}).fetchall()}
            dosyalar = cur.execute(_SQL_DOSYA).fetchall()
            metrikler = cur.execute(_SQL_METRIK).fetchall()

        per_file: dict[int, list[tuple[str, dict]]] = {}
        for fid, step, detail in metrikler:
            per_file.setdefault(fid, []).append((step, detail or {}))

        satirlar = []
        for fid, ad, skor in dosyalar:
            c = chunklar.get(fid)
            if not c:
                continue
            n, kar, tok, kucuk, kucuk_ilk, tablo, _son_idx, kucuk_son = c
            adimlar = per_file.get(fid, [])
            # parse: OCR fallback varsa KABUL EDİLEN deneme (qc.py:132 ile aynı kural)
            pd = [d for s, d in adimlar if s == "parse"]
            cd = [d for s, d in adimlar if s == "clean"]
            if not pd or not cd:
                continue
            p = max(pd, key=lambda d: d.get("attempt", 1))
            k = cd[-1]
            p_kar = p.get("char_count")
            c_kar = k.get("cleaned_chars")
            sayfa = p.get("page_count")
            if not p_kar or not c_kar:
                continue
            satirlar.append({
                "fid": fid, "ad": ad,
                "skor": float(skor) if skor is not None else None,
                "sayfa": sayfa or 0, "p_kar": p_kar, "c_kar": c_kar,
                "n": n, "kar": kar or 0, "tok": tok or 0,
                "kucuk": kucuk, "kucuk_ilk": kucuk_ilk, "kucuk_son": kucuk_son,
                "tablo": tablo,
                "tutma": c_kar / p_kar,          # clean retention (yeniden hesap)
                "korunum": (kar or 0) / c_kar,   # chunk'a giren / temizlikten çıkan
            })

        print(f"\n  ölçülen dosya: {len(satirlar)}/{len(dosyalar)}")

        # ------------------------------------------------------------------ §1
        print("\n" + "=" * 96)
        print("§1 ZİNCİR DENETİMİ — metin korunuyor mu?")
        print("=" * 96)
        tp = sum(r["p_kar"] for r in satirlar)
        tc = sum(r["c_kar"] for r in satirlar)
        tk = sum(r["kar"] for r in satirlar)
        print(f"  parse char_count toplamı   : {tp:>12,}")
        print(f"  clean cleaned_chars toplamı: {tc:>12,}   (parse'ın %{100*tc/tp:.2f}'si)")
        print(f"  chunk metni toplamı        : {tk:>12,}   (clean'in %{100*tk/tc:.2f}'si)")
        print("\n  Beklenti: chunk/clean oranı > %100 olmalı — pencereler 64 token")
        print("  OVERLAP'li, yani metin bir miktar TEKRAR eder. %100'ün altı KAYIPTIR.")
        print(f"\n  dosya başına chunk/clean oranı: "
              f"{_dagilim([100 * r['korunum'] for r in satirlar], '%')}")
        kayip = [r for r in satirlar if r["korunum"] < 0.95]
        agir = [r for r in satirlar if r["korunum"] < 0.50]
        print(f"\n  oranı %95 ALTINDA olan dosya : {len(kayip):>5} / {len(satirlar)}"
              f"  (%{100*len(kayip)/len(satirlar):.1f})")
        print(f"  oranı %50 ALTINDA olan dosya : {len(agir):>5} / {len(satirlar)}"
              f"  (%{100*len(agir)/len(satirlar):.1f})  ← metnin yarısından çoğu düşmüş")
        if agir:
            kk = sum(r["c_kar"] - r["kar"] for r in agir)
            print(f"  ağır kayıplı dosyalarda düşen karakter: {kk:,}")

        # ------------------------------------------------------------------ §2
        print("\n" + "=" * 96)
        print("§2 SAYFA BAŞINA VERİM — '2 chunk' küçüklükten mi, kayıptan mı?")
        print("=" * 96)
        sayfali = [r for r in satirlar if r["sayfa"] > 0]
        print(f"  sayfa sayısı        : {_dagilim([float(r['sayfa']) for r in sayfali])}")
        print(f"  chunk sayısı        : {_dagilim([float(r['n']) for r in satirlar])}")
        print(f"  chunk / sayfa       : {_dagilim([r['n'] / r['sayfa'] for r in sayfali])}")
        print(f"  token / sayfa       : {_dagilim([r['tok'] / r['sayfa'] for r in sayfali])}")
        print(f"  clean karakter/sayfa: {_dagilim([r['c_kar'] / r['sayfa'] for r in sayfali])}")

        print("\n  --- chunk sayısına göre kırılım ---")
        print(f"  {'chunk adedi':>14} | {'dosya':>6} | {'ort sayfa':>9} | "
              f"{'ort clean kar':>13} | {'ort chunk kar':>13} | {'korunum':>8}")
        for etiket, kos in ((" n ≤ 2", lambda r: r["n"] <= 2),
                            (" n = 3-4", lambda r: 3 <= r["n"] <= 4),
                            (" n = 5-10", lambda r: 5 <= r["n"] <= 10),
                            (" n = 11-50", lambda r: 11 <= r["n"] <= 50),
                            (" n > 50", lambda r: r["n"] > 50)):
            g = [r for r in satirlar if kos(r)]
            if not g:
                continue
            gs = [r for r in g if r["sayfa"] > 0]
            print(f"  {etiket:>14} | {len(g):>6} | "
                  f"{(sum(r['sayfa'] for r in gs)/len(gs) if gs else 0):>9.1f} | "
                  f"{sum(r['c_kar'] for r in g)/len(g):>13,.0f} | "
                  f"{sum(r['kar'] for r in g)/len(g):>13,.0f} | "
                  f"{100*sum(r['kar'] for r in g)/max(1, sum(r['c_kar'] for r in g)):>7.1f}%")
        print("\n  OKUMA: 'n ≤ 2' satırında ort sayfa küçükse (1-2) hipotez (a) —")
        print("  dosyalar gerçekten kısa, kusur SKORUN paydasında. Ort sayfa büyükse")
        print("  ya da korunum düşükse hipotez (b) — metin KAYBOLUYOR.")

        # ------------------------------------------------------------------ §3
        print("\n" + "=" * 96)
        print("§3 EN DÜŞÜK SKORLU 10 DOSYANIN ZİNCİRİ")
        print("=" * 96)
        print(f"  {'dosya':<28} {'skor':>6} {'syf':>4} {'parse kar':>10} "
              f"{'clean kar':>10} {'chunk kar':>10} {'n':>4} {'tok':>7} {'korunum':>8}")
        for r in sorted((r for r in satirlar if r["skor"] is not None),
                        key=lambda r: r["skor"])[:10]:
            print(f"  {r['ad'][:28]:<28} {r['skor']:>6.2f} {r['sayfa']:>4} "
                  f"{r['p_kar']:>10,} {r['c_kar']:>10,} {r['kar']:>10,} "
                  f"{r['n']:>4} {r['tok']:>7,} {100*r['korunum']:>7.1f}%")

        # ------------------------------------------------------------------ §4
        print("\n" + "=" * 96)
        print("§4 KAYIP ŞÜPHELİLERİ — chunk/clean oranı en düşük 15")
        print("=" * 96)
        print(f"  {'dosya':<28} {'skor':>6} {'syf':>4} {'clean kar':>10} "
              f"{'chunk kar':>10} {'n':>4} {'tablo':>5} {'korunum':>8}")
        for r in sorted(satirlar, key=lambda r: r["korunum"])[:15]:
            skor = f"{r['skor']:>6.2f}" if r["skor"] is not None else "     -"
            print(f"  {r['ad'][:28]:<28} {skor} {r['sayfa']:>4} "
                  f"{r['c_kar']:>10,} {r['kar']:>10,} {r['n']:>4} {r['tablo']:>5} "
                  f"{100*r['korunum']:>7.1f}%")

        # ------------------------------------------------------------------ §5
        print("\n" + "=" * 96)
        print(f"§5 min-altı (<{min_t} token) chunk'lar NEREDE?")
        print("=" * 96)
        tk_kucuk = sum(r["kucuk"] for r in satirlar)
        tk_ilk = sum(r["kucuk_ilk"] for r in satirlar)
        tk_son = sum(r["kucuk_son"] for r in satirlar)
        print(f"  toplam min-altı chunk        : {tk_kucuk}")
        if tk_kucuk:
            print(f"    bunlardan chunk_index = 0  : {tk_ilk:>5}  "
                  f"(%{100*tk_ilk/tk_kucuk:.1f})  ← _min_merge yalnız GERİYE birleştirir,")
            print("                                          ilk chunk'ın birleşeceği yer YOK")
            print(f"    bunlardan SON chunk        : {tk_son:>5}  (%{100*tk_son/tk_kucuk:.1f})")
            print(f"    ortada (ne ilk ne son)     : {tk_kucuk-tk_ilk-tk_son:>5}  "
                  f"(%{100*(tk_kucuk-tk_ilk-tk_son)/tk_kucuk:.1f})")
        etkilenen = [r for r in satirlar if r["kucuk"]]
        print(f"\n  min-altı chunk'ı OLAN dosya  : {len(etkilenen)}/{len(satirlar)}"
              f"  (%{100*len(etkilenen)/len(satirlar):.1f})")
        sadece_ilk = [r for r in etkilenen if r["kucuk"] == r["kucuk_ilk"]]
        print(f"    tek kusuru İLK chunk olan  : {len(sadece_ilk)}"
              f"  → _min_merge düzeltmesi ({'' if not etkilenen else f'%{100*len(sadece_ilk)/len(etkilenen):.1f}'}) "
              f"bu dosyaları tek başına temizler")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

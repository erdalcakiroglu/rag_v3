#!/usr/bin/env python
"""Korpus kalite KIRILIM probu — SALT-OKUMA (yalnız SELECT).

NEDEN: `python -m ragintel.report ingestion` özet veriyor (bileşik skor + bulgu
SAYILARI) ama teşhis vermiyor. 1119 dosyalık BDDK korpusunda açık bulgular:
too_short=1060, duplicate_chunk=297, too_long=217, chunk_truncation_high=132.
Bu betik her birinin ARKASINDAKİ dağılımı basar ki "gerçek kusur" ile "ölçüm
aracının yan etkisi" ayrılabilsin (ölçüm-zemini dersi: bulgu sayısı tek başına
kusur değildir).

ÖLÇÜM-ZEMİNİ:
  • Yalnız SELECT. Korpusa/config'e/golden'a YAZMAZ.
  • Eşikler DB config'ten OKUNUR (kodda sabit eşik yok) — dev varsayılanı
    (max_tokens=512/min_tokens=30) H200'de farklı olabilir, rapor hangi değerle
    ölçtüğünü basar.
  • §1 golden↔korpus çakışmasını ölçer: retrieval benchmark'ın quote eşlemesi
    `file_name` ile aday çeker; golden başka bir korpusa aitse metrikler
    kusurdan DEĞİL bayat ölçüm aracından 0 çıkar.
  • §7 coverage'ı SAYFA cinsinden ölçer, oran cinsinden değil: 2 sayfalık bir
    belgede coverage 0.5 = 1 sayfa (granül eseri), 223 sayfalıkta 0.95 = 11
    sayfa (gerçek kayıp) — oranla sıralamak ikincisini gizler. Ayrıca
    `page_ratio` yan yana basılır: metin çıkmayan sayfada TABLO/ŞEKİL varsa
    içerik aslında çıkarılmıştır, oysa `parse_score` yalnız coverage kullanır
    (parsing/quality.py) → o dosyaya haksız ceza yazılır.

KOŞUM (H200 bare-metal, venv aktif + .env.h200 yüklü):
    python scripts/kalite_kirilim_probe.py
    python scripts/kalite_kirilim_probe.py --json > kirilim.json
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


def _clip(text, n: int = 110) -> str:
    if not text:
        return ""
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


# --- §1 golden v0 kanıt dosyaları (eval/golden/v0.jsonl'den okunur) ----------
_SQL_GOLDEN_MATCH = """
SELECT g.name,
       (SELECT count(*) FROM core_files f WHERE f.file_name = g.name) AS korpusta
FROM unnest(%(names)s::text[]) AS g(name)
ORDER BY korpusta, g.name;
"""

# --- §2 bulgu × chunk-türü kırılımı -----------------------------------------
_SQL_FINDING_BREAKDOWN = """
SELECT q.finding,
       count(*)                                              AS bulgu,
       count(DISTINCT q.file_id)                             AS dosya,
       count(*) FILTER (WHERE c.table_id IS NOT NULL)        AS tablo_kokenli,
       count(*) FILTER (WHERE c.chunk_id IS NULL)            AS chunksiz_dosya_duzeyi,
       min(c.token_count)                                    AS tok_min,
       round(avg(c.token_count)::numeric, 1)                 AS tok_ort,
       max(c.token_count)                                    AS tok_max
FROM qc_findings q
LEFT JOIN core_chunks c ON c.chunk_id = q.chunk_id
WHERE NOT q.resolved
GROUP BY q.finding
ORDER BY count(*) DESC;
"""

# --- §3 token dolu-luk anatomisi: "kesik" mi "bütçesi dolmuş" mu? ------------
_SQL_TOKEN_ANATOMY = """
SELECT CASE
         WHEN c.token_count >  %(max_t)s THEN '3) max ÜSTÜ (bölünemedi)'
         WHEN c.token_count =  %(max_t)s THEN '2) tam max (bütçe doldu)'
         WHEN c.token_count <  %(min_t)s THEN '0) min ALTI'
         ELSE                                 '1) normal aralık'
       END AS kova,
       count(*)                                       AS chunk,
       count(*) FILTER (WHERE c.table_id IS NOT NULL) AS tablo_kokenli,
       count(DISTINCT c.file_id)                      AS dosya
FROM core_chunks c
GROUP BY 1 ORDER BY 1;
"""

# --- §4 dosyalar-arası duplicate: retrieval kirliliği ------------------------
_SQL_CROSS_DUP = """
WITH d AS (
    SELECT c.chunk_text_norm,
           count(*)                 AS chunk_adet,
           count(DISTINCT c.file_id) AS dosya_adet,
           min(c.token_count)        AS tok
    FROM core_chunks c
    GROUP BY c.chunk_text_norm
    HAVING count(DISTINCT c.file_id) > 1
)
SELECT chunk_adet, dosya_adet, tok, chunk_text_norm
FROM d ORDER BY dosya_adet DESC, chunk_adet DESC LIMIT %(limit)s;
"""

_SQL_CROSS_DUP_TOPLAM = """
WITH d AS (
    SELECT c.chunk_text_norm, count(*) AS n
    FROM core_chunks c GROUP BY c.chunk_text_norm
    HAVING count(DISTINCT c.file_id) > 1
)
SELECT count(*) AS grup, coalesce(sum(n), 0) AS etkilenen_chunk,
       (SELECT count(*) FROM core_chunks)    AS toplam_chunk
FROM d;
"""

# --- §5 FAILED + düşük kapsam dosyaları --------------------------------------
_SQL_FAILED = """
SELECT f.file_id, f.file_name, f.status, f.retry_count, f.fail_reason
FROM core_files f WHERE f.status <> 'COMPLETED' ORDER BY f.file_id;
"""

_SQL_WORST = """
SELECT f.file_id, f.file_name, f.quality_score,
       (SELECT m.detail FROM metrics_ingestion m
         WHERE m.file_id = f.file_id AND m.step = 'parse'
         ORDER BY m.metric_id DESC LIMIT 1) AS parse_detail,
       (SELECT m.detail FROM metrics_ingestion m
         WHERE m.file_id = f.file_id AND m.step = 'chunk'
         ORDER BY m.metric_id DESC LIMIT 1) AS chunk_detail
FROM core_files f
WHERE f.quality_score IS NOT NULL
ORDER BY f.quality_score ASC, f.file_id LIMIT %(limit)s;
"""


# --- §7 coverage anatomisi: "gerçek içerik kaybı" mı, ölçüm granülü mü? ------
# coverage = METİN çıkan sayfa oranı; page_ratio = metin VEYA tablo VEYA şekil
# üreten sayfa oranı. parse_score YALNIZ coverage kullanır → salt-tablo bir sayfa
# "kayıp" sayılır ama tablosu çıkarılmıştır. İkisi yan yana basılmadan
# "yarım belge" ile "kapak sayfası" ayrılamaz.
_P_CTE = """
WITH p AS (
    SELECT DISTINCT ON (m.file_id)
           m.file_id,
           (m.detail->>'coverage')::numeric      AS coverage,
           (m.detail->>'page_ratio')::numeric    AS page_ratio,
           (m.detail->>'garbage_ratio')::numeric AS garbage,
           (m.detail->>'page_count')::int        AS pages,
           (m.detail->>'char_count')::int        AS chars,
           coalesce((m.detail->>'ocr')::boolean, false) AS ocr
    FROM metrics_ingestion m
    WHERE m.step = 'parse'
    ORDER BY m.file_id, m.metric_id DESC
)
"""

_SQL_COVERAGE_HIST = _P_CTE + """
SELECT CASE
         WHEN p.coverage IS NULL          THEN '4) ÖLÇÜLMEMİŞ'
         WHEN p.coverage >= 1.0           THEN '3) tam 1.0'
         WHEN p.coverage >= %(soft)s      THEN '2) soft..1.0'
         WHEN p.coverage >= %(hard)s      THEN '1) hard..soft (low_coverage bölgesi)'
         ELSE                                  '0) hard ALTI'
       END                                                   AS kova,
       count(*)                                              AS dosya,
       coalesce(sum(p.pages), 0)                             AS sayfa,
       coalesce(sum(round((p.pages * (1 - p.coverage))::numeric)), 0) AS tahmini_eksik_sayfa,
       count(*) FILTER (WHERE p.page_ratio >= 1.0)           AS ama_page_ratio_tam,
       count(*) FILTER (WHERE p.ocr)                         AS ocr_kosmus
FROM p GROUP BY 1 ORDER BY 1;
"""

# Sıralama coverage'a göre DEĞİL, TAHMİNİ EKSİK SAYFAYA göre: 2 sayfalık bir
# belgede coverage 0.5 = 1 sayfa, 223 sayfalıkta 0.95 = 11 sayfa. İkincisi daha ağır.
_SQL_COVERAGE_WORST = _P_CTE + """
SELECT f.file_id, f.file_name, p.pages, p.coverage, p.page_ratio, p.garbage, p.ocr,
       round((p.pages * (1 - p.coverage))::numeric)          AS tahmini_eksik,
       EXISTS (SELECT 1 FROM qc_findings q
                WHERE q.file_id = p.file_id AND q.finding = 'low_coverage'
                  AND NOT q.resolved)                        AS bulgu_acik
FROM p JOIN core_files f ON f.file_id = p.file_id
WHERE p.coverage IS NOT NULL AND p.coverage < 1.0
ORDER BY tahmini_eksik DESC, p.coverage ASC LIMIT %(limit)s;
"""

# Kapı muhasebesi: eşiğin altındaki her dosyanın bulgusu AÇILMIŞ mı? Sayılar
# tutmuyorsa kusur belgede değil QC kapısındadır.
_SQL_COVERAGE_GATE = _P_CTE + """
SELECT count(*) FILTER (WHERE p.coverage < %(soft)s)              AS esik_alti,
       count(*) FILTER (WHERE p.coverage < %(soft)s AND EXISTS (
           SELECT 1 FROM qc_findings q WHERE q.file_id = p.file_id
            AND q.finding = 'low_coverage' AND NOT q.resolved))    AS bulgusu_acik,
       count(*) FILTER (WHERE p.coverage < 1.0)                    AS tam_olmayan,
       coalesce(sum(round((p.pages * (1 - p.coverage))::numeric))
                FILTER (WHERE p.coverage < 1.0), 0)                AS toplam_eksik_sayfa,
       coalesce(sum(p.pages), 0)                                   AS toplam_sayfa,
       count(*) FILTER (WHERE p.garbage > 0)                       AS garbage_sifirdan_buyuk,
       max(p.garbage)                                              AS garbage_max
FROM p;
"""


def _golden_names(path: str) -> list[str]:
    names: set[str] = set()
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                for ev in json.loads(line).get("gold_evidence", []) or []:
                    if ev.get("file_name"):
                        names.add(ev["file_name"])
    except OSError:
        return []
    return sorted(names)


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description="Korpus kalite kırılımı (salt-okuma)")
    ap.add_argument("--golden", default="eval/golden/v0.jsonl",
                    help="Golden JSONL (çakışma kontrolü için; yoksa §1 atlanır)")
    ap.add_argument("--dup-limit", type=int, default=15)
    ap.add_argument("--worst-limit", type=int, default=10)
    ap.add_argument("--cov-limit", type=int, default=20,
                    help="§7'de listelenecek eksik-sayfası en çok dosya sayısı")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    from ragintel.config.loader import load_config
    from ragintel.config.settings import DbSettings
    from ragintel.database import Database
    from ragintel.database.config_store import make_db_reader

    db = Database(DbSettings()).open()
    out: dict = {}
    try:
        cfg = load_config(db_reader=make_db_reader(db))
        ch = cfg.group("chunking")
        max_t, min_t = int(ch.max_tokens), int(ch.min_tokens)
        out["esikler"] = {"max_tokens": max_t, "min_tokens": min_t,
                          "table_subchunk_max_tokens": int(
                              getattr(ch, "table_subchunk_max_tokens", max_t))}

        # §7 eşikleri de DB config'ten — dev varsayılanı (0.50/0.85) H200'de
        # farklı olabilir; hangi eşikle ölçtüğümüzü raporun kendisi söylesin.
        q = cfg.group("quality")
        hard_cov = float(q.parse.hard_fail_coverage)
        soft_cov = float(q.parse.soft_flag_coverage)
        out["esikler"].update({
            "hard_fail_coverage": hard_cov,
            "soft_flag_coverage": soft_cov,
            "ocr_enabled": bool(q.ocr_fallback.enabled),
            "ocr_trigger_below": float(q.ocr_fallback.trigger_coverage_below),
        })

        with db.connection() as conn:
            cur = conn.cursor()
            names = _golden_names(args.golden)
            if names:
                rows = cur.execute(_SQL_GOLDEN_MATCH, {"names": names}).fetchall()
                out["golden_cakisma"] = {
                    "golden_dosya": len(names),
                    "korpusta_bulunan": sum(1 for _, n in rows if n),
                    "eksik": [nm for nm, n in rows if not n],
                }

            out["bulgu_kirilim"] = [
                {"finding": r[0], "bulgu": r[1], "dosya": r[2], "tablo_kokenli": r[3],
                 "dosya_duzeyi": r[4], "tok_min": r[5],
                 "tok_ort": float(r[6]) if r[6] is not None else None, "tok_max": r[7]}
                for r in cur.execute(_SQL_FINDING_BREAKDOWN).fetchall()]

            out["token_anatomi"] = [
                {"kova": r[0], "chunk": r[1], "tablo_kokenli": r[2], "dosya": r[3]}
                for r in cur.execute(_SQL_TOKEN_ANATOMY,
                                     {"max_t": max_t, "min_t": min_t}).fetchall()]

            g, etk, tot = cur.execute(_SQL_CROSS_DUP_TOPLAM).fetchone()
            out["duplicate_ozet"] = {"grup": g, "etkilenen_chunk": etk,
                                     "toplam_chunk": tot,
                                     "oran": round(etk / tot, 4) if tot else 0.0}
            out["duplicate_top"] = [
                {"chunk_adet": r[0], "dosya_adet": r[1], "token": r[2],
                 "onizleme": _clip(r[3])}
                for r in cur.execute(_SQL_CROSS_DUP,
                                     {"limit": args.dup_limit}).fetchall()]

            out["terminal_olmayan_veya_failed"] = [
                {"file_id": r[0], "file_name": r[1], "status": r[2],
                 "retry_count": r[3], "fail_reason": _clip(r[4], 200)}
                for r in cur.execute(_SQL_FAILED).fetchall()]

            out["en_dusuk_skorlu"] = [
                {"file_id": r[0], "file_name": r[1],
                 "quality_score": float(r[2]), "parse": r[3], "chunk": r[4]}
                for r in cur.execute(_SQL_WORST,
                                     {"limit": args.worst_limit}).fetchall()]

            cov_p = {"hard": hard_cov, "soft": soft_cov}
            out["coverage_hist"] = [
                {"kova": r[0], "dosya": r[1], "sayfa": r[2],
                 "tahmini_eksik_sayfa": int(r[3]), "ama_page_ratio_tam": r[4],
                 "ocr_kosmus": r[5]}
                for r in cur.execute(_SQL_COVERAGE_HIST, cov_p).fetchall()]

            out["coverage_worst"] = [
                {"file_id": r[0], "file_name": r[1], "pages": r[2],
                 "coverage": float(r[3]), "page_ratio": float(r[4]),
                 "garbage": float(r[5]) if r[5] is not None else None,
                 "ocr": r[6], "tahmini_eksik": int(r[7]), "bulgu_acik": r[8]}
                for r in cur.execute(_SQL_COVERAGE_WORST,
                                     {"limit": args.cov_limit}).fetchall()]

            gr = cur.execute(_SQL_COVERAGE_GATE, {"soft": soft_cov}).fetchone()
            out["coverage_kapi"] = {
                "esik_alti": gr[0], "bulgusu_acik": gr[1], "tam_olmayan": gr[2],
                "toplam_eksik_sayfa": int(gr[3]), "toplam_sayfa": int(gr[4]),
                "garbage_sifirdan_buyuk": gr[5],
                "garbage_max": float(gr[6]) if gr[6] is not None else None,
            }
    finally:
        db.close()

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0

    e = out["esikler"]
    print("=" * 78)
    print("KORPUS KALİTE KIRILIMI — salt-okuma")
    print("=" * 78)
    print(f"Efektif eşikler: max_tokens={e['max_tokens']} min_tokens={e['min_tokens']} "
          f"table_subchunk_max={e['table_subchunk_max_tokens']}")

    gc = out.get("golden_cakisma")
    if gc:
        print(f"\n§1 GOLDEN ↔ KORPUS ÇAKIŞMASI: {gc['korpusta_bulunan']}/{gc['golden_dosya']} "
              "golden kanıt dosyası korpusta VAR")
        if gc["eksik"]:
            print("    ⚠ Korpusta OLMAYAN golden dosyaları (quote eşlemesi bunlarda 0 döner):")
            for nm in gc["eksik"][:20]:
                print(f"      - {nm}")
            if gc["korpusta_bulunan"] == 0:
                print("    ⚠⚠ HİÇBİRİ yok → retrieval metrikleri KORPUS KUSURU DEĞİL, "
                      "bayat ölçüm aracı. Yeni korpus için golden gerekir.")

    print("\n§2 AÇIK BULGU × CHUNK TÜRÜ")
    print(f"{'finding':<24}{'bulgu':>7}{'dosya':>7}{'tablo':>7}{'tok_min':>9}"
          f"{'tok_ort':>9}{'tok_max':>9}")
    print("-" * 78)
    for r in out["bulgu_kirilim"]:
        print(f"{r['finding']:<24}{r['bulgu']:>7}{r['dosya']:>7}{r['tablo_kokenli']:>7}"
              f"{str(r['tok_min']):>9}{str(r['tok_ort']):>9}{str(r['tok_max']):>9}")

    print("\n§3 TOKEN ANATOMİSİ — 'kesik' mi 'bütçe doldu' mu?")
    print(f"{'kova':<28}{'chunk':>9}{'tablo':>9}{'dosya':>9}")
    print("-" * 78)
    for r in out["token_anatomi"]:
        print(f"{r['kova']:<28}{r['chunk']:>9}{r['tablo_kokenli']:>9}{r['dosya']:>9}")

    d = out["duplicate_ozet"]
    print(f"\n§4 DOSYALAR-ARASI DUPLICATE: {d['grup']} grup, "
          f"{d['etkilenen_chunk']}/{d['toplam_chunk']} chunk (%{d['oran']*100:.1f})")
    print(f"{'chunk':>7}{'dosya':>7}{'tok':>6}  önizleme")
    print("-" * 78)
    for r in out["duplicate_top"]:
        print(f"{r['chunk_adet']:>7}{r['dosya_adet']:>7}{str(r['token']):>6}  {r['onizleme']}")

    print("\n§5 COMPLETED OLMAYAN DOSYALAR")
    for r in out["terminal_olmayan_veya_failed"]:
        print(f"  [{r['file_id']}] {r['file_name']} · {r['status']} "
              f"· retry={r['retry_count']} · {r['fail_reason']}")

    print("\n§6 EN DÜŞÜK SKORLU DOSYALAR (alt-skor anatomisi)")
    for r in out["en_dusuk_skorlu"]:
        print(f"  {r['quality_score']:>6.2f} [{r['file_id']}] {r['file_name']}")
        print(f"         parse: {_clip(r['parse'], 200)}")
        print(f"         chunk: {_clip(r['chunk'], 200)}")

    k = out["coverage_kapi"]
    print(f"\n§7 COVERAGE ANATOMİSİ — eşikler: hard={e['hard_fail_coverage']} "
          f"soft={e['soft_flag_coverage']} · OCR={'AÇIK' if e['ocr_enabled'] else 'KAPALI'}"
          f" (tetik <{e['ocr_trigger_below']})")
    print(f"{'kova':<38}{'dosya':>7}{'sayfa':>8}{'eksik~':>8}{'pr=1.0':>8}{'ocr':>6}")
    print("-" * 78)
    for r in out["coverage_hist"]:
        print(f"{r['kova']:<38}{r['dosya']:>7}{r['sayfa']:>8}"
              f"{r['tahmini_eksik_sayfa']:>8}{r['ama_page_ratio_tam']:>8}{r['ocr_kosmus']:>6}")
    pay = (k["toplam_eksik_sayfa"] / k["toplam_sayfa"] * 100) if k["toplam_sayfa"] else 0.0
    print(f"  → tahmini metin-siz sayfa: {k['toplam_eksik_sayfa']}/{k['toplam_sayfa']} "
          f"(%{pay:.2f}) · coverage<1.0 dosya: {k['tam_olmayan']}")
    print(f"  → KAPI MUHASEBESİ: soft eşik altı {k['esik_alti']} dosya, "
          f"low_coverage bulgusu açık {k['bulgusu_acik']}"
          + ("  ✓ tutuyor" if k["esik_alti"] == k["bulgusu_acik"]
             else "  ⚠ TUTMUYOR → kusur belgede değil QC KAPISINDA"))
    print(f"  → garbage_ratio: >0 olan {k['garbage_sifirdan_buyuk']} dosya, "
          f"max={k['garbage_max']}")

    if out["coverage_worst"]:
        print("\n  En çok metin-siz SAYFA'sı olan dosyalar (coverage'a göre DEĞİL):")
        print(f"  {'sayfa':>6}{'eksik~':>8}{'cover':>8}{'p_ratio':>9}{'ocr':>5}"
              f"{'bulgu':>7}  dosya")
        print("-" * 78)
        for r in out["coverage_worst"]:
            # page_ratio > coverage ⇒ o sayfalarda TABLO/ŞEKİL var: metin yok ama
            # içerik çıkarılmış → parse_score haksız ceza yazıyor.
            flag = " ← tablo/şekil sayfası" if r["page_ratio"] > r["coverage"] else ""
            print(f"  {r['pages']:>6}{r['tahmini_eksik']:>8}{r['coverage']:>8.3f}"
                  f"{r['page_ratio']:>9.3f}{'E' if r['ocr'] else '-':>5}"
                  f"{'A' if r['bulgu_acik'] else '-':>7}  {r['file_name']}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

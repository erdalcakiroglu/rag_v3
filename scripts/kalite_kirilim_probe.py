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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

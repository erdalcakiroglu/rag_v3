#!/usr/bin/env python
"""Görev #11 ADIM-1 · golden aday-tarama probu — SALT-OKUMA (yalnız SELECT).

Amaç: kategori-koşullu rerank Adım-1 için `table_based` ve `synthesis` golden
sorularını İNSAN-onaylı taslaklamak üzere, MEVCUT korpustan aday chunk/bölüm
yüzeye çıkarır. Buradan çıkan liste → Sonnet soru+evidence-quote taslaklar →
insan onaylar (kapı) → yeni golden versiyonu. Bkz.
`docs/Brief_M11_Kategori_Kosullu_Rerank_Adim1.md`.

ÖLÇÜM-ZEMİNİ / GÜVENLİK:
  • Yalnız SELECT. DB/prod/config'e YAZMAZ, DDL yok, golden'a dokunmaz.
  • Aday olmanın ön-koşulu RETRIEVABLE olmak: chunk'ın `core_vectors`'ta karşılığı
    olmalı — yoksa A/B'de zaten çekilemez, golden'a alınamaz.
  • İki kova:
      table_based → `core_chunks.table_id IS NOT NULL` (tablo-kökenli chunk;
        core_tables.table_text önizlemesiyle). Cross-encoder'ın güçlü olduğu yer.
      synthesis  → aynı (file, section_title) altında ÇOK substantif chunk (küme);
        soru birden çok chunk'ı BİRLEŞTİRMEYİ gerektirir. Bölüm + üye chunk'lar.

Konteynerde koşulur (DB host-network):
  docker cp scripts/golden_aday_tarama_probe.py ragintel-api:/app/golden_aday_tarama_probe.py
  docker exec ragintel-api python /app/golden_aday_tarama_probe.py
  # veya JSON:
  docker exec ragintel-api python /app/golden_aday_tarama_probe.py --json > adaylar.json
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


def _clip(text: str | None, n: int = 280) -> str:
    """Önizleme: satır sonlarını boşlukla değiştir, n karaktere kırp."""
    if not text:
        return ""
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


# --- SALT-OKUMA sorgular -----------------------------------------------------

_SQL_SCOPES = """
SELECT f.doc_scope, count(DISTINCT f.file_id) AS files, count(c.chunk_id) AS chunks
FROM core_files f
LEFT JOIN core_chunks c USING (file_id)
GROUP BY f.doc_scope
ORDER BY chunks DESC;
"""

# Tablo-kökenli, retrievable chunk'lar. Dosya-başı ROW_NUMBER ile çeşitlilik
# (tek dosya listeyi kaplamasın), sonra genel limit.
_SQL_TABLE = """
WITH tbl AS (
    SELECT c.chunk_id, f.file_name, c.page_number, c.section_title,
           c.token_count, c.chunk_text, t.table_text,
           ROW_NUMBER() OVER (PARTITION BY c.file_id ORDER BY c.token_count DESC) AS rn
    FROM core_chunks c
    JOIN core_files f USING (file_id)
    LEFT JOIN core_tables t ON t.table_id = c.table_id
    WHERE f.doc_scope = ANY(%(scopes)s)
      AND c.table_id IS NOT NULL
      AND EXISTS (SELECT 1 FROM core_vectors v WHERE v.chunk_id = c.chunk_id)
)
SELECT chunk_id, file_name, page_number, section_title, token_count, chunk_text, table_text
FROM tbl
WHERE rn <= %(per_file)s
ORDER BY token_count DESC
LIMIT %(limit)s;
"""

# Sentez adayları: aynı (file, section) altında >=N substantif chunk = küme.
_SQL_SYNTH = """
SELECT c.file_id, f.file_name, c.section_title,
       count(*) AS n_chunks,
       sum(c.token_count) AS tot_tokens,
       (array_agg(c.chunk_id ORDER BY c.chunk_index))[1:%(preview)s] AS preview_ids,
       count(*) FILTER (WHERE c.table_id IS NOT NULL) AS n_table_chunks
FROM core_chunks c
JOIN core_files f USING (file_id)
WHERE f.doc_scope = ANY(%(scopes)s)
  AND c.section_title IS NOT NULL AND btrim(c.section_title) <> ''
  AND c.token_count >= %(min_tokens)s
  AND EXISTS (SELECT 1 FROM core_vectors v WHERE v.chunk_id = c.chunk_id)
GROUP BY c.file_id, f.file_name, c.section_title
HAVING count(*) >= %(min_chunks)s
ORDER BY tot_tokens DESC
LIMIT %(limit)s;
"""

_SQL_PREVIEWS = """
SELECT chunk_id, chunk_text
FROM core_chunks
WHERE chunk_id = ANY(%(ids)s);
"""


def _fetch_table(conn, scopes, per_file, limit):
    rows = conn.execute(
        _SQL_TABLE, {"scopes": scopes, "per_file": per_file, "limit": limit}
    ).fetchall()
    return [
        {
            "chunk_id": r[0], "file_name": r[1], "page": r[2], "section": r[3],
            "token_count": r[4], "text_preview": _clip(r[5]),
            "table_text_preview": _clip(r[6], 360),
        }
        for r in rows
    ]


def _fetch_synth(conn, scopes, min_tokens, min_chunks, limit, preview):
    rows = conn.execute(
        _SQL_SYNTH,
        {"scopes": scopes, "min_tokens": min_tokens, "min_chunks": min_chunks,
         "limit": limit, "preview": preview},
    ).fetchall()
    sections = [
        {
            "file_id": r[0], "file_name": r[1], "section": r[2],
            "n_chunks": r[3], "tot_tokens": r[4],
            "preview_ids": list(r[5]), "n_table_chunks": r[6],
        }
        for r in rows
    ]
    # Üye chunk önizlemelerini TEK sorguda topla.
    all_ids = sorted({cid for s in sections for cid in s["preview_ids"]})
    prev: dict[int, str] = {}
    if all_ids:
        for cid, txt in conn.execute(_SQL_PREVIEWS, {"ids": all_ids}).fetchall():
            prev[cid] = _clip(txt, 200)
    for s in sections:
        s["previews"] = [{"chunk_id": cid, "text_preview": prev.get(cid, "")}
                         for cid in s["preview_ids"]]
    return sections


def _print_human(scope_rows, table_cands, synth_cands, scopes):
    print("=== golden aday-tarama (SALT-OKUMA) ===")
    print(f"doc_scope filtresi: {scopes}\n")
    print("--- korpustaki doc_scope dağılımı ---")
    for ds, files, chunks in scope_rows:
        print(f"  {ds!r}: {files} dosya, {chunks} chunk")
    print()

    print(f"--- TABLE_BASED adayları ({len(table_cands)}) — tablo-kökenli, retrievable ---")
    for i, c in enumerate(table_cands, 1):
        print(f"[T{i:02d}] chunk_id={c['chunk_id']}  «{c['file_name']}»  "
              f"s.{c['page']}  bölüm={c['section']!r}  tok={c['token_count']}")
        if c["table_text_preview"]:
            print(f"      tablo: {c['table_text_preview']}")
        print(f"      metin: {c['text_preview']}")
    print()

    print(f"--- SYNTHESIS adayları ({len(synth_cands)}) — çok-chunk bölüm kümeleri ---")
    for i, s in enumerate(synth_cands, 1):
        tflag = f"  ({s['n_table_chunks']} tablo-chunk)" if s["n_table_chunks"] else ""
        print(f"[S{i:02d}] «{s['file_name']}»  bölüm={s['section']!r}  "
              f"{s['n_chunks']} chunk / {s['tot_tokens']} tok{tflag}")
        print(f"      üye chunk_id: {s['preview_ids']}")
        for p in s["previews"]:
            print(f"        [{p['chunk_id']}] {p['text_preview']}")
    print()
    print("NOT: bu liste TASLAK girdisidir; sorular buradan yazılıp İNSAN onayından "
          "geçmeden golden değildir (Brief §1c kapısı).")


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(prog="golden_aday_tarama_probe")
    ap.add_argument("--doc-scope", action="append", default=None,
                    help="Taranacak doc_scope (yinelenebilir; vars. ['default'])")
    ap.add_argument("--limit-table", type=int, default=40, help="table_based aday sayısı")
    ap.add_argument("--per-file", type=int, default=4, help="table_based: dosya-başı tavan (çeşitlilik)")
    ap.add_argument("--limit-synth", type=int, default=30, help="synthesis bölüm-kümesi sayısı")
    ap.add_argument("--synth-min-tokens", type=int, default=80, help="synthesis: chunk substansı eşiği")
    ap.add_argument("--synth-min-chunks", type=int, default=3, help="synthesis: bölümdeki min chunk")
    ap.add_argument("--synth-preview", type=int, default=4, help="synthesis: bölüm başı önizlenecek chunk")
    ap.add_argument("--json", action="store_true", help="Ham JSON bas (insan-metni yerine)")
    args = ap.parse_args(argv)

    scopes = args.doc_scope or ["default"]

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database

    db = Database(DbSettings()).open()
    try:
        with db.connection() as conn:
            scope_rows = conn.execute(_SQL_SCOPES).fetchall()
            table_cands = _fetch_table(conn, scopes, args.per_file, args.limit_table)
            synth_cands = _fetch_synth(
                conn, scopes, args.synth_min_tokens, args.synth_min_chunks,
                args.limit_synth, args.synth_preview,
            )
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()

    if args.json:
        print(json.dumps(
            {
                "scopes": scopes,
                "doc_scope_dagilimi": [
                    {"doc_scope": ds, "files": f, "chunks": ch} for ds, f, ch in scope_rows
                ],
                "table_based": table_cands,
                "synthesis": synth_cands,
            },
            ensure_ascii=False, indent=2,
        ))
    else:
        _print_human(scope_rows, table_cands, synth_cands, scopes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""İP-9 ingestion raporu: korpus özeti + FAZ 1 çıkış kriteri.

`build_report(db, doc_scope=None)` bir sözlük döndürür; `render_text` insan-okur
metne çevirir. doc_scope verilirse rapor o kapsamla sınırlanır (test izolasyonu);
CLI'da None = tüm korpus.
"""

from __future__ import annotations

# FAZ 1 çıkış kriteri: ≥ %95 parse başarısı.
# M-4: efektif değer `quality.parse_success_target` (DB > ENV > default). Bu sabit
# yalnızca config verilmediğinde (CLI/test) kullanılan fallback'tir.
PARSE_SUCCESS_TARGET = 0.95

# Parse başarı oranı YALNIZCA terminal durumdaki dosyalar üzerinden hesaplanır.
# PENDING/PROCESSING/RETRY/REPROCESS henüz işlenmediği için paydaya girmez —
# aksi halde yarım koşuda oran yanıltıcı düşer (yanlış ✗).
TERMINAL_STATUSES = ("COMPLETED", "FAILED")
_TERMINAL_IN = "(" + ", ".join(f"'{s}'" for s in TERMINAL_STATUSES) + ")"


def _scope(where_col: str, doc_scope):
    if doc_scope is None:
        return "", ()
    return f" AND {where_col} = %s", (doc_scope,)


def build_report(db, doc_scope: str | None = None, *, config=None) -> dict:
    """M-4: parse hedefi `quality.parse_success_target`'ten; `config=None` ise DB'den yüklenir."""
    if config is None:
        from ..config.loader import load_config
        from ..database.config_store import make_db_reader
        config = load_config(db_reader=make_db_reader(db))
    target = float(getattr(config.group("quality"), "parse_success_target", PARSE_SUCCESS_TARGET))
    with db.connection() as conn:
        cur = conn.cursor()
        s_and, s_p = _scope("doc_scope", doc_scope)
        base = f"FROM core_files WHERE true{s_and}"

        # Durum dağılımı + toplam.
        status_rows = cur.execute(
            f"SELECT status, count(*) {base} GROUP BY status;", s_p).fetchall()
        status = {st: c for st, c in status_rows}
        total = sum(status.values())

        # Terminal (COMPLETED+FAILED) vs terminal-olmayan (beklemede) ayrımı.
        terminal_total = sum(status.get(s, 0) for s in TERMINAL_STATUSES)
        pending_files = total - terminal_total

        # Parse başarısı: TERMINAL dosyalar içinde parse'ı ok olanların oranı.
        # Hem pay hem payda terminal ile sınırlı → beklemedeki dosyalar oranı bozmaz.
        parse_ok = cur.execute(
            "SELECT count(DISTINCT m.file_id) FROM metrics_ingestion m "
            "JOIN core_files f USING (file_id) "
            f"WHERE m.step='parse' AND m.ok AND f.status IN {_TERMINAL_IN}{s_and};",
            s_p).fetchone()[0]
        parse_rate = (parse_ok / terminal_total) if terminal_total else 0.0

        # Adım süre ortalamaları.
        dur_rows = cur.execute(
            "SELECT m.step, round(avg(m.duration_ms)::numeric, 1) "
            "FROM metrics_ingestion m JOIN core_files f USING (file_id) "
            f"WHERE true{s_and} GROUP BY m.step;", s_p).fetchall()
        step_avg_ms = {st: float(v) for st, v in dur_rows}

        # Chunk istatistikleri.
        chunk_stats = cur.execute(
            "SELECT count(*), round(avg(token_count)::numeric,1), "
            "percentile_cont(0.95) WITHIN GROUP (ORDER BY token_count) "
            "FROM core_chunks c JOIN core_files f USING (file_id) "
            f"WHERE true{s_and};", s_p).fetchone()
        chunks = {"count": chunk_stats[0],
                  "token_avg": float(chunk_stats[1]) if chunk_stats[1] else 0.0,
                  "token_p95": float(chunk_stats[2]) if chunk_stats[2] else 0.0}

        # Kalite dağılımı (min/ort/p95) — skorlu dosyalar.
        q = cur.execute(
            "SELECT min(quality_score), round(avg(quality_score)::numeric,2), "
            "percentile_cont(0.95) WITHIN GROUP (ORDER BY quality_score), count(*) "
            f"{base} AND quality_score IS NOT NULL;", s_p).fetchone()
        quality = {
            "min": float(q[0]) if q[0] is not None else None,
            "avg": float(q[1]) if q[1] is not None else None,
            "p95": float(q[2]) if q[2] is not None else None,
            "scored_files": q[3],
        }

        # En düşük skorlu ilk 10 dosya.
        lowest = cur.execute(
            "SELECT file_id, file_name, quality_score "
            f"{base} AND quality_score IS NOT NULL "
            "ORDER BY quality_score ASC, file_id LIMIT 10;", s_p).fetchall()
        lowest_10 = [{"file_id": r[0], "file_name": r[1], "quality_score": float(r[2])}
                     for r in lowest]

        # Açık qc_findings tip bazında.
        qc_rows = cur.execute(
            "SELECT q.finding, count(*) FROM qc_findings q "
            "JOIN core_files f USING (file_id) "
            f"WHERE NOT q.resolved{s_and} GROUP BY q.finding ORDER BY count(*) DESC;",
            s_p).fetchall()
        open_findings = {f: c for f, c in qc_rows}

        # Dosyalar ARASI duplicate (raporlanır, silinmez): >1 dosyada geçen norm.
        cross = cur.execute(
            "SELECT count(*) FROM (SELECT chunk_text_norm FROM core_chunks c "
            "JOIN core_files f USING (file_id) "
            f"WHERE true{s_and} GROUP BY chunk_text_norm "
            "HAVING count(DISTINCT c.file_id) > 1) t;", s_p).fetchone()[0]

        # COMPLETED ama skorsuz (backfill kanıtı).
        missing = cur.execute(
            f"SELECT count(*) {base} AND status='COMPLETED' AND quality_score IS NULL;",
            s_p).fetchone()[0]

        # Kurtarılan dosyalar: COMPLETED olup retry görmüş VEYA bir adım denemesi
        # ok=false olmuş (OCR fallback / retry ilk denemeyi kurtardı). Bilgi amaçlı
        # (kapı DEĞİL — parse başarısını etkilemez).
        recovered = cur.execute(
            "SELECT count(*) FROM core_files f WHERE f.status='COMPLETED'"
            " AND (f.retry_count > 0 OR EXISTS ("
            "   SELECT 1 FROM metrics_ingestion m WHERE m.file_id = f.file_id AND m.ok = false))"
            f"{s_and};", s_p).fetchone()[0]

    return {
        "total_files": total,
        "status_distribution": status,
        "terminal_files": terminal_total,       # parse oranının paydası
        "pending_files": pending_files,         # terminal olmayan (beklemede)
        "run_complete": pending_files == 0,
        "parse_success_rate": round(parse_rate, 4),
        "parse_success_target": target,
        "parse_success_met": parse_rate >= target if terminal_total else False,
        "step_avg_ms": step_avg_ms,
        "chunks": chunks,
        "quality_distribution": quality,
        "lowest_scored_10": lowest_10,
        "open_qc_findings": open_findings,
        "cross_file_duplicate_groups": cross,
        "completed_without_score": missing,
        "recovered_files": recovered,
    }


def render_text(r: dict) -> str:
    L = []
    L.append("=" * 64)
    L.append("ragintel — FAZ 1 Ingestion Raporu")
    L.append("=" * 64)
    L.append(f"Toplam dosya: {r['total_files']}")
    L.append(f"Durum dağılımı: {r['status_distribution']}")
    if not r.get("run_complete", True):
        L.append(f"⚠ Koşu tamamlanmadı: {r['pending_files']} dosya beklemede "
                 f"(terminal olmayan; parse oranına dahil DEĞİL)")
    pr = r["parse_success_rate"] * 100
    ok = "✓" if r["parse_success_met"] else "✗"
    L.append(f"Parse başarı oranı: %{pr:.1f}  (terminal {r.get('terminal_files', 0)} dosya üzerinden; "
             f"hedef ≥%{r['parse_success_target']*100:.0f}) {ok}")
    L.append("")
    L.append("Adım süre ort. (ms): " + ", ".join(
        f"{k}={v}" for k, v in sorted(r["step_avg_ms"].items())))
    c = r["chunks"]
    L.append(f"Chunk: adet={c['count']} token_ort={c['token_avg']} token_p95={c['token_p95']}")
    q = r["quality_distribution"]
    L.append(f"Kalite skoru: min={q['min']} ort={q['avg']} p95={q['p95']} "
             f"(skorlu={q['scored_files']})")
    L.append(f"Kurtarılan dosya: {r.get('recovered_files', 0)} (retry/OCR)")
    L.append(f"COMPLETED ama skorsuz: {r['completed_without_score']}")
    L.append(f"Dosyalar arası duplicate grup: {r['cross_file_duplicate_groups']}")
    L.append("")
    L.append("Açık QC bulguları: " + (
        ", ".join(f"{k}={v}" for k, v in r["open_qc_findings"].items()) or "yok"))
    L.append("")
    L.append("En düşük skorlu 10 dosya:")
    for f in r["lowest_scored_10"]:
        L.append(f"  {f['quality_score']:>6.2f}  [{f['file_id']}] {f['file_name']}")
    return "\n".join(L)

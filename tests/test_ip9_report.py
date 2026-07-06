"""İP-9 ingestion raporu (canlı DB) — Ek-A: kalite dağılımı + top-10 + parse oranı."""

from __future__ import annotations

import pytest

from ragintel.report import build_report, render_text

pytestmark = pytest.mark.db

TEST_SCOPE = "__ip9rep_test__"


def _file(db, checksum, *, status="COMPLETED", score=None, parse_ok=True):
    with db.connection() as conn:
        fid = conn.execute(
            "INSERT INTO core_files (file_name,file_type,file_size,checksum,source_path,"
            "doc_scope,status,quality_score) VALUES (%s,'pdf',10,%s,'p',%s,%s,%s) "
            "RETURNING file_id;",
            (f"{checksum}.pdf", checksum, TEST_SCOPE, status, score)).fetchone()[0]
        conn.execute(
            "INSERT INTO metrics_ingestion (file_id,step,duration_ms,ok) "
            "VALUES (%s,'parse',10,%s);", (fid, parse_ok))
    return fid


def _chunk(db, fid, index, norm):
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO core_chunks (file_id,chunk_index,chunk_text,chunk_text_norm,"
            "token_count) VALUES (%s,%s,%s,%s,50);", (fid, index, norm, norm))


@pytest.fixture(autouse=True)
def _clean(live_db):
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))
    yield
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))


def test_report_structure_and_exit_criterion(live_db):
    # 19 başarılı COMPLETED + 1 parse-FAILED -> parse başarısı %95 (>= hedef)
    for i in range(19):
        _file(live_db, f"ok{i}", score=90.0 + (i % 5))
    _file(live_db, "bad0", status="FAILED", score=None, parse_ok=False)

    r = build_report(live_db, doc_scope=TEST_SCOPE)
    assert r["total_files"] == 20
    assert r["status_distribution"]["COMPLETED"] == 19
    assert round(r["parse_success_rate"], 2) == 0.95
    assert r["parse_success_met"] is True                 # ≥ %95 çıkış kriteri
    # kalite dağılımı + top-10
    assert r["quality_distribution"]["scored_files"] == 19
    assert r["quality_distribution"]["min"] is not None
    assert 1 <= len(r["lowest_scored_10"]) <= 10
    assert r["completed_without_score"] == 0
    # render metin patlamaz
    assert "Parse başarı oranı" in render_text(r)


def test_parse_rate_denominator_is_terminal_only(live_db):
    # Yarım koşu: 8 COMPLETED(ok) + 1 FAILED(parse-fail) + 6 beklemede
    # (PENDING/PROCESSING/RETRY). Payda terminal=9 olmalı → 8/9, yanıltıcı ✗ değil.
    for i in range(8):
        _file(live_db, f"done{i}", score=90.0)
    _file(live_db, "fail0", status="FAILED", score=None, parse_ok=False)
    for i, st in enumerate(("PENDING", "PENDING", "PROCESSING", "PROCESSING", "RETRY", "REPROCESS")):
        # beklemedeki dosyalar parse metriği olsa bile paydaya/paya girmemeli
        _file(live_db, f"wait{i}", status=st, score=None, parse_ok=True)

    r = build_report(live_db, doc_scope=TEST_SCOPE)
    assert r["total_files"] == 15
    assert r["terminal_files"] == 9              # COMPLETED(8)+FAILED(1)
    assert r["pending_files"] == 6
    assert r["run_complete"] is False
    assert round(r["parse_success_rate"], 4) == round(8 / 9, 4)   # beklemedekiler oranı bozmadı
    text = render_text(r)
    assert "Koşu tamamlanmadı: 6 dosya beklemede" in text
    assert "terminal 9 dosya üzerinden" in text


def test_recovered_files_counted_not_gated(live_db):
    # 2 temiz COMPLETED + 1 retry'li COMPLETED + 1 parse-fail-sonra-ok COMPLETED
    _file(live_db, "clean0", score=90.0)
    _file(live_db, "clean1", score=90.0)
    with live_db.connection() as conn:
        # retry ile kurtarılmış: retry_count>0
        rid = conn.execute(
            "INSERT INTO core_files (file_name,file_type,file_size,checksum,source_path,"
            "doc_scope,status,quality_score,retry_count) VALUES "
            "('r.pdf','pdf',10,'rec_retry','p',%s,'COMPLETED',88.0,1) RETURNING file_id;",
            (TEST_SCOPE,)).fetchone()[0]
        conn.execute("INSERT INTO metrics_ingestion (file_id,step,duration_ms,ok) VALUES (%s,'parse',10,true);", (rid,))
    # OCR ile kurtarılmış: parse önce false sonra true, retry_count=0
    oid = _file(live_db, "rec_ocr", score=95.0, parse_ok=True)
    with live_db.connection() as conn:
        conn.execute("INSERT INTO metrics_ingestion (file_id,step,duration_ms,ok) VALUES (%s,'parse',10,false);", (oid,))

    r = build_report(live_db, doc_scope=TEST_SCOPE)
    assert r["recovered_files"] == 2                 # retry'li + OCR'li
    assert r["parse_success_met"] is True            # kurtarma kapı DEĞİL, oranı bozmaz
    assert "Kurtarılan dosya: 2 (retry/OCR)" in render_text(r)


def test_run_complete_when_all_terminal(live_db):
    for i in range(3):
        _file(live_db, f"c{i}", score=88.0)
    r = build_report(live_db, doc_scope=TEST_SCOPE)
    assert r["run_complete"] is True
    assert r["pending_files"] == 0
    assert "Koşu tamamlanmadı" not in render_text(r)


def test_cross_file_duplicate_reported_not_flagged(live_db):
    f1 = _file(live_db, "d1", score=80.0)
    f2 = _file(live_db, "d2", score=80.0)
    _chunk(live_db, f1, 0, "ayni normalize metin")
    _chunk(live_db, f2, 0, "ayni normalize metin")     # dosyalar arası aynı

    r = build_report(live_db, doc_scope=TEST_SCOPE)
    assert r["cross_file_duplicate_groups"] == 1        # raporlanır
    # ama qc_findings('duplicate_chunk') AÇILMAZ (dosya içi değil)
    with live_db.connection() as conn:
        dup = conn.execute(
            "SELECT count(*) FROM qc_findings q JOIN core_files f USING(file_id) "
            "WHERE f.doc_scope=%s AND q.finding='duplicate_chunk';",
            (TEST_SCOPE,)).fetchone()[0]
    assert dup == 0

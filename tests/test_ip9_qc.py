"""İP-9 QC konsolidasyonu + bileşik skor + backfill (canlı DB)."""

from __future__ import annotations

import pytest
from psycopg.types.json import Jsonb

from ragintel.config.loader import load_config
from ragintel.database.config_store import make_db_reader
from ragintel.ingestion.qc import QCConsolidator

pytestmark = pytest.mark.db

TEST_SCOPE = "__ip9qc_test__"


def _file(db, checksum, status="COMPLETED", score=None):
    with db.connection() as conn:
        return conn.execute(
            "INSERT INTO core_files (file_name,file_type,file_size,checksum,source_path,"
            "doc_scope,status,quality_score) VALUES ('c.pdf','pdf',10,%s,'p',%s,%s,%s) "
            "RETURNING file_id;", (checksum, TEST_SCOPE, status, score)).fetchone()[0]


def _chunk(db, fid, index, norm, tokens):
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO core_chunks (file_id,chunk_index,chunk_text,chunk_text_norm,"
            "token_count) VALUES (%s,%s,%s,%s,%s);", (fid, index, norm or "x", norm, tokens))


def _metric(db, fid, step, detail, ok=True):
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO metrics_ingestion (file_id,step,duration_ms,ok,detail) "
            "VALUES (%s,%s,1,%s,%s);", (fid, step, ok, Jsonb(detail)))


def _score(db, fid):
    with db.connection() as conn:
        s = conn.execute("SELECT quality_score FROM core_files WHERE file_id=%s;",
                         (fid,)).fetchone()[0]
    return float(s) if s is not None else None


def _findings(db, fid):
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT finding, count(*) FROM qc_findings WHERE file_id=%s GROUP BY finding;",
            (fid,)).fetchall()
    return {f: c for f, c in rows}


def _qc(db, config=None):
    return QCConsolidator(db, config=config or load_config(db_reader=make_db_reader(db)))


@pytest.fixture(autouse=True)
def _clean(live_db):
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))
    yield
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))


# --- Bileşik skor: weights × alt_skor (config'ten) ---------------------------
def test_composite_score_weighted_sum(live_db):
    fid = _file(live_db, "ip9s1")
    _metric(live_db, fid, "parse", {"parse_score": 100, "attempt": 1})
    _metric(live_db, fid, "clean", {"clean_score": 80})
    _metric(live_db, fid, "chunk", {"chunk_score": 60})
    _metric(live_db, fid, "embed", {"embed_score": 40})
    # intake skora GİRMEZ:
    _metric(live_db, fid, "intake", {"copied": True})

    score, subs = _qc(live_db).compute_and_write_score(fid)
    # 0.35*100 + 0.20*80 + 0.25*60 + 0.20*40 = 74.0
    assert score == 74.0 and _score(live_db, fid) == 74.0
    assert set(subs) == {"parse", "clean", "chunk", "embed"}


def test_score_uses_final_ocr_attempt(live_db):
    fid = _file(live_db, "ip9s2")
    _metric(live_db, fid, "parse", {"parse_score": 0, "attempt": 1})     # OCR öncesi
    _metric(live_db, fid, "parse", {"parse_score": 90, "attempt": 2})    # OCR sonrası (kabul)
    _metric(live_db, fid, "clean", {"clean_score": 100})
    _metric(live_db, fid, "chunk", {"chunk_score": 100})
    _metric(live_db, fid, "embed", {"embed_score": 100})
    score, _ = _qc(live_db).compute_and_write_score(fid)
    # parse=90 (son deneme): 0.35*90+0.20*100+0.25*100+0.20*100 = 96.5
    assert score == 96.5


def test_weights_from_config_change_behavior(live_db):
    fid = _file(live_db, "ip9s3")
    for step, sc in [("parse", 100), ("clean", 0), ("chunk", 0), ("embed", 0)]:
        _metric(live_db, fid, step, {f"{step}_score": sc, "attempt": 1})
    # parse ağırlığı 1.0, diğerleri 0 -> skor = parse_score = 100
    cfg = load_config(db_reader=lambda: {"quality": {"weights": {
        "parse": 1.0, "clean": 0.0, "chunk": 0.0, "embed": 0.0}}})
    score, _ = _qc(live_db, cfg).compute_and_write_score(fid)
    assert score == 100.0


# --- Chunk QC taramaları -----------------------------------------------------
def test_chunk_qc_flags(live_db):
    fid = _file(live_db, "ip9q1")
    _chunk(live_db, fid, 0, "yeterince uzun normal bir chunk metni burada", 100)
    _chunk(live_db, fid, 1, "", 0)                    # empty
    _chunk(live_db, fid, 2, "kisa", 5)               # too_short (<30)
    _chunk(live_db, fid, 3, "cok uzun tablo", 600)   # too_long (>512)
    _chunk(live_db, fid, 4, "tekrar eden metin", 100)
    _chunk(live_db, fid, 5, "tekrar eden metin", 100)  # duplicate (dosya içi)

    counts = _qc(live_db).run_chunk_qc(fid)
    assert counts["empty_chunk"] == 1
    assert counts["too_short"] >= 1
    assert counts["too_long"] == 1
    assert counts["duplicate_chunk"] == 1
    f = _findings(live_db, fid)
    assert f.get("duplicate_chunk") == 1 and f.get("too_long") == 1


def test_chunk_qc_idempotent(live_db):
    fid = _file(live_db, "ip9q2")
    _chunk(live_db, fid, 0, "kisa", 5)
    qc = _qc(live_db)
    qc.run_chunk_qc(fid)
    qc.run_chunk_qc(fid)     # ikinci kez -> yinelenmez (önce siler)
    assert sum(_findings(live_db, fid).values()) == 1


# --- Backfill: COMPLETED ama skorsuz = 0 -------------------------------------
def test_backfill_fills_completed_null_scores(live_db):
    fid = _file(live_db, "ip9b1", status="COMPLETED", score=None)
    for step, sc in [("parse", 100), ("clean", 100), ("chunk", 100), ("embed", 100)]:
        _metric(live_db, fid, step, {f"{step}_score": sc, "attempt": 1})
    # PENDING dosya backfill'e girmez:
    _file(live_db, "ip9b2", status="PENDING", score=None)

    result = QCConsolidator(live_db).backfill_scores()
    assert result["backfilled"] >= 1
    with live_db.connection() as conn:
        missing = conn.execute(
            "SELECT count(*) FROM core_files WHERE doc_scope=%s AND status='COMPLETED' "
            "AND quality_score IS NULL;", (TEST_SCOPE,)).fetchone()[0]
    assert missing == 0                 # kanıt: COMPLETED ama skorsuz = 0
    assert _score(live_db, fid) == 100.0

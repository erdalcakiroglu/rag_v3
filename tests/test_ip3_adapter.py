"""İP-3 CleanAdapter DB entegrasyonu (canlı DB).

Kabul: metrics_ingestion(step='clean'); retention hard-fail -> FAILED;
soft flag'ler -> qc_findings('low_retention'/'no_cleaning_effect');
eşikler config'ten (değişince davranış değişir).
"""

from __future__ import annotations

import pytest

from ragintel.config.loader import load_config
from ragintel.database.config_store import make_db_reader
from ragintel.ingestion.cleaning import CleanAdapter
from ragintel.ingestion.parsing.parsed_document import Page, ParsedDocument

pytestmark = pytest.mark.db

TEST_SCOPE = "__ip3_test__"


def _insert_file(db, checksum, name="c"):
    with db.connection() as conn:
        row = conn.execute(
            "INSERT INTO core_files (file_name,file_type,file_size,checksum,"
            "source_path,doc_scope,status) VALUES (%s,'txt',10,%s,'p',%s,'PENDING') "
            "RETURNING file_id;",
            (name, checksum, TEST_SCOPE),
        ).fetchone()
    return row[0]


def _clean_metric(db, file_id):
    with db.connection() as conn:
        return conn.execute(
            "SELECT ok, detail FROM metrics_ingestion WHERE file_id=%s AND step='clean';",
            (file_id,),
        ).fetchone()


def _findings(db, file_id):
    with db.connection() as conn:
        return [r[0] for r in conn.execute(
            "SELECT finding FROM qc_findings WHERE file_id=%s ORDER BY finding;",
            (file_id,),
        ).fetchall()]


def _status(db, file_id):
    with db.connection() as conn:
        return conn.execute(
            "SELECT status FROM core_files WHERE file_id=%s;", (file_id,)
        ).fetchone()[0]


@pytest.fixture(autouse=True)
def _clean(live_db):
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))
    yield
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))


def _adapter(db, config=None):
    cfg = config or load_config(db_reader=make_db_reader(db))
    return CleanAdapter(db, config=cfg)


def _retention_doc(keep: int, junk: int) -> ParsedDocument:
    """retention = keep/(keep+junk) olan tek-blok belge (junk=zero-width)."""
    return ParsedDocument(pages=[Page(1, ["K" * keep + "​" * junk])])


# --- metrik + no_cleaning_effect (retention 1.0) -----------------------------
def test_clean_metric_and_no_cleaning_effect(live_db):
    fid = _insert_file(live_db, "ip3clean1")
    doc = ParsedDocument(pages=[Page(1, ["Karbonvergisi"])])   # temiz -> retention 1.0
    out = _adapter(live_db).clean_file(fid, doc)

    assert out.status == "CLEANED"
    ok, detail = _clean_metric(live_db, fid)
    assert ok is True
    assert detail["retention_ratio"] == 1.0
    assert "encoding_fixes" in detail and "header_footer_removed" in detail
    assert _findings(live_db, fid) == ["no_cleaning_effect"]


# --- low_retention soft flag (0.65) ------------------------------------------
def test_low_retention_opens_qc_finding(live_db):
    fid = _insert_file(live_db, "ip3clean2")
    out = _adapter(live_db).clean_file(fid, _retention_doc(65, 35))  # retention 0.65
    assert out.status == "CLEANED"
    assert _findings(live_db, fid) == ["low_retention"]
    assert _status(live_db, fid) != "FAILED"


# --- hard fail retention < 0.60 -> FAILED ------------------------------------
def test_hard_fail_marks_failed(live_db):
    fid = _insert_file(live_db, "ip3clean3")
    out = _adapter(live_db).clean_file(fid, _retention_doc(40, 60))  # retention 0.40
    assert out.status == "FAILED"
    assert _status(live_db, fid) == "FAILED"
    ok, detail = _clean_metric(live_db, fid)
    assert ok is False


# --- eşik app_config'ten: aynı belge, eşik yükselince FAILED olur -------------
def test_threshold_from_config_changes_behavior(live_db):
    doc = _retention_doc(65, 35)   # retention 0.65

    # Gerçek app_config('quality').clean.hard_fail_retention = 0.60 -> CLEANED
    fid1 = _insert_file(live_db, "ip3cfg1")
    assert _adapter(live_db).clean_file(fid1, doc).status == "CLEANED"

    # app_config('quality') 0.70 OLSAYDI -> FAILED (DB katmanı sahte reader ile
    # simüle edilir; kodda eşik sabiti olmadığını kanıtlar).
    def fake_reader():
        return {"quality": {"clean": {"hard_fail_retention": 0.70}}}

    cfg = load_config(db_reader=fake_reader)
    fid2 = _insert_file(live_db, "ip3cfg2")
    assert _adapter(live_db, cfg).clean_file(fid2, doc).status == "FAILED"

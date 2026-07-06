"""İP-2 retrofit: coverage soft eşiğin altında (ama hard-fail değil) ->
qc_findings('low_coverage') (Ek3 enum'unda mevcut). Canlı DB."""

from __future__ import annotations

import pytest

from ragintel.config.loader import load_config
from ragintel.database.config_store import make_db_reader
from ragintel.ingestion.parsing import ParseAdapter, get_backend

pytestmark = pytest.mark.db

TEST_SCOPE = "__ip2lc_test__"


def _make_partial_pdf(path, text_pages=3, blank_pages=2):
    """coverage = text_pages/(text_pages+blank_pages) olan PDF (ASCII)."""
    import fitz
    doc = fitz.open()
    for i in range(text_pages):
        p = doc.new_page()
        p.insert_text((40, 60), f"Sayfa {i} govde metni burada.", fontsize=12)
    for _ in range(blank_pages):
        doc.new_page()      # metin yok
    doc.save(path)
    doc.close()


def _insert_file(db, path, checksum):
    with db.connection() as conn:
        return conn.execute(
            "INSERT INTO core_files (file_name,file_type,file_size,checksum,"
            "source_path,doc_scope,status) VALUES ('p.pdf','pdf',100,%s,%s,%s,'PENDING') "
            "RETURNING file_id;",
            (checksum, path, TEST_SCOPE),
        ).fetchone()[0]


@pytest.fixture(autouse=True)
def _clean(live_db):
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))
    yield
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))


def test_low_coverage_opens_qc_finding(live_db, tmp_path):
    p = str(tmp_path / "partial.pdf")
    _make_partial_pdf(p)                     # coverage 0.6: soft ama hard değil
    fid = _insert_file(live_db, p, "ip2lc1")

    cfg = load_config(db_reader=make_db_reader(live_db))
    res = ParseAdapter(live_db, config=cfg, backend=get_backend("fallback")).parse_file(fid)

    assert res.status == "PARSED"            # 0.6 >= hard_fail 0.50
    with live_db.connection() as conn:
        findings = [r[0] for r in conn.execute(
            "SELECT finding FROM qc_findings WHERE file_id=%s;", (fid,)).fetchall()]
        cov = conn.execute(
            "SELECT detail->>'coverage' FROM metrics_ingestion "
            "WHERE file_id=%s AND step='parse';", (fid,)).fetchone()[0]
    assert "low_coverage" in findings
    assert 0.5 <= float(cov) < 0.85

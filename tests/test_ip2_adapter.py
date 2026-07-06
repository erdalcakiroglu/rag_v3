"""İP-2 ParseAdapter DB entegrasyonu (canlı DB).

Kapsanan kabul kriterleri:
  - Parse süresi + uyarılar metrics_ingestion(step='parse').detail'e yazılır.
  - Sayfa/section korunur, language core_files'a yazılır.
  - (Ek-A İP-2) Taranmış PDF'te OCR fallback tetiklenir, ikinci deneme metrikleri
    AYRI kaydedilir.
  - Hard fail (coverage<eşik) -> status=FAILED; pipeline devam eder.
"""

from __future__ import annotations

import pytest

from ragintel.config.loader import load_config
from ragintel.ingestion.parsing import ParseAdapter, get_backend
from ragintel.ingestion.parsing.parsed_document import Page, ParsedDocument
from tests import _corpus_parse as cp

pytestmark = pytest.mark.db

TEST_SCOPE = "__ip2_test__"


def _insert_file(db, *, file_type, source_path, checksum, name="t"):
    with db.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO core_files
              (file_name, file_type, file_size, checksum, source_path,
               doc_scope, status)
            VALUES (%s,%s,%s,%s,%s,%s,'PENDING') RETURNING file_id;
            """,
            (name, file_type, 100, checksum, source_path, TEST_SCOPE),
        ).fetchone()
    return row[0]


def _parse_metrics(db, file_id):
    with db.connection() as conn:
        return conn.execute(
            "SELECT ok, duration_ms, detail FROM metrics_ingestion "
            "WHERE file_id=%s AND step='parse' ORDER BY (detail->>'attempt')::int;",
            (file_id,),
        ).fetchall()


def _file_row(db, file_id):
    with db.connection() as conn:
        return conn.execute(
            "SELECT status, language, fail_reason FROM core_files WHERE file_id=%s;",
            (file_id,),
        ).fetchone()


@pytest.fixture(autouse=True)
def _clean(live_db):
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))
    yield
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))


def _adapter(live_db, backend):
    return ParseAdapter(live_db, config=load_config(db_reader=make_reader(live_db)),
                        backend=backend)


def make_reader(db):
    from ragintel.database.config_store import make_db_reader
    return make_db_reader(db)


# --- Parse metriği + language (fallback, gerçek txt) -------------------------
def test_parse_writes_metric_and_language(live_db, tmp_path):
    p = str(tmp_path / "a.txt")
    cp.make_txt(p)
    fid = _insert_file(live_db, file_type="txt", source_path=p, checksum="ip2txt1")

    res = _adapter(live_db, get_backend("fallback")).parse_file(fid)
    assert res.status == "PARSED"

    metrics = _parse_metrics(live_db, fid)
    assert len(metrics) == 1
    ok, dur, detail = metrics[0]
    assert ok is True and dur >= 0
    assert detail["attempt"] == 1 and detail["ocr"] is False
    assert "coverage" in detail and "warnings" in detail
    assert detail["backend"] == "office"

    status, language, _ = _file_row(live_db, fid)
    assert language == "tr"
    assert status != "FAILED"


# --- Section + page korunur, parse metriği (gerçek pdf) ----------------------
def test_pdf_sections_pages_persisted_metric(live_db, tmp_path):
    p = str(tmp_path / "r.pdf")
    cp.make_text_pdf(p, title="Bolum Bir")
    fid = _insert_file(live_db, file_type="pdf", source_path=p, checksum="ip2pdf1")

    res = _adapter(live_db, get_backend("fallback")).parse_file(fid)
    assert res.status == "PARSED"
    assert res.parsed.page_count == 1
    assert any(s.title.startswith("Bolum Bir") for s in res.parsed.sections)
    assert _parse_metrics(live_db, fid)[0][2]["section_count"] >= 1


# --- OCR fallback: taranmış pdf -> 2 ayrı metrik, hard-fail FAILED -----------
def test_scanned_pdf_ocr_fallback_two_metrics_and_failed(live_db, tmp_path):
    p = str(tmp_path / "scan.pdf")
    cp.make_scanned_pdf(p)
    fid = _insert_file(live_db, file_type="pdf", source_path=p, checksum="ip2scan1")

    res = _adapter(live_db, get_backend("fallback")).parse_file(fid)

    metrics = _parse_metrics(live_db, fid)
    assert len(metrics) == 2                      # deneme 1 + OCR denemesi
    assert metrics[0][2]["attempt"] == 1 and metrics[0][2]["ocr"] is False
    assert metrics[1][2]["attempt"] == 2 and metrics[1][2]["ocr"] is True
    assert metrics[0][2]["coverage"] == 0.0

    status, _, fail_reason = _file_row(live_db, fid)
    assert status == "FAILED" and fail_reason      # coverage 0 -> hard fail
    assert res.status == "FAILED"


# --- OCR fallback happy-path: ikinci deneme coverage'ı düzeltir (stub) -------
class _StubBackend:
    name = "stub"

    def supports(self, ft):
        return ft in ("pdf", "docx")

    def parse(self, path, file_type, *, ocr=False):
        if ocr:
            from ragintel.ingestion.parsing.text_utils import detect_language
            txt = "OCR ile çıkarılan tam Türkçe metin buradadır."
            return ParsedDocument(pages=[Page(1, [txt])],
                                  language=detect_language(txt))
        return ParsedDocument(pages=[Page(1, [])])   # metin yok -> coverage 0


def test_ocr_fallback_second_attempt_accepted(live_db, tmp_path):
    fid = _insert_file(live_db, file_type="pdf", source_path="ignored",
                       checksum="ip2stub1")

    res = _adapter(live_db, _StubBackend()).parse_file(fid)

    metrics = _parse_metrics(live_db, fid)
    assert len(metrics) == 2
    assert metrics[0][2]["coverage"] == 0.0        # deneme 1
    assert metrics[1][2]["coverage"] == 1.0        # OCR denemesi düzeltti
    assert res.status == "PARSED"                  # ikinci sonuç kabul edildi
    status, language, _ = _file_row(live_db, fid)
    assert status != "FAILED" and language == "tr"

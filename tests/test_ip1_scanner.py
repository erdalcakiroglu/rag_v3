"""İP-1 Folder Scanner kabul kriterleri (canlı DB).

4 zorunlu senaryo: (1) 100 karışık dosya, (2) duplicate re-scan no-op,
(3) doc_version artışı, (4) her dosya için intake metriği. + oversize FAILED.

İzolasyon: tüm kayıtlar TEST_SCOPE doc_scope'una yazılır ve her testte
temizlenir (CASCADE metrikleri de siler). 'intake' step kısıtı yoksa (Ek2
uygulanmadıysa) testler net mesajla atlanır.
"""

from __future__ import annotations

import pytest

from ragintel.ingestion import FolderScanner, Outcome
from tests import _corpus

pytestmark = pytest.mark.db

TEST_SCOPE = "__ip1_test__"


def _delete_scope(db) -> None:
    with db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope = %s;", (TEST_SCOPE,))


def _count_files(db) -> int:
    with db.connection() as conn:
        return conn.execute(
            "SELECT count(*) FROM core_files WHERE doc_scope = %s;", (TEST_SCOPE,)
        ).fetchone()[0]


def _count_intake_metrics(db) -> int:
    with db.connection() as conn:
        return conn.execute(
            """
            SELECT count(*) FROM metrics_ingestion m
            JOIN core_files f USING (file_id)
            WHERE f.doc_scope = %s AND m.step = 'intake';
            """,
            (TEST_SCOPE,),
        ).fetchone()[0]


@pytest.fixture
def intake_enabled(live_db):
    with live_db.connection() as conn:
        row = conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'metrics_ingestion_step_check';"
        ).fetchone()
    if not row or "intake" not in row[0]:
        pytest.skip("Ek2 (intake) migration uygulanmamış — "
                    "docs/FAZ1_Sema_Ek2_Intake.sql")


@pytest.fixture(autouse=True)
def _clean(live_db, intake_enabled):
    _delete_scope(live_db)
    yield
    _delete_scope(live_db)


def _scanner(live_db, tmp_path):
    return FolderScanner(
        live_db,
        storage_root=str(tmp_path / "storage"),
        upload_user="pytest",
    )


# --- Kriter 1: 100 karışık dosya ---------------------------------------------
def test_mixed_100_files(live_db, tmp_path):
    expected = _corpus.build_mixed_corpus(str(tmp_path / "corpus"))
    assert expected["total"] == 100

    report = _scanner(live_db, tmp_path).scan(
        str(tmp_path / "corpus"), doc_scope=TEST_SCOPE
    )

    assert report.total == 100
    assert report.count(Outcome.CREATED) == expected["created"]   # 65
    assert report.count(Outcome.SKIPPED) == expected["skipped"]   # 25
    assert report.count(Outcome.REJECTED) == expected["rejected"] # 10

    # Geçerliler PENDING, yanlış uzantılı içerik doğru tiple (pdf).
    with live_db.connection() as conn:
        pending = conn.execute(
            "SELECT count(*) FROM core_files WHERE doc_scope=%s AND status='PENDING';",
            (TEST_SCOPE,),
        ).fetchone()[0]
        mislabeled_types = conn.execute(
            "SELECT DISTINCT file_type FROM core_files "
            "WHERE doc_scope=%s AND file_name LIKE 'mislabeled_%%';",
            (TEST_SCOPE,),
        ).fetchall()
    assert pending == expected["created"]
    assert mislabeled_types == [("pdf",)]   # uzantı .docx ama içerik pdf


# --- Kriter 2: duplicate / idempotent re-scan no-op --------------------------
def test_rescan_is_noop(live_db, tmp_path):
    root = str(tmp_path / "corpus")
    import os
    os.makedirs(root)
    _corpus.make_txt(os.path.join(root, "a.txt"), "içerik A")
    _corpus.make_pdf(os.path.join(root, "b.pdf"))
    _corpus.make_docx(os.path.join(root, "c.docx"))

    scanner = _scanner(live_db, tmp_path)

    r1 = scanner.scan(root, doc_scope=TEST_SCOPE)
    assert r1.count(Outcome.CREATED) == 3
    files_after_1 = _count_files(live_db)
    metrics_after_1 = _count_intake_metrics(live_db)
    assert files_after_1 == 3 and metrics_after_1 == 3

    # İkinci tarama: hiçbir yeni kayıt/metrik açılmaz.
    r2 = scanner.scan(root, doc_scope=TEST_SCOPE)
    assert r2.count(Outcome.CREATED) == 0
    assert r2.count(Outcome.SKIPPED) == 3
    assert _count_files(live_db) == 3
    assert _count_intake_metrics(live_db) == 3


# --- Kriter 3: aynı isim farklı içerik -> doc_version=2 -----------------------
def test_changed_content_bumps_doc_version(live_db, tmp_path):
    import os
    root = str(tmp_path / "corpus")
    os.makedirs(root)
    p = os.path.join(root, "report.txt")

    _corpus.make_txt(p, "sürüm bir içeriği")
    r1 = _scanner(live_db, tmp_path).scan(root, doc_scope=TEST_SCOPE)
    assert r1.count(Outcome.CREATED) == 1

    # Aynı isim, değişmiş içerik.
    _corpus.make_txt(p, "sürüm İKİ farklı içeriği")
    r2 = _scanner(live_db, tmp_path).scan(root, doc_scope=TEST_SCOPE)
    assert r2.count(Outcome.VERSIONED) == 1

    with live_db.connection() as conn:
        versions = conn.execute(
            "SELECT doc_version FROM core_files "
            "WHERE doc_scope=%s AND file_name='report.txt' ORDER BY doc_version;",
            (TEST_SCOPE,),
        ).fetchall()
    assert [v[0] for v in versions] == [1, 2]


# --- Kriter 4: her oluşturulan dosya için intake metriği ----------------------
def test_metric_written_per_file(live_db, tmp_path):
    import os
    root = str(tmp_path / "corpus")
    os.makedirs(root)
    for i in range(5):
        _corpus.make_txt(os.path.join(root, f"m_{i}.txt"), f"metrik içerik {i}")

    _scanner(live_db, tmp_path).scan(root, doc_scope=TEST_SCOPE)

    with live_db.connection() as conn:
        rows = conn.execute(
            """
            SELECT m.step, m.ok, m.duration_ms, m.detail
            FROM metrics_ingestion m JOIN core_files f USING (file_id)
            WHERE f.doc_scope = %s;
            """,
            (TEST_SCOPE,),
        ).fetchall()
    assert len(rows) == 5
    for step, ok, dur, detail in rows:
        assert step == "intake"
        assert ok is True
        assert dur >= 0
        assert detail["action"] == "PENDING"
        # tip tespiti sonuçları (içerik-tabanlı)
        assert detail["file_type"] == "txt"
        assert detail["detected_mime"].startswith("text/")
        # dedup / sürümleme bağlamı
        assert detail["prior_versions"] == 0
        assert detail["is_version_bump"] is False
        assert "checksum_prefix" in detail


# --- Ek: boyut limiti aşan dosya FAILED --------------------------------------
def test_oversize_file_marked_failed(live_db, tmp_path):
    import os
    root = str(tmp_path / "corpus")
    os.makedirs(root)
    _corpus.make_txt(os.path.join(root, "big.txt"), "x" * 5000)

    scanner = _scanner(live_db, tmp_path)
    scanner.max_bytes = 100  # limiti düşür (config yerine test override)

    report = scanner.scan(root, doc_scope=TEST_SCOPE)
    assert report.count(Outcome.FAILED) == 1

    with live_db.connection() as conn:
        row = conn.execute(
            "SELECT status, fail_reason FROM core_files "
            "WHERE doc_scope=%s AND file_name='big.txt';",
            (TEST_SCOPE,),
        ).fetchone()
        metric_ok = conn.execute(
            "SELECT m.ok FROM metrics_ingestion m JOIN core_files f USING (file_id) "
            "WHERE f.doc_scope=%s AND m.step='intake';",
            (TEST_SCOPE,),
        ).fetchone()
    assert row[0] == "FAILED" and row[1] is not None
    assert metric_ok[0] is False

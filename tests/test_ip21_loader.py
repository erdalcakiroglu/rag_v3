"""İP-2.1a loader: evidence doğrulaması, idempotent yükleme ve mismatch raporu."""

from __future__ import annotations

import pytest

from ragintel.eval import EvidenceValidationError, load_golden_set
from ragintel.eval import repository as eval_repo
from ragintel.text import normalize_for_quote

pytestmark = pytest.mark.db

TEST_SCOPE = "__golden_test__"
SET_VERSION = "v1-sample"


def _insert_file(db, file_name: str, checksum: str) -> int:
    with db.connection() as conn:
        return conn.execute(
            "INSERT INTO core_files (file_name,file_type,file_size,checksum,source_path,doc_scope,status) "
            "VALUES (%s,%s,10,%s,'fixture',%s,'COMPLETED') RETURNING file_id;",
            (file_name, file_name.rsplit(".", 1)[-1], checksum, TEST_SCOPE),
        ).fetchone()[0]


def _insert_chunk(db, file_id: int, chunk_index: int, text: str, *, page=None, sheet=None):
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO core_chunks (file_id,chunk_index,chunk_text,chunk_text_norm,token_count,page_number,sheet_name) "
            "VALUES (%s,%s,%s,%s,20,%s,%s);",
            (file_id, chunk_index, text, normalize_for_quote(text), page, sheet),
        )


def _seed_corpus(db):
    policy = _insert_file(db, "policy.pdf", "gold-policy")
    _insert_chunk(db, policy, 0, "Karbon vergisi 1 Ocak 2026 tarihinde yururluge girer.", page=1)

    handbook = _insert_file(db, "handbook.pdf", "gold-handbook")
    _insert_chunk(db, handbook, 0, "Izin talebi once yonetici onayina sunulur.", page=2)
    _insert_chunk(db, handbook, 1, "Kesinlestirme icin IK operasyon onayi zorunludur.", page=3)

    finance = _insert_file(db, "finance.xlsx", "gold-finance")
    _insert_chunk(db, finance, 0, "Operasyon | B-42 | 2026 butce merkezi", sheet="Butce")

    onboarding = _insert_file(db, "onboarding.txt", "gold-onboarding")
    _insert_chunk(db, onboarding, 0, "Yeni personel varsayilan olarak Operasyon birimine atanir.", page=1)


@pytest.fixture(autouse=True)
def _clean(live_db):
    with live_db.connection() as conn:
        if not eval_repo.eval_schema_ready(conn):
            pytest.skip("eval_golden_* tabloları yok; docs/FAZ2_Sema.sql elle uygulanmalı")
        conn.execute("DELETE FROM eval_golden_sets WHERE set_version = %s;", (SET_VERSION,))
        conn.execute("DELETE FROM core_files WHERE doc_scope = %s;", (TEST_SCOPE,))
    yield
    with live_db.connection() as conn:
        conn.execute("DELETE FROM eval_golden_sets WHERE set_version = %s;", (SET_VERSION,))
        conn.execute("DELETE FROM core_files WHERE doc_scope = %s;", (TEST_SCOPE,))


def test_loader_end_to_end_and_idempotent(live_db):
    _seed_corpus(live_db)

    first = load_golden_set(live_db, "eval/golden/v1.sample.jsonl", set_version=SET_VERSION)
    assert first.no_op is False
    assert first.inserted == 5
    assert first.skipped == 0

    with live_db.connection() as conn:
        assert eval_repo.count_set_records(conn, SET_VERSION) == 5

    second = load_golden_set(live_db, "eval/golden/v1.sample.jsonl", set_version=SET_VERSION)
    assert second.no_op is True
    assert second.inserted == 0
    assert second.skipped == 5

    with live_db.connection() as conn:
        assert eval_repo.count_set_records(conn, SET_VERSION) == 5


def test_loader_reports_unmatched_evidence(live_db, tmp_path):
    _seed_corpus(live_db)
    broken = tmp_path / "broken.jsonl"
    broken.write_text(
        '{"id":"b1","question":"Yanlis kanit","ideal_answer":"x","category":"single_fact",'
        '"difficulty":1,"gold_evidence":[{"file_name":"policy.pdf","page":1,"quote":"olmayan alinti"}],'
        '"doc_scope":"__golden_test__","answerable":true,"created_by":"fixture","notes":""}',
        encoding="utf-8",
    )

    with pytest.raises(EvidenceValidationError) as exc:
        load_golden_set(live_db, broken, set_version=SET_VERSION)

    assert len(exc.value.mismatches) == 1
    mismatch = exc.value.mismatches[0]
    assert mismatch.reason == "quote_not_found"
    assert mismatch.file_name == "policy.pdf"

    with live_db.connection() as conn:
        assert eval_repo.count_set_records(conn, SET_VERSION) == 0

"""İP-7 EmbeddingService DB entegrasyonu (canlı DB, mock Ollama).

Kabul: metrics_ingestion(step='embed').detail; embed_failed/embed_anomaly ->
qc_findings; Ollama erişilemezken dosya FAILED OLMAZ (pipeline durur).
"""

from __future__ import annotations

import pytest

from ragintel.config.loader import load_config
from ragintel.database.config_store import make_db_reader
from ragintel.ingestion.chunking.chunk import Chunk
from ragintel.ingestion.embedding import (
    EmbeddingBackendError,
    EmbeddingService,
    OllamaEmbedder,
)
from tests import _ollama_mock as om

pytestmark = pytest.mark.db

TEST_SCOPE = "__ip7_test__"
DIM = om.DIM


def _insert_file(db, checksum):
    with db.connection() as conn:
        return conn.execute(
            "INSERT INTO core_files (file_name,file_type,file_size,checksum,"
            "source_path,doc_scope,status) VALUES ('c.pdf','pdf',10,%s,'p',%s,'PENDING') "
            "RETURNING file_id;",
            (checksum, TEST_SCOPE),
        ).fetchone()[0]


def _embed_metric(db, fid):
    with db.connection() as conn:
        return conn.execute(
            "SELECT ok, detail FROM metrics_ingestion WHERE file_id=%s AND step='embed';",
            (fid,)).fetchone()


def _findings(db, fid):
    with db.connection() as conn:
        return [r[0] for r in conn.execute(
            "SELECT finding FROM qc_findings WHERE file_id=%s;", (fid,)).fetchall()]


def _status(db, fid):
    with db.connection() as conn:
        return conn.execute("SELECT status FROM core_files WHERE file_id=%s;",
                            (fid,)).fetchone()[0]


@pytest.fixture(autouse=True)
def _clean(live_db):
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))
    yield
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))


def _cfg(db, batch_size=8):
    # Gerçek quality (Ek1 DB) + embedding batch override (fake reader ile birlikte
    # gerçek DB katmanını taklit et: sadece embedding'i değiştiriyoruz).
    base = make_db_reader(db)

    def reader():
        groups = dict(base())
        groups["embedding"] = {"model": "BAAI/bge-m3", "dim": DIM,
                               "batch_size": batch_size, "normalize": True}
        return groups
    return load_config(db_reader=reader)


def _svc(db, handler, **kw):
    emb = OllamaEmbedder("http://mock", client=om.make_client(handler))
    return EmbeddingService(db, config=_cfg(db), embedder=emb,
                            backoff_base=0.0, sleep=lambda s: None, **kw)


def _chunks(n):
    return [Chunk(i, f"metin {i}", f"metin {i}", 5) for i in range(n)]


def test_embed_file_writes_metric(live_db):
    fid = _insert_file(live_db, "ip7e1")
    res = _svc(live_db, om.ok_handler()).embed_file(fid, _chunks(5))
    assert res.metrics["embedded"] == 5

    ok, detail = _embed_metric(live_db, fid)
    assert ok is True
    assert detail["model_name"] == "bge-m3@ollama"
    assert detail["dim"] == DIM
    assert abs(detail["mean_norm"] - 1.0) < 1e-6
    assert "request_count" in detail and "batch_halvings" in detail


def test_embed_failed_opens_qc_finding(live_db):
    fid = _insert_file(live_db, "ip7e2")
    _svc(live_db, om.ok_handler(bad_texts={"metin 2"})).embed_file(fid, _chunks(4))
    ok, detail = _embed_metric(live_db, fid)
    assert ok is False and detail["failed"] == 1
    assert "embed_failed" in _findings(live_db, fid)


def test_embed_anomaly_opens_qc_finding(live_db):
    fid = _insert_file(live_db, "ip7e3")
    _svc(live_db, om.ok_handler(identical=True)).embed_file(fid, _chunks(5))
    assert "embed_anomaly" in _findings(live_db, fid)


def test_unreachable_does_not_fail_file(live_db):
    fid = _insert_file(live_db, "ip7e4")
    with pytest.raises(EmbeddingBackendError):
        _svc(live_db, om.unreachable_handler()).embed_file(fid, _chunks(4))
    # Altyapı hatası dosya hatası değildir: FAILED OLMAZ.
    assert _status(live_db, fid) == "PENDING"
    assert _embed_metric(live_db, fid) is None       # metrik yazılmadı

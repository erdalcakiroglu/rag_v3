"""İP-8 StorageWriter — transaksiyonel yazım (canlı DB, sentetik vektör).

Kabul: atomiklik (hata -> rollback, yarım dosya yok), REPROCESS tutarlılığı
(öksüz vektör sorgusuyla kanıt), model_name damgası, COMPLETED/FAILED.
"""

from __future__ import annotations

import datetime
import json

import pytest

from ragintel.config.loader import load_config
from ragintel.database import storage_repo as repo
from ragintel.database.config_store import make_db_reader
from ragintel.ingestion.chunking.chunk import Chunk
from ragintel.ingestion.embedding import EmbeddedChunk, EmbedResult
from ragintel.ingestion.parsing.parsed_document import Figure, Table
from ragintel.ingestion.persistence import StorageWriter, StorageWriteError

pytestmark = pytest.mark.db

TEST_SCOPE = "__ip8_test__"
DIM = 1024


def _insert_file(db, checksum):
    with db.connection() as conn:
        return conn.execute(
            "INSERT INTO core_files (file_name,file_type,file_size,checksum,"
            "source_path,doc_scope,status) VALUES ('c.pdf','pdf',10,%s,'p',%s,'PENDING') "
            "RETURNING file_id;",
            (checksum, TEST_SCOPE),
        ).fetchone()[0]


def _result(file_id, n, *, fail_index=None, dim=DIM, tag="A"):
    items = []
    for i in range(n):
        vec = None if i == fail_index else [0.1] * dim
        items.append(EmbeddedChunk(
            Chunk(i, f"{tag} chunk {i}", f"{tag} chunk {i}", 5,
                  page_number=1, section_title="S", char_start=i, char_end=i + 1),
            vec))
    return EmbedResult(file_id, items, {}, [], "bge-m3@ollama")


def _writer(db):
    return StorageWriter(db, config=load_config(db_reader=make_db_reader(db)))


def _counts(db, file_id):
    with db.connection() as conn:
        ch = conn.execute("SELECT count(*) FROM core_chunks WHERE file_id=%s;",
                          (file_id,)).fetchone()[0]
        vec = conn.execute(
            "SELECT count(*) FROM core_vectors v JOIN core_chunks c USING(chunk_id) "
            "WHERE c.file_id=%s;", (file_id,)).fetchone()[0]
        st = conn.execute("SELECT status FROM core_files WHERE file_id=%s;",
                          (file_id,)).fetchone()[0]
    return ch, vec, st


@pytest.fixture(autouse=True)
def _clean(live_db):
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))
    yield
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))


def test_write_all_and_completed(live_db):
    fid = _insert_file(live_db, "ip8w1")
    res = _writer(live_db).write_file(
        fid, _result(fid, 5, fail_index=2),
        tables=[Table(0, [["a", "b"]], "a | b", page_no=1)],
        figures=[Figure(0, page_no=1, caption="cap")])

    assert res.status == "COMPLETED"
    assert res.chunks == 5 and res.vectors == 4   # 1 embed_failed -> vektör yok
    assert res.tables == 1 and res.figures == 1
    ch, vec, st = _counts(live_db, fid)
    assert (ch, vec, st) == (5, 4, "COMPLETED")

    with live_db.connection() as conn:
        models = [r[0] for r in conn.execute(
            "SELECT DISTINCT model_name FROM core_vectors v JOIN core_chunks c "
            "USING(chunk_id) WHERE c.file_id=%s;", (fid,)).fetchall()]
    assert models == ["bge-m3@ollama"]


def test_write_table_with_datetime_cell_no_crash(live_db):
    # Regresyon: XLSX tarih hücresi (datetime) core_tables JSONB'de crash etmemeli.
    fid = _insert_file(live_db, "ip8dt")
    res = _writer(live_db).write_file(
        fid, _result(fid, 1),
        tables=[Table(0, [["Server", "LastPatch"], ["ggb-01", datetime.datetime(2026, 7, 5, 1, 0, 17)]],
                      "Server | LastPatch\nggb-01 | 2026-07-05", sheet_name="Envanter")],
        figures=[])
    assert res.status == "COMPLETED" and res.tables == 1
    with live_db.connection() as conn:
        td = conn.execute("SELECT table_data FROM core_tables WHERE file_id=%s;", (fid,)).fetchone()[0]
    assert "2026-07-05T01:00:17" in json.dumps(td)   # datetime ISO string olarak yazıldı


def test_reprocess_renews_ids_no_orphans(live_db):
    fid = _insert_file(live_db, "ip8w2")
    w = _writer(live_db)
    w.write_file(fid, _result(fid, 5, tag="OLD"))
    with live_db.connection() as conn:
        old_ids = [r[0] for r in conn.execute(
            "SELECT chunk_id FROM core_chunks WHERE file_id=%s;", (fid,)).fetchall()]

    w.write_file(fid, _result(fid, 3, tag="NEW"))   # REPROCESS
    with live_db.connection() as conn:
        new_ids = [r[0] for r in conn.execute(
            "SELECT chunk_id FROM core_chunks WHERE file_id=%s;", (fid,)).fetchall()]
        orphans = repo.count_orphan_vectors(conn)

    ch, vec, st = _counts(live_db, fid)
    assert ch == 3 and vec == 3 and st == "COMPLETED"
    assert set(old_ids).isdisjoint(new_ids)         # chunk_id'ler yenilendi
    assert orphans == 0                              # öksüz vektör yok


def test_rollback_on_error_no_half_file(live_db):
    """Yanlış boyut vektör -> COPY hatası -> rollback (kill -9 eşdeğeri: yarım yok)."""
    fid = _insert_file(live_db, "ip8w3")
    bad = _result(fid, 4)
    bad.items[1].vector = [0.1] * 1023              # 1023 boyut -> DB hata

    with pytest.raises(StorageWriteError):
        _writer(live_db).write_file(fid, bad)

    ch, vec, st = _counts(live_db, fid)
    assert ch == 0 and vec == 0                     # hiçbir şey kalıcı olmadı
    assert st == "FAILED"                            # ya COMPLETED ya eski/FAILED


def test_reprocess_after_failure_recovers(live_db):
    """Başarısız yazımdan sonra REPROCESS temiz COMPLETED üretir (yarım kalmaz)."""
    fid = _insert_file(live_db, "ip8w4")
    w = _writer(live_db)
    bad = _result(fid, 3)
    bad.items[0].vector = [0.1] * 1000
    with pytest.raises(StorageWriteError):
        w.write_file(fid, bad)
    assert _counts(live_db, fid)[0] == 0

    w.write_file(fid, _result(fid, 3))              # temiz tekrar
    assert _counts(live_db, fid) == (3, 3, "COMPLETED")


def test_bulk_reindex_drop_recreate(live_db):
    def index_exists():
        with live_db.connection() as conn:
            return conn.execute(
                "SELECT 1 FROM pg_indexes WHERE schemaname='ragintel' "
                "AND indexname='idx_core_vectors_hnsw';").fetchone() is not None

    w = StorageWriter(live_db, config=load_config(db_reader=make_db_reader(live_db)),
                      hnsw_bulk_reindex=True)
    assert index_exists()
    with w.bulk_load():
        assert not index_exists()                   # toplu yük için drop edildi
    assert index_exists()                            # çıkışta yeniden oluşturuldu

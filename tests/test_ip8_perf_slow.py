"""İP-8 performans: 10k chunk yazımı < 60 sn (COPY yolu). YAVAŞ + DB.

`pytest -m slow`. Sentetik 1024-boyut vektör (Ollama gerekmez). Kayıtlar
sonda temizlenir.
"""

from __future__ import annotations

import time

import pytest

from ragintel.config.loader import load_config
from ragintel.database.config_store import make_db_reader
from ragintel.ingestion.chunking.chunk import Chunk
from ragintel.ingestion.embedding import EmbeddedChunk, EmbedResult
from ragintel.ingestion.persistence import StorageWriter

pytestmark = [pytest.mark.slow, pytest.mark.db]

TEST_SCOPE = "__ip8_perf__"
DIM = 1024
N = 10_000


def test_10k_chunks_under_60s(live_db):
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))
        fid = conn.execute(
            "INSERT INTO core_files (file_name,file_type,file_size,checksum,"
            "source_path,doc_scope,status) VALUES ('big.pdf','pdf',10,'ip8perf','p',"
            "%s,'PENDING') RETURNING file_id;", (TEST_SCOPE,)).fetchone()[0]
    try:
        vec = [0.0123] * DIM
        items = [EmbeddedChunk(
            Chunk(i, f"chunk metni {i}", f"chunk metni {i}", 10, page_number=1),
            list(vec)) for i in range(N)]
        result = EmbedResult(fid, items, {}, [], "bge-m3@ollama")

        writer = StorageWriter(live_db, config=load_config(db_reader=make_db_reader(live_db)))
        t0 = time.perf_counter()
        res = writer.write_file(fid, result)
        elapsed = time.perf_counter() - t0

        assert res.status == "COMPLETED"
        assert res.chunks == N and res.vectors == N
        assert elapsed < 60.0, f"10k yazım {elapsed:.1f}s (>60s)"
        with live_db.connection() as conn:
            n = conn.execute(
                "SELECT count(*) FROM core_vectors v JOIN core_chunks c USING(chunk_id) "
                "WHERE c.file_id=%s;", (fid,)).fetchone()[0]
        assert n == N
    finally:
        with live_db.connection() as conn:
            conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))

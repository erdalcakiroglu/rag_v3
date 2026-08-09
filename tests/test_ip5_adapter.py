"""İP-5 ChunkAdapter DB entegrasyonu (canlı DB).

Kabul: metrics_ingestion(step='chunk') (token dağılımı/alt skor); truncated_ratio
> eşik -> qc_findings('chunk_truncation_high'); eşik/limit config'ten (fake
app_config ile davranış değişir). İP-6: metadata_fill detail'de.
"""

from __future__ import annotations

import pytest

from ragintel.config.loader import load_config
from ragintel.ingestion.chunking import ChunkAdapter, WordTokenCounter
from ragintel.ingestion.parsing.parsed_document import Page, ParsedDocument, Section, Table
from ragintel.ingestion.parsing.text_utils import flatten_table

pytestmark = pytest.mark.db

TEST_SCOPE = "__ip5_test__"


def _insert_file(db, checksum):
    with db.connection() as conn:
        return conn.execute(
            "INSERT INTO core_files (file_name,file_type,file_size,checksum,"
            "source_path,doc_scope,status) VALUES ('c.pdf','pdf',10,%s,'p',%s,'PENDING') "
            "RETURNING file_id;",
            (checksum, TEST_SCOPE),
        ).fetchone()[0]


def _chunk_metric(db, file_id):
    with db.connection() as conn:
        return conn.execute(
            "SELECT ok, detail FROM metrics_ingestion WHERE file_id=%s AND step='chunk';",
            (file_id,),
        ).fetchone()


def _findings(db, file_id):
    with db.connection() as conn:
        return [r[0] for r in conn.execute(
            "SELECT finding FROM qc_findings WHERE file_id=%s;", (file_id,)).fetchall()]


@pytest.fixture(autouse=True)
def _clean(live_db):
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))
    yield
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE doc_scope=%s;", (TEST_SCOPE,))


def _long_doc():
    words = " ".join(f"w{i}" for i in range(120))
    return ParsedDocument(pages=[Page(1, ["Bölüm", words])],
                          sections=[Section("Bölüm", 1, 1)])


def _table_doc():
    """Satır-grubu bütçeye sığmayan tablo: her (başlık + tek satır) 10 token
    ('a | b | c' → '|' de token), alt-chunk bütçesi 8 → daha küçüğe bölünemez."""
    rows = [["bas1", "bas2", "bas3"], ["r1a", "r1b", "r1c"],
            ["r2a", "r2b", "r2c"], ["r3a", "r3b", "r3c"]]
    return ParsedDocument(
        pages=[Page(1, ["Bölüm"])],
        sections=[Section("Bölüm", 1, 1)],
        tables=[Table(index=0, data=rows, flattened_text=flatten_table(rows), page_no=1)],
    )


def _cfg(chunking: dict):
    """Fake app_config: chunking DB katmanından gelir (diğerleri default)."""
    return load_config(db_reader=lambda: {"chunking": chunking})


def test_chunk_metric_written(live_db):
    fid = _insert_file(live_db, "ip5m1")
    cfg = _cfg({"strategy": "section", "max_tokens": 512, "overlap_tokens": 64, "min_tokens": 30})
    out = ChunkAdapter(live_db, config=cfg, counter=WordTokenCounter()).chunk_file(fid, _long_doc())

    assert len(out.chunks) >= 1
    ok, detail = _chunk_metric(live_db, fid)
    assert ok is True
    assert "token_avg" in detail and "token_p95" in detail and "chunk_score" in detail
    assert "metadata_fill" in detail
    assert detail["metadata_fill"]["page_number_fill"] == 1.0   # tüm chunk'larda sayfa


def test_truncation_high_opens_qc_finding(live_db):
    fid = _insert_file(live_db, "ip5t1")
    # Bütçeyi AŞAN chunk yalnızca bölünemeyen tablo satırından çıkar: başlık+tek
    # satır (10 token) alt-chunk bütçesini (8) aşıyor, daha küçüğe bölünemiyor.
    cfg = _cfg({"strategy": "section", "max_tokens": 8, "overlap_tokens": 2,
                "min_tokens": 1, "table_subchunk_max_tokens": 8})
    out = ChunkAdapter(live_db, config=cfg, counter=WordTokenCounter()).chunk_file(fid, _table_doc())

    assert out.metrics["truncated_ratio"] > 0.30
    assert "chunk_truncation_high" in _findings(live_db, fid)


def test_at_max_chunks_do_not_open_truncation_finding(live_db):
    """REGRESYON: eski `>=` tanımında bu senaryo (küçük max → her chunk tam
    max'ta) bulguyu açıyordu. Canlı korpusta 132 `chunk_truncation_high`
    bulgusunun TAMAMI bu yüzden sahteydi (yeni tanımla 0 dosya). Tavana değmek
    overlap'li pencerelemenin normal sonucudur, kusur değil."""
    fid = _insert_file(live_db, "ip5t3")
    cfg = _cfg({"strategy": "section", "max_tokens": 10, "overlap_tokens": 2, "min_tokens": 1})
    out = ChunkAdapter(live_db, config=cfg, counter=WordTokenCounter()).chunk_file(fid, _long_doc())

    assert out.metrics["at_max_ratio"] > 0.30        # senaryo gerçekten tavana değiyor
    assert out.metrics["truncated_ratio"] == 0.0     # ama bütçe aşımı yok
    assert "chunk_truncation_high" not in _findings(live_db, fid)


def test_no_truncation_flag_with_large_max(live_db):
    fid = _insert_file(live_db, "ip5t2")
    cfg = _cfg({"strategy": "section", "max_tokens": 512, "overlap_tokens": 64, "min_tokens": 30})
    ChunkAdapter(live_db, config=cfg, counter=WordTokenCounter()).chunk_file(fid, _long_doc())
    assert "chunk_truncation_high" not in _findings(live_db, fid)

"""İP-8 transaksiyonel storage yazımı — SQL katmanı.

Vektörler COPY (binary) ile toplu yazılır (satır satır INSERT değil). Chunk'lar
da COPY ile yazılıp chunk_id eşlemesi tek SELECT ile alınır (IDENTITY üretir).
Tüm fonksiyonlar bir connection üzerinde çalışır; transaction sınırı çağırandadır
(StorageWriter dosya başına tek transaction açar).
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


def _json_default(o: Any):
    """JSON-native olmayan tipleri güvenli biçime indirger.

    XLSX tarih/saat hücreleri openpyxl'den datetime/date/time olarak gelir ve
    varsayılan json.dumps bunları serileştiremez ("Object of type datetime is
    not JSON serializable"). ISO string'e çeviririz; diğer bilinmeyen tipler str.
    """
    if isinstance(o, (_dt.datetime, _dt.date, _dt.time)):
        return o.isoformat()
    return str(o)


def _jsonb(data: Any) -> Jsonb:
    """table_data gibi keyfi içerikte datetime vb. olsa bile crash etmeyen Jsonb."""
    return Jsonb(data, dumps=lambda obj: json.dumps(obj, ensure_ascii=False, default=_json_default))

_CHUNK_COLS = ("file_id", "chunk_index", "chunk_text", "chunk_text_norm",
               "token_count", "page_number", "sheet_name", "section_title",
               "char_start", "char_end")


def register_vector(conn: psycopg.Connection) -> None:
    """pgvector tipini bu connection'a kaydeder (COPY binary + sorgu için)."""
    from pgvector.psycopg import register_vector as _rv
    _rv(conn)


def delete_file_derived(conn: psycopg.Connection, file_id: int) -> None:
    """REPROCESS: dosyanın türev kayıtlarını siler. core_chunks silinince
    core_vectors CASCADE ile gider — öksüz vektör kalmaz."""
    conn.execute("DELETE FROM core_chunks WHERE file_id = %s;", (file_id,))
    conn.execute("DELETE FROM core_tables WHERE file_id = %s;", (file_id,))
    conn.execute("DELETE FROM core_figures WHERE file_id = %s;", (file_id,))


def copy_chunks(conn: psycopg.Connection, file_id: int, chunks) -> None:
    """core_chunks'a COPY ile toplu yazar (chunk_id IDENTITY üretir; tsv generated)."""
    cols = ", ".join(_CHUNK_COLS)
    with conn.cursor() as cur:
        with cur.copy(f"COPY core_chunks ({cols}) FROM STDIN") as cp:
            for c in chunks:
                cp.write_row((
                    file_id, c.chunk_index, c.chunk_text, c.chunk_text_norm,
                    c.token_count, c.page_number, c.sheet_name, c.section_title,
                    c.char_start, c.char_end,
                ))


def chunk_id_map(conn: psycopg.Connection, file_id: int) -> dict[int, int]:
    """chunk_index -> chunk_id eşlemesi (COPY sonrası tek SELECT)."""
    rows = conn.execute(
        "SELECT chunk_index, chunk_id FROM core_chunks WHERE file_id = %s;",
        (file_id,),
    ).fetchall()
    return {idx: cid for idx, cid in rows}


class CorpusModelMismatch(RuntimeError):
    """Yazılmak istenen embedding damgası mevcut korpusunkiyle uyuşmuyor.

    ADR-012 "tek korpus tek backend" kuralı artık DİSİPLİNLE değil MEKANİK olarak
    zorlanır: farklı modelle üretilmiş vektörler aynı uzayda karşılaştırılamaz.
    """


def assert_corpus_model(conn: psycopg.Connection, model_name: str) -> None:
    """Korpusta başka bir damga varsa AÇIK hata. Boş korpus her damgayı kabul eder."""
    existing = [r[0] for r in conn.execute(
        "SELECT DISTINCT model_name FROM core_vectors LIMIT 5;").fetchall()]
    other = [m for m in existing if m != model_name]
    if other:
        raise CorpusModelMismatch(
            f"Korpus '{', '.join(sorted(other))}' damgalı vektörler içeriyor; "
            f"'{model_name}' yazılamaz. Model değiştiyse korpus yeniden embed edilmeli "
            "(ADR-012: tek korpus tek backend)."
        )


def copy_vectors(conn: psycopg.Connection, rows: list[tuple[int, list, str]]) -> None:
    """core_vectors'a COPY BINARY ile toplu yazar. rows: (chunk_id, vector, model_name).

    M-4: yazımdan ÖNCE korpus damgası doğrulanır (karışık-model korpusu önlenir).
    """
    if not rows:
        return
    assert_corpus_model(conn, rows[0][2])
    from pgvector import Vector
    with conn.cursor() as cur:
        with cur.copy(
            "COPY core_vectors (chunk_id, embedding, model_name) "
            "FROM STDIN WITH (FORMAT BINARY)"
        ) as cp:
            cp.set_types(["int8", "vector", "text"])
            for chunk_id, vec, model in rows:
                cp.write_row((chunk_id, Vector(vec), model))


def insert_tables(conn: psycopg.Connection, file_id: int, tables) -> None:
    if not tables:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO core_tables (file_id, page_number, sheet_name, "
            "table_index, table_data, table_text) VALUES (%s,%s,%s,%s,%s,%s);",
            [(file_id, t.page_no, t.sheet_name, t.index, _jsonb(t.data),
              t.flattened_text) for t in tables],
        )


def insert_figures(conn: psycopg.Connection, file_id: int, figures) -> None:
    if not figures:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO core_figures (file_id, page_number, figure_index, "
            "caption, storage_path) VALUES (%s,%s,%s,%s,%s);",
            [(file_id, f.page_no, f.index, f.caption, None) for f in figures],
        )


def set_status(conn: psycopg.Connection, file_id: int, status: str,
               fail_reason: str | None = None) -> None:
    conn.execute(
        "UPDATE core_files SET status = %s, fail_reason = %s WHERE file_id = %s;",
        (status, fail_reason, file_id),
    )


# --- HNSW bulk-load stratejisi (config flag, varsayılan kapalı) ---------------
_HNSW_NAME = "idx_core_vectors_hnsw"


def drop_vector_index(conn: psycopg.Connection) -> None:
    conn.execute(f"DROP INDEX IF EXISTS {_HNSW_NAME};")


def create_vector_index(conn: psycopg.Connection, *, m: int = 16, ef_construction: int = 64) -> None:
    """HNSW index'i BUILD parametreleriyle kurar (M-4: `storage` config grubundan).

    `IF NOT EXISTS` nedeniyle parametre değişikliği MEVCUT index'i değiştirmez —
    etkili olması için önce `drop_vector_index` (yeniden index) gerekir.
    """
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS {_HNSW_NAME} ON core_vectors "
        f"USING hnsw (embedding vector_cosine_ops) "
        f"WITH (m = {int(m)}, ef_construction = {int(ef_construction)});"
    )


# --- test/rapor yardımcıları -------------------------------------------------
def count_orphan_vectors(conn: psycopg.Connection) -> int:
    """core_chunks'a bağlanamayan vektör sayısı (0 olmalı)."""
    return conn.execute(
        "SELECT count(*) FROM core_vectors v "
        "LEFT JOIN core_chunks c USING (chunk_id) WHERE c.chunk_id IS NULL;"
    ).fetchone()[0]

"""M-2 — tablo gösterimi SQL katmanı: chunk→tablo çözümleme + scope'lu tablo okuma.

Şemada `core_chunks.table_id` YOK ve `is_table` persist EDİLMEZ (bkz. chunking/chunk.py).
Bağlantı bu yüzden iki yoldan TÜRETİLİR (M-2b: kalıcı kolon sonraki re-ingest turunda):

  Yol A — M-1 alt-chunk'ı: `section_title = 'tablo{N} · satır A[-B]'` → (file_id, N)
          → table_id + satır aralığı (UI vurgusu bundan gelir).
  Yol B — eşik-altı tek-chunk tablo: `chunk_text = table_text` birebir eşitliği
          → table_id (satır aralığı yok; tablonun tamamı gösterilir).

Yol B metin eşitliğine dayanır: cleaning/flatten davranışı değişirse SESSİZCE kopar.
`tests/test_faz_m2_table_view.py::test_path_b_equality_join_still_resolves` bunu mühürler.

Mükerrer `table_data` payload'ları için tie-break `min(table_id)` (deterministik).
"""

from __future__ import annotations

from typing import Any

import psycopg

# Docling'in zengin (birleşik/biçimli) hücre yer tutucusu. 23 tabloda BAŞLIK satırının
# tamamı bundan ibaret — jsonb olarak geçerli ama anlamsız; jenerik başlığa düşeriz.
PLACEHOLDER_CELL = "<!-- rich cell -->"

# `tablo{index} · satır {start}[-{end}]` (chunker._table_chunks ile birebir).
_SECTION_RE = r"^tablo(\d+) · satır (\d+)(?:-(\d+))?$"

_RESOLVE_SQL = f"""
WITH ch AS (
    SELECT chunk_id, file_id, chunk_text,
           regexp_match(section_title, %(rx)s) AS m
    FROM core_chunks
    WHERE chunk_id = ANY(%(ids)s)
),
path_a AS (
    SELECT ch.chunk_id,
           min(t.table_id) AS table_id,
           (ch.m)[2]::int AS row_start,
           COALESCE((ch.m)[3]::int, (ch.m)[2]::int) AS row_end
    FROM ch
    JOIN core_tables t
      ON t.file_id = ch.file_id AND t.table_index = (ch.m)[1]::int
    WHERE ch.m IS NOT NULL
    GROUP BY ch.chunk_id, (ch.m)[2], (ch.m)[3]
),
path_b AS (
    SELECT ch.chunk_id,
           min(t.table_id) AS table_id,
           NULL::int AS row_start,
           NULL::int AS row_end
    FROM ch
    JOIN core_tables t
      ON t.file_id = ch.file_id AND t.table_text = ch.chunk_text
    WHERE ch.m IS NULL AND COALESCE(t.table_text, '') <> ''
    GROUP BY ch.chunk_id
)
SELECT chunk_id, table_id, row_start, row_end FROM path_a
UNION ALL
SELECT chunk_id, table_id, row_start, row_end FROM path_b;
"""


# M-2b: KALICI bağ — chunk yazılırken doldurulan kolonlar. Türetme YOK.
_COLUMN_SQL = """
SELECT chunk_id, table_id, table_row_start, table_row_end
  FROM core_chunks
 WHERE chunk_id = ANY(%(ids)s) AND table_id IS NOT NULL;
"""


def _table_columns_present(conn: psycopg.Connection) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'core_chunks' AND column_name = 'table_id';"
    ).fetchone()
    return bool(row and row[0])


def resolve_table_refs(conn: psycopg.Connection, chunk_ids: list[int]) -> dict[int, dict[str, Any]]:
    """chunk_id → {table_id, row_start?, row_end?}. Tablo-kökenli OLMAYAN chunk'lar
    sonuçta YER ALMAZ (çağıran `table_ref` eklemez → mevcut davranış korunur).

    M-2b ÇİFT YOL (geçiş dönemi):
      1. KOLON yolu — chunk yazılırken doldurulan table_id/satır aralığı. Kesin.
      2. TÜRETME yolu — kolonu NULL olan (DDL'den ÖNCE yazılmış) chunk'lar için
         eski section_title-regex + core_tables JOIN mantığı.
    REPROCESS tamamlanınca her chunk'ın kolonu dolar → türetme yolu hiç çalışmaz
    ve Aşama 3'te (doğrulama yeşilken) sökülür. Geçişte İKİSİ birden gerekli:
    yalnız kolona güvenmek, REPROCESS'ten önce tablo gösterimini KIRARDI.
    """
    if not chunk_ids:
        return {}
    ids = list(chunk_ids)
    out: dict[int, dict[str, Any]] = {}

    if _table_columns_present(conn):
        for r in conn.execute(_COLUMN_SQL, {"ids": ids}).fetchall():
            out[int(r[0])] = {"table_id": int(r[1]), "row_start": r[2], "row_end": r[3]}

    # Kolonu dolmayanlar için eski türetilmiş yol (yalnızca KALANLAR sorgulanır).
    remaining = [i for i in ids if i not in out]
    if remaining:
        rows = conn.execute(_RESOLVE_SQL, {"rx": _SECTION_RE, "ids": remaining}).fetchall()
        for r in rows:
            out[int(r[0])] = {"table_id": int(r[1]), "row_start": r[2], "row_end": r[3]}
    return out


def _clean_cell(value: Any) -> str:
    """Yer tutucuyu ve NBSP'yi temizler (Docling çıktısı `\xa0` ile başlıyor)."""
    if value is None:
        return ""
    return str(value).replace(PLACEHOLDER_CELL, "").replace("\xa0", " ").strip()


def normalize_table_data(raw: Any) -> dict[str, Any]:
    """jsonb `table_data` → {columns, rows, headerless}. Satır YUTULMAZ.

    Başlık satırı (rows[0]) temizlendikten sonra TAMAMEN boşsa (23 tablodaki
    `<!-- rich cell -->` başlıkları) jenerik `Kolon N` başlığı üretilir; gövde
    satırları olduğu gibi kalır. Beklenmeyen şekil → ValueError (çağıran fallback'e düşer).
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("table_data bir satır listesi değil")
    grid = [[_clean_cell(c) for c in row] for row in raw if isinstance(row, list)]
    if len(grid) != len(raw):
        raise ValueError("table_data içinde satır olmayan öğe var")

    width = max(len(r) for r in grid)
    grid = [r + [""] * (width - len(r)) for r in grid]  # ragged'a karşı savunma

    header, body = grid[0], grid[1:]
    headerless = not any(cell for cell in header)
    if headerless:
        # Başlık bilgi taşımıyor → jenerik başlık. Gövde satırı TÜKETİLMEZ.
        columns = [f"Kolon {i + 1}" for i in range(width)]
    else:
        columns = header
    return {"columns": columns, "rows": body, "headerless": headerless}


def get_table_for_scopes(conn: psycopg.Connection, table_id: int,
                         allowed_doc_scopes: list[str]) -> dict[str, Any] | None:
    """Tabloyu YALNIZCA dosyasının doc_scope'u kullanıcının scope'larındaysa döner.

    FAIL-CLOSED: scope dışı VE var olmayan tablo aynı şekilde `None` döner — çağıran
    ikisini de 404'e çevirir, böylece varlık bilgisi (403 vs 404) sızmaz.
    Boş scope listesi → hiçbir şey (retrieval/repository ile aynı sözleşme).
    """
    if not allowed_doc_scopes:
        return None
    row = conn.execute(
        """
        SELECT t.table_id, t.table_data, t.page_number, t.sheet_name, t.table_index, f.file_name
        FROM core_tables t
        JOIN core_files f USING (file_id)
        WHERE t.table_id = %s AND f.doc_scope = ANY(%s)
        LIMIT 1;
        """,
        (int(table_id), list(allowed_doc_scopes)),
    ).fetchone()
    if row is None:
        return None

    payload: dict[str, Any] = {
        "table_id": int(row[0]),
        "file_name": row[5],
        "page": row[2],
        "sheet": row[3],
        "table_index": int(row[4]),
    }
    try:
        payload.update(normalize_table_data(row[1]))
        payload["renderable"] = True
    except ValueError:
        # Bozuk/beklenmedik table_data → UI metin-quote fallback'ine düşer (kırılmaz).
        payload.update({"columns": [], "rows": [], "headerless": False, "renderable": False})
    payload["row_count"] = len(payload["rows"])
    return payload

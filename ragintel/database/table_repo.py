"""M-2 — tablo gösterimi SQL katmanı: chunk→tablo çözümleme + scope'lu tablo okuma.

M-2b DDL (docs/FAZ7_Sema_Ek2_ChunkTableRef.sql) canlıya UYGULANDI ve M-7 Aşama 2
reprocess-all ile 41/41 dosya yeniden işlendi: artık her tablo-kökenli chunk'ın
`table_id`/`table_row_start`/`table_row_end` kolonları KALICI olarak dolu (doğrulandı:
tablo-kökenli & table_id IS NULL = 0). Eskiden burada yaşayan section_title-regex +
`chunk_text = table_text` eşitlik-join TÜRETME yolu (geçiş dönemi B-kanaryası) artık
hiçbir satırda tetiklenmiyor; ölü kod olarak SÖKÜLDÜ. Bağlantı artık TEK yoldan:
kolon okuması.

Mükerrer `table_data` payload'ları için tie-break `min(table_id)` (deterministik) —
bu artık `storage_repo.copy_chunks`'ın yazdığı kolon değerine taşındı.
"""

from __future__ import annotations

from typing import Any

import psycopg

# Docling'in zengin (birleşik/biçimli) hücre yer tutucusu. 23 tabloda BAŞLIK satırının
# tamamı bundan ibaret — jsonb olarak geçerli ama anlamsız; jenerik başlığa düşeriz.
PLACEHOLDER_CELL = "<!-- rich cell -->"

# M-2b: KALICI bağ — chunk yazılırken doldurulan kolonlar. Tek yol, türetme YOK.
_COLUMN_SQL = """
SELECT chunk_id, table_id, table_row_start, table_row_end
  FROM core_chunks
 WHERE chunk_id = ANY(%(ids)s) AND table_id IS NOT NULL;
"""


def resolve_table_refs(conn: psycopg.Connection, chunk_ids: list[int]) -> dict[int, dict[str, Any]]:
    """chunk_id → {table_id, row_start?, row_end?}. Tablo-kökenli OLMAYAN chunk'lar
    sonuçta YER ALMAZ (çağıran `table_ref` eklemez → mevcut davranış korunur).

    M-2b DDL sonrası TEK yol: kolon okuması (`core_chunks.table_id` chunk yazılırken
    dolduruluyor). Eski section_title-regex/eşitlik-join türetme yolu emekli edildi
    (bkz. modül docstring'i).
    """
    if not chunk_ids:
        return {}
    out: dict[int, dict[str, Any]] = {}
    for r in conn.execute(_COLUMN_SQL, {"ids": list(chunk_ids)}).fetchall():
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

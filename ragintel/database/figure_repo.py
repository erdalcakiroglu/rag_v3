"""M-7: belge görselleri (core_figures) — scope korumalı okuma.

GÜVENLİK SÖZLEŞMESİ (table_repo ile BİREBİR aynı): scope dışı görsel ile VAR OLMAYAN
görsel AYNI yanıtı (None) verir → çağıran ikisini de 404'e çevirir. 403 dönmek
görselin VAR OLDUĞUNU sızdırırdı (envanter dosyasının varlığı bile bilgidir).
Boş scope listesi → hiçbir şey (fail-closed).
"""

from __future__ import annotations

from typing import Any

import psycopg


def get_figure_for_scopes(conn: psycopg.Connection, figure_id: int,
                          allowed_doc_scopes: list[str]) -> dict[str, Any] | None:
    """Görselin meta'sı + diskteki yolu — YALNIZCA dosyası kullanıcının scope'undaysa."""
    if not allowed_doc_scopes:
        return None
    row = conn.execute(
        """
        SELECT g.figure_id, g.storage_path, g.page_number, g.figure_index,
               g.caption, f.file_name
        FROM core_figures g
        JOIN core_files f USING (file_id)
        WHERE g.figure_id = %s AND f.doc_scope = ANY(%s)
        LIMIT 1;
        """,
        (int(figure_id), list(allowed_doc_scopes)),
    ).fetchone()
    if row is None:
        return None
    return {
        "figure_id": int(row[0]),
        "storage_path": row[1],          # None olabilir: kayıt var, görüntü yok
        "page": row[2],
        "figure_index": int(row[3]) if row[3] is not None else None,
        "caption": row[4],
        "file_name": row[5],
    }


def list_figures_for_chunks(conn: psycopg.Connection, chunk_ids: list[int],
                            allowed_doc_scopes: list[str]) -> dict[int, list[dict[str, Any]]]:
    """chunk_id → o chunk'ın SAYFASINDAKİ görseller (aynı dosya + aynı sayfa).

    Kaynak panelinde "cite edilen sayfanın görselleri" bölümü bunu kullanır: alıntı
    hangi sayfadan geldiyse o sayfanın şekilleri gösterilir. Sayfası olmayan chunk
    (xlsx vb.) veya görüntüsü kaydedilmemiş şekil sonuçta YER ALMAZ — storage_path
    NULL olanı göstermenin anlamı yok (tıklanınca 404 verirdi).

    Scope koruması burada da fail-closed: chunk'ın dosyası kullanıcının scope'unda
    değilse o chunk için hiçbir görsel dönmez.
    """
    if not chunk_ids or not allowed_doc_scopes:
        return {}
    rows = conn.execute(
        """
        SELECT c.chunk_id, g.figure_id, g.page_number, g.caption, g.figure_index
        FROM core_chunks c
        JOIN core_files f USING (file_id)
        JOIN core_figures g
          ON g.file_id = c.file_id AND g.page_number = c.page_number
        WHERE c.chunk_id = ANY(%s)
          AND f.doc_scope = ANY(%s)
          AND c.page_number IS NOT NULL
          AND g.storage_path IS NOT NULL
        ORDER BY c.chunk_id, g.figure_index;
        """,
        (list(chunk_ids), list(allowed_doc_scopes)),
    ).fetchall()
    out: dict[int, list[dict[str, Any]]] = {}
    for chunk_id, figure_id, page, caption, fig_index in rows:
        out.setdefault(int(chunk_id), []).append({
            "figure_id": int(figure_id),
            "page": page,
            "caption": caption,
            "figure_index": int(fig_index) if fig_index is not None else None,
        })
    return out

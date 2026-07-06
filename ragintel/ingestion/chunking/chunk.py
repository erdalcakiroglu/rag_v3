"""Chunk — İP-5 çıktısı, core_chunks kontratının bellek-içi karşılığı.

Alanlar core_chunks şemasıyla hizalı (İP-8 bunları transaksiyonel yazar):
chunk_index, chunk_text, chunk_text_norm, token_count, page_number, sheet_name,
section_title, char_start, char_end. `is_table` yalnızca dahili (persist edilmez).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Chunk:
    chunk_index: int
    chunk_text: str
    chunk_text_norm: str
    token_count: int
    page_number: int | None = None
    sheet_name: str | None = None
    section_title: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    is_table: bool = False   # tablo chunk'ı (bölünmez) — persist edilmez

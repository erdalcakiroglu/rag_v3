"""ParsedDocument — İP-2 çıktısı, downstream'in TEK girdisi (DONMUŞ KONTRAT).

FAZ1_Is_Plani İP-2:
  pages[]   : {page_no, text_blocks[]}
  sections[]: {title, level, page_start, char_span}
  tables[]  : {page_no|sheet_name, index, data(jsonb-uyumlu), flattened_text}
  figures[] : {page_no, index, caption}
  language, parse_warnings[]

Alan ekleme/çıkarma ihtiyacında ÖNCE raporla (kod içinde değiştirme).
`char_span` ve tablo `data`, JSON/JSONB uyumlu tiplerdir (list/tuple/skalar).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Page:
    page_no: int
    text_blocks: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.text_blocks)


@dataclass
class Section:
    title: str
    level: int
    page_start: int | None = None
    char_span: tuple[int, int] | None = None   # temizlenmemiş tam metindeki aralık


@dataclass
class Table:
    index: int                      # dosya/sayfa içi tablo sırası
    data: list[list[Any]]           # satır/sütun (jsonb-uyumlu)
    flattened_text: str             # tablonun düzleştirilmiş metni (chunk'a gömülür)
    page_no: int | None = None      # pdf/docx
    sheet_name: str | None = None   # xlsx


@dataclass
class Figure:
    index: int
    page_no: int | None = None
    caption: str | None = None


@dataclass
class ParsedDocument:
    pages: list[Page] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)
    language: str | None = None
    parse_warnings: list[str] = field(default_factory=list)

    # -- türetilmiş yardımcılar (kontrat alanı değil) -------------------------
    @property
    def body_text(self) -> str:
        """Sayfa gövde metinleri (tablo/şekil metni HARİÇ — onlar ayrı)."""
        return "\n".join(p.text for p in self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def warn(self, msg: str) -> None:
        self.parse_warnings.append(msg)

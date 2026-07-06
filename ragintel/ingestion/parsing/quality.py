"""Parse aşama kalite ölçümü (Ek-A / ADR-011).

Bu modül YALNIZCA ÖLÇER — hiçbir eşik sabiti içermez. Eşiklerle karar
(hard fail / soft flag / OCR tetikleme) adaptörde, `app_config('quality')`'den
okunarak verilir. Böylece "kodda eşik sabiti yok" kuralı korunur.

Ölçülenler (Ek-A İP-2 girdileri):
  - coverage        : metin çıkan sayfa oranı (taranmış PDF -> ~0; OCR sinyali)
  - page_ratio      : yapısal içerik (metin/tablo/şekil) üreten sayfa oranı
  - garbage_ratio   : mojibake / basılamayan karakter oranı
  - table_count     : tespit edilen tablo sayısı
  - parse_score     : 0-100 türetilmiş alt skor (bileşik skora girdi, İP-9)
"""

from __future__ import annotations

import unicodedata

from .parsed_document import ParsedDocument

_REPLACEMENT = "�"


def _garbage_char(ch: str) -> bool:
    if ch in "\t\n\r":
        return False
    if ch == _REPLACEMENT:
        return True
    cat = unicodedata.category(ch)
    # C* = kontrol/format/atanmamış/özel-kullanım -> bozuk sinyali
    return cat.startswith("C")


def garbage_ratio(text: str) -> float:
    if not text:
        return 0.0
    garbage = sum(1 for ch in text if _garbage_char(ch))
    return garbage / len(text)


def _has_text(page) -> bool:
    return any(b.strip() for b in page.text_blocks)


def compute_parse_metrics(parsed: ParsedDocument, file_type: str) -> dict:
    """ParsedDocument'ten ham parse ölçümlerini + parse_score üretir."""
    pages = parsed.pages
    total_pages = len(pages) if pages else 0

    if file_type in ("pdf", "docx"):
        pages_with_text = sum(1 for p in pages if _has_text(p))
        pages_with_any = sum(
            1 for p in pages
            if _has_text(p)
            or any(t.page_no == p.page_no for t in parsed.tables)
            or any(f.page_no == p.page_no for f in parsed.figures)
        )
        coverage = (pages_with_text / total_pages) if total_pages else 0.0
        page_ratio = (pages_with_any / total_pages) if total_pages else 0.0
    else:
        # xlsx/txt: sayfa kavramı yok; içerik varsa coverage=1.
        has_content = bool(parsed.body_text.strip()) or bool(parsed.tables)
        coverage = 1.0 if has_content else 0.0
        page_ratio = 1.0 if has_content else 0.0

    # garbage: gövde + tablo düzleştirilmiş metin.
    combined = parsed.body_text + "\n" + "\n".join(t.flattened_text for t in parsed.tables)
    g_ratio = garbage_ratio(combined)

    parse_score = round(100.0 * coverage * (1.0 - g_ratio), 2)

    return {
        "coverage": round(coverage, 4),
        "page_ratio": round(page_ratio, 4),
        "garbage_ratio": round(g_ratio, 4),
        "table_count": len(parsed.tables),
        "figure_count": len(parsed.figures),
        "section_count": len(parsed.sections),
        "page_count": total_pages,
        "char_count": len(combined),
        "parse_score": parse_score,
    }

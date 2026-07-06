"""Deterministik cleaning çekirdeği (İP-3).

ParsedDocument (İP-2) üzerinde çalışır; SAYFA yapısını korur (citation için) ve
tablo/şekilleri OLDUĞU GİBİ geçirir (İP-2 kazanır — cleaning tabloya dokunmaz).

Adımlar (anlam yeniden yazılmaz):
  1. Kırık encoding onarımı (ftfy).
  2. Gereksiz karakter temizliği (zero-width, soft-hyphen, kontrol, U+FFFD;
     satır içi ardışık boşluk sadeleştirme).
  3. Boş satır/blok atma.
  4. Header/footer tekrarı tespiti ve atma (çok sayıda sayfada baş/son konumda
     tekrarlayan kısa bloklar).

Çıktı: cleaned ParsedDocument + cleaning_metrics (kırpılan karakter, retention,
header/footer imzaları, encoding düzeltme sayısı).
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

import ftfy

from ..parsing.parsed_document import Page, ParsedDocument
from ...text.normalize import normalize_for_quote

# Görünmez/gereksiz karakterler (soft hyphen, zero-width, BOM, replacement).
_JUNK = dict.fromkeys(map(ord, "­​‌‍⁠﻿�"), None)
_INLINE_WS = re.compile(r"[^\S\n]+")   # newline hariç ardışık boşluk


@dataclass
class CleanResult:
    document: ParsedDocument
    cleaned_text: str
    metrics: dict = field(default_factory=dict)


def _strip_junk(text: str) -> str:
    # Kontrol karakterlerini (kategori C*, \n/\t hariç) at.
    out = []
    for ch in text:
        if ch in "\n\t":
            out.append(ch)
            continue
        if unicodedata.category(ch).startswith("C"):
            continue
        out.append(ch)
    return "".join(out).translate(_JUNK)


def _clean_block(text: str) -> tuple[str, bool]:
    """Tek metin bloğunu temizler; (temiz_metin, encoding_düzeltildi_mi)."""
    fixed = ftfy.fix_text(text)
    encoding_fixed = fixed != text
    cleaned = _strip_junk(fixed)
    cleaned = _INLINE_WS.sub(" ", cleaned).strip()
    return cleaned, encoding_fixed


def _detect_boilerplate(page_blocks: list[list[str]], total_pages: int) -> set[str]:
    """Header/footer imzalarını bulur: baş/son konumda çok sayıda sayfada
    tekrarlayan kısa bloklar (normalize edilmiş anahtar)."""
    if total_pages < 3:
        return set()
    threshold = max(3, math.ceil(0.5 * total_pages))
    counts: dict[str, int] = {}
    for blocks in page_blocks:
        if not blocks:
            continue
        # Yalnızca baş(2) ve son(2) bloklar header/footer adayı.
        candidates = set(blocks[:2] + blocks[-2:])
        for b in candidates:
            if len(b) > 200:
                continue
            key = normalize_for_quote(b)
            if key:
                counts[key] = counts.get(key, 0) + 1
    return {k for k, c in counts.items() if c >= threshold}


def clean_document(parsed: ParsedDocument) -> CleanResult:
    original_chars = len(parsed.body_text)

    # 1-2) Blok bazında encoding onarımı + karakter temizliği.
    encoding_fixes = 0
    cleaned_pages_blocks: list[list[str]] = []
    for page in parsed.pages:
        blocks = []
        for raw in page.text_blocks:
            cleaned, fixed = _clean_block(raw)
            if fixed:
                encoding_fixes += 1
            blocks.append(cleaned)   # boşları henüz atma (konum korunur)
        cleaned_pages_blocks.append(blocks)

    # 3-4) Header/footer imzaları (boşluk atmadan önce konum korunur).
    signatures = _detect_boilerplate(cleaned_pages_blocks, len(parsed.pages))

    blank_removed = 0
    header_footer_removed = 0
    new_pages: list[Page] = []
    for page, blocks in zip(parsed.pages, cleaned_pages_blocks):
        kept = []
        for b in blocks:
            if not b:
                blank_removed += 1
                continue
            if normalize_for_quote(b) in signatures:
                header_footer_removed += 1
                continue
            kept.append(b)
        new_pages.append(Page(page_no=page.page_no, text_blocks=kept))

    cleaned_doc = ParsedDocument(
        pages=new_pages,
        sections=parsed.sections,      # İP-2 çıktısı korunur
        tables=parsed.tables,          # İP-2 kazanır — dokunulmaz
        figures=parsed.figures,
        language=parsed.language,
        parse_warnings=list(parsed.parse_warnings),
    )

    cleaned_text = cleaned_doc.body_text
    cleaned_chars = len(cleaned_text)
    retention = (cleaned_chars / original_chars) if original_chars else 1.0

    metrics = {
        "original_chars": original_chars,
        "cleaned_chars": cleaned_chars,
        "removed_chars": max(0, original_chars - cleaned_chars),
        "retention_ratio": round(retention, 4),
        "blank_blocks_removed": blank_removed,
        "header_footer_removed": header_footer_removed,
        "header_footer_signatures": sorted(signatures)[:20],
        "encoding_fixes": encoding_fixes,
    }
    return CleanResult(document=cleaned_doc, cleaned_text=cleaned_text, metrics=metrics)

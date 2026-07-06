"""Parse yardımcıları: tablo düzleştirme ve dil tespiti (lingua)."""

from __future__ import annotations

from typing import Any

_detector = None


def flatten_table(rows: list[list[Any]]) -> str:
    """Satır/sütun yapısını düzleştirilmiş metne çevirir (chunk'a gömülür)."""
    lines = []
    for row in rows:
        cells = ["" if c is None else str(c).strip() for c in row]
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def _get_detector():
    """lingua dedektörünü tembel kurar (TR/EN ağırlıklı kurumsal korpus)."""
    global _detector
    if _detector is None:
        from lingua import Language, LanguageDetectorBuilder
        langs = [
            Language.TURKISH, Language.ENGLISH, Language.GERMAN,
            Language.FRENCH, Language.SPANISH, Language.ITALIAN,
        ]
        _detector = LanguageDetectorBuilder.from_languages(*langs).build()
    return _detector


def detect_language(text: str, *, min_chars: int = 20) -> str | None:
    """Metnin ISO 639-1 dil kodunu döndürür; kısa/boşsa None."""
    if not text or len(text.strip()) < min_chars:
        return None
    lang = _get_detector().detect_language_of(text)
    if lang is None:
        return None
    return lang.iso_code_639_1.name.lower()

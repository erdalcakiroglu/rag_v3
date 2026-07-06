"""Deterministik injection dedektörleri (İP-4) — llm-guard/torch YOK.

Her dedektör (span, kanıt) bulguları döndürür. Eşikler/kalıplar çağıran
tarafından (config'ten) verilir; bu modülde sabit YOK (hidden-unicode aralıkları
yapısaldır — ADR-007'de tanımlı).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Gizli unicode: zero-width (U+200B..200F), bidi override (U+202A..202E),
# isolate (U+2066..2069). ADR-007 minimal set.
_HIDDEN_UNICODE = re.compile("[​-‏‪-‮⁦-⁩]")
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass
class Finding:
    rule: str            # 'pattern' | 'hidden_unicode' | 'base64' | 'homoglyph'
    start: int
    end: int
    evidence: str        # kısa kanıt (span metni / kod noktası)


def scan_patterns(text: str, compiled: list[re.Pattern]) -> list[Finding]:
    out: list[Finding] = []
    for pat in compiled:
        for m in pat.finditer(text):
            out.append(Finding("pattern", m.start(), m.end(),
                               m.group(0)[:80]))
    return out


def scan_hidden_unicode(text: str) -> list[Finding]:
    out: list[Finding] = []
    for m in _HIDDEN_UNICODE.finditer(text):
        cp = ord(m.group(0))
        out.append(Finding("hidden_unicode", m.start(), m.end(), f"U+{cp:04X}"))
    return out


def scan_base64(text: str, min_run: int) -> list[Finding]:
    """En az min_run uzunluğunda base64 çalışması. Hex-hash FP'sini dışlamak için
    çalışmada '+'/'/' bulunmalı veya '=' ile padding'lenmiş olmalı."""
    rx = re.compile(r"[A-Za-z0-9+/]{%d,}={0,2}" % int(min_run))
    out: list[Finding] = []
    for m in rx.finditer(text):
        s = m.group(0)
        if "+" in s or "/" in s or s.endswith("="):
            out.append(Finding("base64", m.start(), m.end(), s[:40] + "…"))
    return out


def _script(ch: str) -> str:
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return "OTHER"
    if "CYRILLIC" in name:
        return "CYRILLIC"
    if "GREEK" in name:
        return "GREEK"
    if "LATIN" in name:
        return "LATIN"
    return "OTHER"


def scan_homoglyph(text: str) -> list[Finding]:
    """Aynı sözcükte Latin + (Kiril/Yunan) karışımı -> homoglyph şüphesi."""
    out: list[Finding] = []
    for m in _WORD.finditer(text):
        word = m.group(0)
        scripts = {_script(c) for c in word if c.isalpha()}
        if "LATIN" in scripts and ({"CYRILLIC", "GREEK"} & scripts):
            out.append(Finding("homoglyph", m.start(), m.end(), word[:40]))
    return out

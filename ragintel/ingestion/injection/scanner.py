"""Injection tarayıcı (İP-4) — config-güdümlü, deterministik.

Kalıplar ve eşikler `app_config('injection')`'den (config zinciri). Karar YOK:
yalnızca şüpheyi işaretler. Girdi: cleaned_text (+ tablo metinleri). Gizli
unicode için ham (pre-clean) metin de kabul edilir — İP-3 cleaning kategori-C
karakterlerini (zero-width/bidi) zaten temizlediğinden, cleaned_text'te bunlar
bulunmaz; bu yüzden hidden-unicode kuralı ham metin üzerinde çalışır.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .rules import (
    Finding,
    scan_base64,
    scan_hidden_unicode,
    scan_homoglyph,
    scan_patterns,
)


@dataclass
class InjectionResult:
    flagged: bool
    findings: list[Finding] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    scan_ms: float = 0.0


class InjectionScanner:
    def __init__(self, config):
        inj = config.group("injection")
        self.enabled = inj.enabled
        self.patterns = [re.compile(p, re.IGNORECASE) for p in inj.patterns]
        self.hidden_min = int(inj.hidden_unicode_min_count)
        self.base64_run = int(inj.base64_min_run)
        self.homoglyph_min = int(inj.homoglyph_min_count)

    def scan(self, text: str, *, raw_text: str | None = None) -> InjectionResult:
        if not self.enabled:
            return InjectionResult(False)
        t0 = time.perf_counter()
        text = text or ""
        hidden_src = raw_text if raw_text is not None else text

        findings: list[Finding] = []
        findings += scan_patterns(text, self.patterns)
        findings += scan_hidden_unicode(hidden_src)
        findings += scan_base64(text, self.base64_run)
        findings += scan_homoglyph(text)

        counts = {r: 0 for r in ("pattern", "hidden_unicode", "base64", "homoglyph")}
        for f in findings:
            counts[f.rule] += 1

        # Eşikler config'ten: pattern/base64 tek eşleşmede; hidden/homoglyph min-count.
        flagged = (
            counts["pattern"] >= 1
            or counts["base64"] >= 1
            or counts["hidden_unicode"] >= self.hidden_min
            or counts["homoglyph"] >= self.homoglyph_min
        )
        scan_ms = (time.perf_counter() - t0) * 1000
        return InjectionResult(flagged, findings, counts, round(scan_ms, 3))

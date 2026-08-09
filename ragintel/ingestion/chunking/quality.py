"""Chunk aşama kalite ölçümü (Ek-A İP-5). Yalnızca ÖLÇER — eşik sabiti yok.

Girdiler: token dağılımı (ort/p95), min-altı oranı, bütçe-aşımı oranı
(truncated_ratio), section hizalama oranı. Kararlar (soft flag) adaptörde
app_config('quality').chunk'tan okunur.

`truncated_ratio` = token_count > max_tokens (2026-08-09'da DÜZELTİLDİ; eskiden
`>=` idi). Gerekçe ÖLÇÜLDÜ (1119 dosya / 50065 chunk, canlı korpus):
  * Tavana DEĞMEK bu boru hattında içerik kaybı değil — gövde pencereleri
    overlap'li (chunker._window_ranges), tablolar satır-gruplarına bölünüyor,
    hiçbir chunk bge-m3'ün 8192 penceresini aşmıyor. Tam max'ta bitmek uzun
    bir bölümün NORMAL sonucudur: korpusun %32.56'sı tam 512'de.
  * `>=` bunu skora çarpan olarak sokunca skor kaliteyi değil UZUNLUĞU
    cezalandırıyordu: 991440.pdf (528 chunk, min-altı 0, aşım 0 — kusursuz)
    chunk skoru 18.37 alıp en kötü 4. sıraya düşüyordu; düzeltmeyle 100.00.
  * `chunk_truncation_high` bulgusunun 132 örneğinin TAMAMI bu yüzden sahteydi
    (yeni tanımla 0 dosya). Ölçüm: scripts/skor_tanimi_probe.py.
Bütçeyi gerçekten AŞAN chunk (>max) ise gerçek bir chunking kusurudur:
bölünemeyen tablo satırı, bütçe tutturulamamış (korpusta %0.44).

`at_max_ratio` bilgi olarak KALIR (skora girmez) — eski `truncated_ratio` ile
karşılaştırılabilirlik ve "max_tokens küçük mü?" sorusu için.
"""

from __future__ import annotations

import math

from .chunk import Chunk


def _percentile(values: list[int], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(s[int(k)])
    return s[lo] * (hi - k) + s[hi] * (k - lo)


def compute_chunk_metrics(chunks: list[Chunk], *, max_tokens: int,
                          min_tokens: int) -> dict:
    total = len(chunks)
    if total == 0:
        return {"chunk_count": 0, "token_avg": 0.0, "token_p95": 0.0,
                "below_min_ratio": 0.0, "truncated_ratio": 0.0,
                "at_max_ratio": 0.0,
                "section_alignment_ratio": 0.0, "table_chunks": 0,
                "chunk_score": 0.0}

    tokens = [c.token_count for c in chunks]
    body = [c for c in chunks if not c.is_table]

    below_min = sum(1 for t in tokens if t < min_tokens)
    truncated = sum(1 for t in tokens if t > max_tokens)    # bütçe AŞIMI (bkz. modül başlığı)
    at_max = sum(1 for t in tokens if t == max_tokens)      # normal pencereleme — yalnız bilgi
    aligned = sum(1 for c in body if c.section_title)

    below_min_ratio = below_min / total
    truncated_ratio = truncated / total
    section_alignment = (aligned / len(body)) if body else 0.0

    chunk_score = round(100.0 * (1 - truncated_ratio) * (1 - below_min_ratio), 2)

    return {
        "chunk_count": total,
        "token_avg": round(sum(tokens) / total, 2),
        "token_p95": round(_percentile(tokens, 0.95), 2),
        "token_max": max(tokens),
        "below_min_ratio": round(below_min_ratio, 4),
        "truncated_ratio": round(truncated_ratio, 4),
        "at_max_ratio": round(at_max / total, 4),
        "section_alignment_ratio": round(section_alignment, 4),
        "table_chunks": sum(1 for c in chunks if c.is_table),
        "chunk_score": chunk_score,
    }


def metadata_fill_report(chunks: list[Chunk]) -> dict:
    """İP-6: chunk düzeyi metadata doluluk oranları."""
    total = len(chunks)
    if total == 0:
        return {"chunk_count": 0}

    def rate(pred) -> float:
        return round(sum(1 for c in chunks if pred(c)) / total, 4)

    return {
        "chunk_count": total,
        "page_number_fill": rate(lambda c: c.page_number is not None),
        "sheet_name_fill": rate(lambda c: c.sheet_name is not None),
        "section_title_fill": rate(lambda c: c.section_title is not None),
        "char_span_fill": rate(lambda c: c.char_start is not None and c.char_end is not None),
    }

"""Retrieval sıralama metrikleri (İP-2.4) — saf fonksiyonlar, deterministik.

Tümü bir `gold` chunk_id KÜMESİ ve sıralı `ranked` chunk_id listesi üzerinden
çalışır. Multi-hop'ta gold küme birden çok dosyanın kanıt chunk'ını içerir;
recall bu küme üzerinden hesaplandığından iki kanıt da bulunmadan 1.0 olmaz.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def recall_at_k(gold: set[int], ranked: Sequence[int], k: int) -> float:
    """Top-k içinde bulunan gold chunk oranı (küme recall'ü)."""
    if not gold:
        return 0.0
    topk = set(ranked[:k])
    return len(gold & topk) / len(gold)


def mrr(gold: set[int], ranked: Sequence[int]) -> float:
    """İlk isabetli (gold) chunk'ın karşılıklı sırası; hiç yoksa 0."""
    if not gold:
        return 0.0
    for idx, cid in enumerate(ranked, start=1):
        if cid in gold:
            return 1.0 / idx
    return 0.0


def ndcg_at_k(gold: set[int], ranked: Sequence[int], k: int) -> float:
    """Binary-relevans nDCG@k. IDCG = ilk min(|gold|,k) sırada tüm gold'lar."""
    if not gold:
        return 0.0
    dcg = 0.0
    for idx, cid in enumerate(ranked[:k], start=1):
        if cid in gold:
            dcg += 1.0 / math.log2(idx + 1)
    ideal_hits = min(len(gold), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


def mean(values: Iterable[float]) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0

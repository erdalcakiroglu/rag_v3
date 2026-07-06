"""Embedding aşama kalite ölçümü (Ek-A İP-7). Yalnızca ÖLÇER.

- NaN/sıfır-norm vektör tespiti (embed_failed).
- Norm dağılımı (normalize doğrulaması: ort ~1.0).
- Doc-içi benzerlik anomalisi (embed_anomaly, soft): ortalama ikili kosinüs
  benzerliği eşiği aşarsa (dejenere/near-duplicate içerik).
"""

from __future__ import annotations

import math

_EPS = 1e-6


def vector_norm(vec: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vec))


def is_bad_vector(vec, dim: int) -> bool:
    """NaN/Inf, yanlış boyut veya sıfır-norm -> bozuk (embed_failed)."""
    if vec is None or len(vec) != dim:
        return True
    if any(not math.isfinite(x) for x in vec):
        return True
    return vector_norm(vec) < _EPS


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = vector_norm(a), vector_norm(b)
    if na < _EPS or nb < _EPS:
        return 0.0
    return dot / (na * nb)


def mean_pairwise_cosine(vectors: list[list[float]], *, cap: int = 50) -> float:
    """Doc-içi ortalama ikili kosinüs benzerliği (büyük N'de örneklenir)."""
    v = vectors[:cap]
    if len(v) < 2:
        return 0.0
    total, count = 0.0, 0
    for i in range(len(v)):
        for j in range(i + 1, len(v)):
            total += _cosine(v[i], v[j])
            count += 1
    return total / count if count else 0.0


def compute_embed_metrics(vectors: list[list[float]], *, expected: int,
                          failed: int, dim: int) -> dict:
    """Norm dağılımı + alt skor. `vectors` = başarılı (normalize) vektörler."""
    norms = [vector_norm(v) for v in vectors]
    n = len(norms)
    mean_norm = sum(norms) / n if n else 0.0
    norm_std = (math.sqrt(sum((x - mean_norm) ** 2 for x in norms) / n)
                if n else 0.0)
    nan_zero = failed
    embed_score = round(100.0 * (n / expected), 2) if expected else 0.0

    return {
        "expected": expected,
        "embedded": n,
        "failed": failed,
        "nan_or_zero_norm": nan_zero,
        "mean_norm": round(mean_norm, 6),
        "norm_std": round(norm_std, 6),
        "embed_score": embed_score,
        "dim": dim,
    }

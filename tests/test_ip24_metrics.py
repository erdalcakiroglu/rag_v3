"""İP-2.4 retrieval metrikleri — saf, deterministik."""

from __future__ import annotations

from ragintel.eval.metrics import mrr, ndcg_at_k, recall_at_k


def test_recall_is_set_based():
    assert recall_at_k({1, 2}, [1, 3, 2], 5) == 1.0
    assert recall_at_k({1, 2}, [1, 3, 4], 5) == 0.5      # yalnız biri
    assert recall_at_k({1, 2}, [3, 4], 5) == 0.0
    assert recall_at_k({1, 2}, [1, 2, 3], 1) == 0.5      # top-1 sadece 1'i kapsar


def test_mrr_first_relevant_rank():
    assert mrr({5}, [1, 2, 5]) == 1 / 3
    assert mrr({1}, [1]) == 1.0
    assert mrr({9}, [1, 2, 3]) == 0.0


def test_ndcg_ideal_and_partial():
    assert ndcg_at_k({1}, [1], 5) == 1.0
    assert ndcg_at_k({1, 2}, [1, 2], 5) == 1.0
    partial = ndcg_at_k({2}, [1, 2], 5)                  # gold rank-2
    assert 0.0 < partial < 1.0


def test_empty_gold_is_zero():
    assert recall_at_k(set(), [1, 2], 5) == 0.0
    assert mrr(set(), [1, 2]) == 0.0
    assert ndcg_at_k(set(), [1, 2], 5) == 0.0

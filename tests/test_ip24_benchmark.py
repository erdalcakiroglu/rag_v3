"""İP-2.4 benchmark motoru — sentetik self-test, determinizm, kategori/küme mantığı."""

from __future__ import annotations

from ragintel.eval.retrieval_benchmark import (
    EvalRecord, GoldMapping, run_benchmark,
)


class FakeRetriever:
    """Sabit sıralama döndürür — düzeneği DB'siz doğrular."""
    def __init__(self, ranked_by_query, name="fake"):
        self.ranked = ranked_by_query
        self.name = name

    def rank(self, query, doc_scope, top_k):
        return list(self.ranked[query])[:top_k]


def _rec(rid, q, cat="single_fact", answerable=True):
    return EvalRecord(id=rid, question=q, category=cat, doc_scope="default",
                      answerable=answerable, evidence=[])


def test_self_test_recall_one_when_gold_returned():
    # KABUL: gold chunk'lar retriever tarafından döndürülünce recall@k=1.0
    recs = [_rec("r1", "q1"), _rec("r2", "q2", cat="table_based")]
    mapping = GoldMapping(gold_by_id={"r1": {10, 11}, "r2": {20}},
                          total_evidence=3, mapped_evidence=3)
    ret = FakeRetriever({"q1": [10, 11, 99, 98], "q2": [20, 1, 2]})
    res = run_benchmark(recs, mapping, ret)
    ov = res["aggregate"]["overall"]
    assert ov["recall@5"] == 1.0 and ov["recall@10"] == 1.0 and ov["recall@20"] == 1.0
    assert ov["mrr"] == 1.0
    assert ov["ndcg@5"] == 1.0
    assert ov["n"] == 2


def test_answerable_false_excluded():
    recs = [_rec("r1", "q1"), _rec("u1", "qu", cat="unanswerable", answerable=False)]
    mapping = GoldMapping(gold_by_id={"r1": {10}}, total_evidence=1, mapped_evidence=1)
    ret = FakeRetriever({"q1": [10], "qu": [1, 2]})
    res = run_benchmark(recs, mapping, ret)
    ids = {r["id"] for r in res["per_record"]}
    assert ids == {"r1"}                       # unanswerable metriklere girmez
    assert res["aggregate"]["overall"]["n"] == 1


def test_multi_hop_recall_over_gold_set():
    # İki dosyanın kanıtı da bulunmalı: yalnız biri gelirse recall@5=0.5
    recs = [_rec("m1", "qm", cat="multi_hop")]
    mapping = GoldMapping(gold_by_id={"m1": {10, 20}}, total_evidence=2, mapped_evidence=2)
    ret = FakeRetriever({"qm": [10, 5, 6, 7, 8]})   # 20 yok
    res = run_benchmark(recs, mapping, ret)
    assert res["aggregate"]["by_category"]["multi_hop"]["recall@5"] == 0.5


def test_category_breakdown_present():
    recs = [_rec("r1", "q1", "single_fact"), _rec("m1", "qm", "multi_hop")]
    mapping = GoldMapping(gold_by_id={"r1": {10}, "m1": {20, 21}}, total_evidence=3, mapped_evidence=3)
    ret = FakeRetriever({"q1": [10], "qm": [20, 21]})
    res = run_benchmark(recs, mapping, ret)
    assert set(res["aggregate"]["by_category"]) == {"single_fact", "multi_hop"}
    assert res["aggregate"]["by_category"]["multi_hop"]["recall@5"] == 1.0


def test_determinism_same_inputs_same_result():
    recs = [_rec("r1", "q1"), _rec("m1", "qm", "multi_hop")]
    mapping = GoldMapping(gold_by_id={"r1": {10}, "m1": {20, 21}}, total_evidence=3, mapped_evidence=3)
    ret = FakeRetriever({"q1": [10, 3], "qm": [20, 9, 21]})
    a = run_benchmark(recs, mapping, ret)
    b = run_benchmark(recs, mapping, ret)
    assert a["aggregate"] == b["aggregate"]
    assert a["per_record"] == b["per_record"]

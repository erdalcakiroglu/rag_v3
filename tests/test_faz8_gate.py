"""FAZ 8 — CI eval gate testleri: pass/fail/infra yolları + eşik config'ten."""

from __future__ import annotations

from ragintel.eval.gates import GateThresholds, gate_decision, thresholds_from_config


def _result(faith=0.80, prec=0.80, hon_pass=5, hon_total=5, status="complete",
            scored=3, awc=5, errors=0, arun=5, urun=5):
    return {
        "status": status, "judge": "x",
        "ragas": {"overall": {"faithfulness": faith, "context_precision": prec},
                  "per_question": [{}] * scored},
        "honesty": {"pass": hon_pass, "total": hon_total},
        "dataset": {"answerable_run": arun, "unanswerable_run": urun,
                    "answered_with_context": awc, "errors": [{}] * errors},
    }


_THR = GateThresholds()  # 0.80 / 0.70 / 0.75


# --- PASS (exit 0) ------------------------------------------------------------
def test_gate_pass():
    o = gate_decision(_result(faith=0.75, prec=0.80, hon_pass=5, hon_total=5), _THR)
    assert o.code == 0 and all(ok for *_, ok in o.checks)


# --- FAIL (exit 1) ------------------------------------------------------------
def test_gate_fail_faithfulness():
    o = gate_decision(_result(faith=0.50), _THR)
    assert o.code == 1 and "faithfulness" in o.reason


def test_gate_fail_honesty():
    o = gate_decision(_result(hon_pass=2, hon_total=5), _THR)  # 0.4 < 0.8
    assert o.code == 1 and "honesty_ratio" in o.reason


def test_gate_fail_precision():
    o = gate_decision(_result(prec=0.60), _THR)
    assert o.code == 1 and "context_precision" in o.reason


# --- INFRA (exit 2) -----------------------------------------------------------
def test_gate_infra_paused():
    assert gate_decision(_result(status="paused"), _THR).code == 2


def test_gate_infra_nothing_scored():
    assert gate_decision(_result(scored=0), _THR).code == 2


def test_gate_infra_no_context():
    # retrieval/embedder down → hiçbir yanıt bağlam almadı
    assert gate_decision(_result(awc=0, arun=5), _THR).code == 2


def test_gate_infra_all_errored():
    assert gate_decision(_result(scored=1, errors=10, arun=5, urun=5), _THR).code == 2


# --- eşik config'ten: DEĞİŞİNCE DAVRANIŞ DEĞİŞİR ------------------------------
def test_threshold_change_flips_outcome():
    r = _result(faith=0.72, prec=0.80, hon_pass=5, hon_total=5)
    assert gate_decision(r, GateThresholds(faithfulness_min=0.70)).code == 0   # geçer
    assert gate_decision(r, GateThresholds(faithfulness_min=0.90)).code == 1   # sıkı eşik → fail


class _FakeGroup:
    honesty_min_ratio = 0.85
    faithfulness_min = 0.72
    context_precision_min = 0.78


class _FakeCfg:
    def group(self, name):
        assert name == "eval_gates"
        return _FakeGroup()


def test_thresholds_from_config():
    thr = thresholds_from_config(_FakeCfg())
    assert thr.honesty_min_ratio == 0.85 and thr.faithfulness_min == 0.72 and thr.context_precision_min == 0.78


def test_thresholds_from_config_fallback_on_missing():
    class _Empty:
        def group(self, name):
            raise KeyError(name)
    thr = thresholds_from_config(_Empty())
    assert thr == GateThresholds()  # kod varsayılanı

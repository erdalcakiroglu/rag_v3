"""FAZ 8 — CI eval gate testleri: pass/fail/infra yolları + eşik config'ten."""

from __future__ import annotations

from ragintel.eval.gates import GateThresholds, gate_decision, thresholds_from_config


def _result(faith=0.90, prec=0.90, hon_pass=5, hon_total=5, status="complete",
            scored=3, awc=5, errors=0, arun=5, urun=5, fb_rate=0.10):
    # M-9: gate KARAR ZEMİNİ answered_only'dir (şişkin overall değil). Fixture ikisini de
    # verir; gate answered_only'i okur. fallback ayrı HARD tavandır.
    return {
        "status": status, "judge": "x",
        "ragas": {"overall": {"faithfulness": faith, "context_precision": prec},
                  "answered_only": {"overall": {"faithfulness": faith, "context_precision": prec}},
                  "fallback": {"rate": fb_rate, "count": 1, "total": 10},
                  "per_question": [{}] * scored},
        "honesty": {"pass": hon_pass, "total": hon_total},
        "dataset": {"answerable_run": arun, "unanswerable_run": urun,
                    "answered_with_context": awc, "errors": [{}] * errors},
    }


_THR = GateThresholds()  # M-9: 0.87 / 0.85 / honesty 0.80 / fallback_max 0.25


# --- PASS (exit 0) ------------------------------------------------------------
def test_gate_pass():
    o = gate_decision(_result(faith=0.90, prec=0.90, hon_pass=5, hon_total=5, fb_rate=0.10), _THR)
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


def test_gate_fail_fallback_orani():
    """M-9: sapkın teşvik kapatıldı — fallback tavanı aşılırsa gate DÜŞER (HARD).
    Kalite metrikleri mükemmel olsa bile (reddederek 'sadık' görünen sistem geçemez)."""
    o = gate_decision(_result(faith=1.0, prec=1.0, fb_rate=0.40), _THR)  # 0.40 > 0.25 tavan
    assert o.code == 1 and "fallback_rate" in o.reason


def test_gate_fallback_her_modda_HARD():
    """Karşı-örnek: honesty gibi fallback da smoke'ta ADVISORY'ye düşmez — deterministik
    bir orandır (judge puanı değil), varyansı yoktur, hard-fail taşıyabilir."""
    o = gate_decision(_result(fb_rate=0.40), _THR, smoke=True)
    assert o.code == 1 and "fallback_rate" in o.reason


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
    r = _result(faith=0.80, prec=0.90, hon_pass=5, hon_total=5)
    assert gate_decision(r, GateThresholds(faithfulness_min=0.70)).code == 0   # gevşek → geçer
    assert gate_decision(r, GateThresholds(faithfulness_min=0.90)).code == 1   # sıkı eşik → fail


class _FakeGroup:
    honesty_min_ratio = 0.85
    faithfulness_min = 0.87
    context_precision_min = 0.85
    fallback_rate_max = 0.25


class _FakeCfg:
    def group(self, name):
        assert name == "eval_gates"
        return _FakeGroup()


def test_thresholds_from_config():
    thr = thresholds_from_config(_FakeCfg())
    assert (thr.honesty_min_ratio == 0.85 and thr.faithfulness_min == 0.87
            and thr.context_precision_min == 0.85 and thr.fallback_rate_max == 0.25)


def test_thresholds_from_config_fallback_on_missing():
    class _Empty:
        def group(self, name):
            raise KeyError(name)
    thr = thresholds_from_config(_Empty())
    assert thr == GateThresholds()  # kod varsayılanı

"""M-7 — smoke gate'te SİNYAL-VARYANS EŞLEMESİ.

Eşik GEVŞETME değildir: eşikler ve sayılar aynı. Değişen tek şey, hangi sinyalin exit
kodunu taşımaya yeterince KARARLI olduğudur.

  HARD (exit 1)     : honesty_ratio — 5 soruda deterministik kontrol (agent "bilmiyorum"
                      diyebildi mi?), judge puanı değil.
  ADVISORY (exit 0) : faithfulness / context_precision — n=5 + TEK KOŞUM judge puanı.
                      ÖLÇÜLDÜ: aynı korpusta tek koşum context_precision 0.630, runs=3
                      medyanı 0.739. Bu varyans hard-fail taşıyamaz — her push'ta rastgele
                      kırmızı yanan gate, kurt-çocuk etkisiyle korumayı öldürür.

Otoriter hard karar NIGHTLY TAM koşudadır (36, runs=3): orada judge metrikleri de HARD.
"""

from __future__ import annotations

from ragintel.eval.gates import GateThresholds, format_gate, gate_decision

_THR = GateThresholds(honesty_min_ratio=0.76, faithfulness_min=0.72,
                      context_precision_min=0.70)


def _result(*, faith=0.80, prec=0.80, hon_pass=5, hon_total=5, scored=5):
    return {
        "status": "complete",
        "judge": "test",
        "ragas": {"per_question": [{}] * scored,
                  "overall": {"faithfulness": faith, "context_precision": prec}},
        "honesty": {"pass": hon_pass, "total": hon_total},
        "dataset": {"answerable_run": scored, "unanswerable_run": hon_total,
                    "answered_with_context": scored, "errors": []},
    }


def _sev(outcome, name):
    return {n: ("hard" if hard else "advisory") for n, _, _, _, hard in outcome.checks}[name]


# --- SMOKE: judge metrikleri advisory ---------------------------------------
def test_smoke_judge_metrigi_altta_ama_exit_0():
    """Asıl iddia: smoke'ta faithfulness eşik ALTINDA olsa bile gate KIRMIZI YANMAZ."""
    o = gate_decision(_result(faith=0.50), _THR, smoke=True)
    assert o.code == 0, "smoke'ta judge metriği hard-fail ETMEMELİ (n=5 varyansı)"
    assert _sev(o, "faithfulness") == "advisory"


def test_smoke_advisory_SUSTURULMAZ():
    """...ama GİZLENMEZ de. Advisory sessizce yutulursa koruma değil, kör nokta olur."""
    o = gate_decision(_result(faith=0.50, prec=0.40), _THR, smoke=True)
    assert o.code == 0
    assert "ADVISORY" in o.reason
    assert "faithfulness" in o.reason and "context_precision" in o.reason
    assert "nightly" in o.reason.lower()          # nereye bakılacağı SÖYLENİR
    çıktı = format_gate(o, _result(), _THR, smoke=True)
    assert "ADVISORY" in çıktı and "ALTINDA" in çıktı


def test_smoke_honesty_HARD_kalir():
    """Karşı-örnek (bu olmadan test vacuous olurdu): smoke'ta HER ŞEY advisory DEĞİL.
    honesty deterministiktir → hâlâ exit 1 verir."""
    o = gate_decision(_result(hon_pass=2, hon_total=5), _THR, smoke=True)   # 0.40 < 0.76
    assert o.code == 1, "honesty smoke'ta da HARD olmalı (deterministik sinyal)"
    assert _sev(o, "honesty_ratio") == "hard"
    assert "honesty_ratio" in o.reason


def test_smoke_hard_fail_advisory_ile_birlikte_raporlanir():
    """honesty düşer (hard) + judge metriği de altta (advisory) → exit 1, ama advisory
    da mesajda görünür (tanı koyarken ikisi birden lazım)."""
    o = gate_decision(_result(faith=0.50, hon_pass=1, hon_total=5), _THR, smoke=True)
    assert o.code == 1
    assert "honesty_ratio" in o.reason and "advisory" in o.reason


def test_smoke_hepsi_gecerse_temiz_pass():
    o = gate_decision(_result(), _THR, smoke=True)
    assert o.code == 0 and o.reason == "tüm eşikler geçildi"


# --- FULL (nightly): judge metrikleri HARD ----------------------------------
def test_full_kosumda_judge_metrigi_HARD():
    """Otoriter karar burada: tam koşuda (runs=3) varyans bastırılmıştır → hard-fail."""
    o = gate_decision(_result(faith=0.50), _THR, smoke=False)
    assert o.code == 1, "tam koşuda judge metriği HARD olmalı"
    assert _sev(o, "faithfulness") == "hard"


def test_full_kosum_varsayilan():
    """smoke bayrağı verilmezse HARD davranış (güvenli taraf: gevşeklik opt-in değil)."""
    assert gate_decision(_result(prec=0.10), _THR).code == 1


# --- Altyapı her modda exit 2 ------------------------------------------------
def test_altyapi_hatasi_smoke_ta_da_exit_2():
    """Sinyal-varyans eşlemesi ALTYAPIYI kapsamaz: eval güvenilir koşmadıysa smoke da durur."""
    assert gate_decision(_result(scored=0), _THR, smoke=True).code == 2
    assert gate_decision({"status": "paused"}, _THR, smoke=True).code == 2


# --- honesty'nin ZEMİNİ: smoke'ta da 5 unanswerable ---------------------------
def test_smoke_honesty_5_soruluk_zeminde_olculur():
    """honesty smoke'ta HARD (exit 1 taşır) → karnedeki 5-soruluk zeminle AYNI temelde
    ölçülmeli. Eskiden smoke yalnızca 2 unanswerable koşuyordu: 0.76 eşiği fiilen 2/2
    şart koşuyordu ve tek soruluk sapma gate'i kırmızıya çeviriyordu (ardışık iki koşu
    1.000 ve 0.500 verdi — aynı kod, aynı korpus).

    honesty JUDGE KULLANMAZ (kural tabanlı) → 3 ek soru judge token'ı harcamaz.
    """
    import inspect

    from ragintel.eval import __main__ as cli
    from ragintel.eval import harness

    # 1) harness parametreyi TANIYOR ve limit'e rağmen tüm unanswerable'ı koşuyor
    assert "all_unanswerable" in inspect.signature(harness.evaluate).parameters
    src = inspect.getsource(harness.evaluate)
    assert "if not all_unanswerable:" in src, "all_unanswerable, unanswerable kırpmasını atlamalı"

    # 2) gate SMOKE'ta bunu AÇIYOR (yoksa parametre var ama kullanılmıyor olurdu — vacuous)
    assert "all_unanswerable=args.smoke" in inspect.getsource(cli._cmd_gate)

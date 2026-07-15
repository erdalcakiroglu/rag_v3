"""M-7 son adım — Eval gate evidence ön-koşulu.

`_cmd_gate`, eşik-kıyas aşamasından (harness.evaluate + judge) ÖNCE golden set'in
`gold_evidence` alıntılarının korpusta hâlâ çözülüp çözülmediğini ucuz ve deterministik
biçimde kontrol eder (`gates.evidence_precondition`). Amaç: korpus/parse kayması (örn.
docling sürüm yükseltmesi) yüzünden golden çıpaları tutmuyorsa, bunu bir KALİTE
regresyonu gibi yorumlayıp pahalı judge çağrısını (token harcayarak) boşa harcamamak.

İki kanıt:
(a) evidence tam çözülüyorsa → gate normal akışına (harness.evaluate ÇAĞRILIR) devam eder.
(b) evidence bozuksa → exit 2 (altyapı, kalite fail=1 DEĞİL) ve harness.evaluate (dolayısıyla
    judge) HİÇ ÇAĞRILMAZ — pozitif ön-koşul: (a)'da GERÇEKTEN çağrıldığını da doğruluyoruz,
    yoksa (b)'deki "çağrılmadı" iddiası vacuous olurdu (evaluate zaten hiç çağrılmıyor olabilirdi).
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

import ragintel.eval.__main__ as main_mod
import ragintel.eval.gates as gates_mod
import ragintel.eval.harness as harness_mod


def _args(golden="v0.1", smoke=True, runs=1, agent_model=None, judge_model=None,
          as_json=False, allow_model_drift=False):
    return argparse.Namespace(
        golden=golden, smoke=smoke, runs=runs,
        agent_model=agent_model, judge_model=judge_model, json=as_json,
        allow_model_drift=allow_model_drift,
    )


class _FakeCfg:
    """EffectiveConfig ikamesi: `group()` DESTEKLER.

    (M-7: gate artık model-zemini ön-koşulunu da koşuyor ve `cfg.group("eval")` /
    `cfg.group("retrieval")` okuyor. Düz SimpleNamespace burada patlar ve gate,
    evidence testinin ölçmek istediği yola HİÇ giremezdi.)
    Modeller karneyle AYNI verilir → bu dosyanın testleri yalnızca EVIDENCE yolunu ölçer,
    model-zemini yolu (ayrı dosyada test edilir) araya karışmaz.
    """

    def group(self, name):
        if name == "eval":
            # M-9: agent_temperature = karne zemini (agent.temperature ile eşleşmeli).
            return SimpleNamespace(agent_model="deepseek-v4-pro",
                                   judge_model="llama-3.3-70b-versatile",
                                   agent_temperature=0.0)
        if name == "retrieval":
            return SimpleNamespace(hnsw_iterative_scan="relaxed_order")
        if name == "agent":
            return SimpleNamespace(temperature=0.0)
        return SimpleNamespace()


class _NullCtx:
    def __enter__(self):
        return object()  # sahte conn — evidence_precondition zaten yamalı

    def __exit__(self, *exc):
        return False


class _FakeDb:
    def open(self):
        return self

    def close(self):
        pass

    def connection(self):
        return _NullCtx()


@pytest.fixture(autouse=True)
def _patch_infra(monkeypatch):
    """DB/config kurulumunu sahteler; testler gerçek Postgres'e dokunmaz."""
    import ragintel.database as db_mod
    import ragintel.config.loader as loader_mod

    monkeypatch.setattr(db_mod, "Database", lambda settings: _FakeDb())
    monkeypatch.setattr(loader_mod, "load_config", lambda **kw: _FakeCfg())


def _fake_complete_result():
    return {
        "status": "complete", "judge": "fake-judge",
        "ragas": {"overall": {"faithfulness": 0.90, "context_precision": 0.90},
                  "per_question": [{}] * 3},
        "honesty": {"pass": 5, "total": 5},
        "dataset": {"answerable_run": 5, "unanswerable_run": 5,
                    "answered_with_context": 5, "errors": []},
    }


# --- (a) evidence tam → normal akışa devam, harness.evaluate GERÇEKTEN çağrılır --------
def test_evidence_tam_gate_normal_akisa_devam_eder(monkeypatch):
    monkeypatch.setattr(gates_mod, "evidence_precondition", lambda conn, version: None)

    calls = {"n": 0}

    def _fake_evaluate(**kwargs):
        calls["n"] += 1
        return _fake_complete_result()

    monkeypatch.setattr(harness_mod, "evaluate", _fake_evaluate)

    code = main_mod._cmd_gate(_args())

    assert calls["n"] == 1, "evidence tamken harness.evaluate GERÇEKTEN çağrılmalı (pozitif ön-koşul)"
    assert code == 0  # eşikler geçti → pass


# --- (b) evidence bozuk → exit 2, judge HİÇ ÇAĞRILMAZ ----------------------------------
def test_evidence_bozuk_exit2_judge_hic_cagrilmaz(monkeypatch):
    outcome = gates_mod.GateOutcome(
        2,
        "evidence çözülemedi (1/2 alıntı) — bu bir KALİTE REGRESYONU değil, "
        "ÖLÇÜM ZEMİNİNİN KAYMASIDIR. Judge ÇAĞRILMADI.",
    )
    monkeypatch.setattr(gates_mod, "evidence_precondition", lambda conn, version: outcome)

    calls = {"n": 0}

    def _fake_evaluate(**kwargs):
        calls["n"] += 1
        return _fake_complete_result()

    monkeypatch.setattr(harness_mod, "evaluate", _fake_evaluate)

    code = main_mod._cmd_gate(_args())

    assert code == 2, "evidence çözülemediğinde exit 2 (altyapı) dönmeli, exit 1 (kalite fail) DEĞİL"
    assert calls["n"] == 0, "judge/harness.evaluate HİÇ ÇAĞRILMAMALI (token harcanmadan durmalı)"


# --- exit 2 gerekçesi eyleme dönük ve Türkçe -------------------------------------------
def test_evidence_bozuk_gerekce_eyleme_donuk(monkeypatch, capsys):
    outcome = gates_mod.GateOutcome(
        2,
        "evidence çözülemedi (1/2 alıntı) — bu bir KALİTE REGRESYONU değil, "
        "ÖLÇÜM ZEMİNİNİN KAYMASIDIR. Judge ÇAĞRILMADI. "
        "Aksiyon: gs-v0-028 kaydını yeniden çıpalayıp v0.2 ile yükleyin.",
    )
    monkeypatch.setattr(gates_mod, "evidence_precondition", lambda conn, version: outcome)
    monkeypatch.setattr(harness_mod, "evaluate", lambda **kw: pytest.fail("çağrılmamalıydı"))

    code = main_mod._cmd_gate(_args())
    out = capsys.readouterr().out

    assert code == 2
    assert "gs-v0-028" in out
    assert "yeniden çıpala" in out or "yükleyin" in out


# --- gerçek evidence_precondition: golden set boşsa da exit 2 (pozitif dal) ------------
def test_evidence_precondition_bos_golden_set_exit2():
    class _EmptyRepo:
        @staticmethod
        def list_golden_records(conn, version):
            return []

    import ragintel.eval.repository as repo_mod
    import unittest.mock as mock

    with mock.patch.object(repo_mod, "list_golden_records", _EmptyRepo.list_golden_records):
        outcome = gates_mod.evidence_precondition(conn=object(), version="v-yok")

    assert outcome is not None and outcome.code == 2
    assert "v-yok" in outcome.reason


# --- gerçek evidence_precondition: tüm evidence çözülürse None (pozitif dal) -----------
def test_evidence_precondition_tam_cozulur_none_doner():
    import ragintel.eval.repository as repo_mod
    import ragintel.eval.retrieval_benchmark as rb_mod
    import unittest.mock as mock

    fake_records = [{"id": "gs-x-001", "question": "q", "ideal_answer": "a",
                      "category": "single_fact", "difficulty": 1,
                      "gold_evidence": [{"file_name": "f.pdf", "page": 1, "quote": "abc"}],
                      "doc_scope": "default", "answerable": True,
                      "created_by": "t", "notes": ""}]

    class _FakeMapping:
        unmapped = []
        total_evidence = 1
        mapped_evidence = 1

    with mock.patch.object(repo_mod, "list_golden_records", lambda conn, version: fake_records), \
         mock.patch.object(rb_mod, "map_gold_chunks", lambda conn, records: _FakeMapping()):
        outcome = gates_mod.evidence_precondition(conn=object(), version="v-tam")

    assert outcome is None

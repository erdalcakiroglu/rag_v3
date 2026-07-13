"""M-7 — Eval gate MODEL-ZEMİNİ ön-koşulu.

YAŞANAN OLAY (bu testler onun kilididir): `.env`'de kalmış bir provider-swap satırı
(`RAGINTEL_LLM_MODEL=deepseek-v4-pro`) karnenin agent'ını (qwen/qwen3-32b) SESSİZCE
ezdi. Gate koştu, sayı üretti (context_precision 0.775 → 0.630) ve bu bir "regresyon"
sanıldı — oysa BAŞKA BİR AGENT ölçülüyordu. Ne gate ne rapor bunu söylüyordu.

İki koruma:
  1. Model-adı önceliği DB-OTORİTER: CLI > DB(eval.agent_model) > .env > kod default.
     `.env` artık karnenin zeminini EZEMEZ.
  2. Gate, judge'a gitmeden ÖNCE koşacağı modelleri karneninkiyle kıyaslar; sapma varsa
     exit 2 (ALTYAPI — kalite fail=1 DEĞİL: zemin kaydı kalite regresyonu değildir).
"""

from __future__ import annotations

import pytest

from ragintel.eval.gates import effective_models, model_ground_precondition


class _Cfg:
    """Minimal EffectiveConfig ikamesi (group() döndüren)."""

    def __init__(self, agent="deepseek-v4-pro", judge="llama-3.3-70b-versatile",
                 iterative="relaxed_order"):
        from types import SimpleNamespace
        self._g = {
            "eval": SimpleNamespace(agent_model=agent, judge_model=judge),
            "retrieval": SimpleNamespace(hnsw_iterative_scan=iterative),
        }

    def group(self, name):
        return self._g[name]


def test_karne_modelleriyle_kosunca_gecer():
    """POZİTİF ÖN-KOŞUL: zemin doğruyken ön-koşul YOL VERİR. Bu olmadan aşağıdaki
    'sapmada durur' iddiası vacuous olurdu (ön-koşul her şeyi durduruyor olabilirdi)."""
    assert model_ground_precondition(_Cfg()) is None


def test_env_artik_karneyi_ezemez(monkeypatch):
    """DB-otoriterlik: `.env RAGINTEL_LLM_MODEL` set olsa BİLE efektif agent karneden gelir.

    Bu tam olarak yaşanan hatadır: eskiden .env kazanıyordu ve kimse fark etmiyordu.
    """
    monkeypatch.setenv("RAGINTEL_LLM_MODEL", "kacak-model")
    eff = effective_models(_Cfg(agent="deepseek-v4-pro"))
    assert eff["agent"] == "deepseek-v4-pro", ".env, DB'deki karne agent'ını EZEMEMELİ"
    assert model_ground_precondition(_Cfg(agent="deepseek-v4-pro")) is None


def test_cli_sapmasi_exit2_ile_durdurulur():
    """CLI ile başka model verilirse: exit 2 (ALTYAPI), exit 1 DEĞİL."""
    out = model_ground_precondition(_Cfg(agent="deepseek-v4-pro"), agent_model="qwen/qwen3-32b")
    assert out is not None
    assert out.code == 2, "zemin kayması KALİTE FAIL (1) değil ALTYAPI (2) olmalı"
    assert "MODEL ZEMİNİ KAYDI" in out.reason
    assert "qwen/qwen3-32b" in out.reason and "deepseek-v4-pro" in out.reason
    assert "Judge ÇAĞRILMADI" in out.reason


def test_judge_sapmasi_da_yakalanir():
    out = model_ground_precondition(_Cfg(), judge_model="baska-judge")
    assert out is not None and out.code == 2
    assert "judge:" in out.reason


def test_bilincli_sapma_allow_drift_ile_mumkun():
    """Keşif amaçlı ölçüm engellenmez — ama AÇIK bir bayrak ister (sessiz olamaz)."""
    assert model_ground_precondition(
        _Cfg(), agent_model="qwen/qwen3-32b", allow_drift=True) is None


def test_olcum_zemini_raporlanir():
    """Sayılar hangi zeminde üretildi? agent/judge/ANN semantiği çıktıya YAZILIR —
    bu satır olmadığı için sapma aylarca görünmez kalabiliyordu."""
    eff = effective_models(_Cfg(iterative="off"))
    assert eff["agent"] == "deepseek-v4-pro"
    assert eff["judge"] == "llama-3.3-70b-versatile"
    assert eff["iterative_scan"] == "off"      # retrieval semantiği de zeminin parçası


def test_oncelik_harness_ile_ayni():
    """gates.effective_models ile harness.build_eval_app AYNI önceliği uygulamalı;
    ayrışırsa rapor yalan söyler (raporda X yazar, koşumda Y çalışır)."""
    import inspect

    from ragintel.eval import harness
    src = inspect.getsource(harness.build_eval_app)
    # DB (cfg.group("eval").agent_model) .env'den (LiteLLMSettings) ÖNCE gelmeli
    i_db = src.index('cfg.group("eval").agent_model')
    i_env = src.index("LiteLLMSettings().model")
    assert i_db < i_env, "harness'ta DB, .env'den ÖNCE gelmeli (DB-otoriter)"

"""kol-2 (a) — tur sayacını sistem prompt'undan EN SON mesaja taşı (prefix disiplini).

Bu test ÖLÇÜT DEĞİL LATENCY değil; (a)'nın kabul kriterini kilitler (Brief_kol2 §1):
  - Sistem mesajı içeriği turlar arası BYTE-ÖZDEŞ (sayaç artık orada değil) → prefix cache
    yeniden-kullanılabilir olur (ÖN KOŞUL; ödül (b)'den sonra ölçülür).
  - Sayaç modele HÂLÂ iletiliyor, EN SON mesajda, her tur doğru değerle (tur-bütçesi korunur).
  - Sayaç GERÇEKTEN en sonda: retry turunda feedback trailing mesaj eklese bile ondan SONRA.

Davranış-nötr: (a) yalnız mesajların KONUMUNU değiştirir; hiçbir metni silmez/eklemez
(sayaç metni birebir korunur, yalnız sistem→sona taşınır).
"""
from __future__ import annotations

from ragintel.agents.nodes.agent import _assemble_messages


class _Grp:
    system_prompt = ""  # boş → load_system_prompt kod varsayılanına düşer (DB'ye bağımlı değil)


class _Cfg:
    """DB'siz sahte config: 'prompts' grubu yok (KeyError), 'agent'.system_prompt boş
    → load_system_prompt DEFAULT_SYSTEM_PROMPT döner. state'ten BAĞIMSIZ, deterministik."""
    def group(self, name):
        if name == "prompts":
            raise KeyError(name)
        return _Grp()


def _state(iteration: int, *, max_iter: int = 3, validation=None, messages=None) -> dict:
    return {
        "query": "Karbon vergisi nedir?",
        "budget": {"max_iterations": max_iter, "iteration": iteration},
        "messages": messages or [],
        "validation": validation,
    }


def test_system_mesaji_turlar_arasi_BYTE_OZDES():
    """Kabul-a çekirdeği: iterasyon değişse de sistem mesajı birebir aynı; sayaç orada YOK.
    Eski davranışta `[Kalan iterasyon: N]` sistemin içindeydi → her tur farklı → cache kırık."""
    cfg = _Cfg()
    m0 = _assemble_messages(_state(0), cfg, None)
    m1 = _assemble_messages(_state(1), cfg, None)
    assert m0[0]["role"] == "system" and m1[0]["role"] == "system"
    assert m0[0]["content"] == m1[0]["content"]        # BYTE-ÖZDEŞ
    assert "Kalan iterasyon" not in m0[0]["content"]   # sayaç sistemden ÇIKTI
    # Bağlam sabitken mesaj-2 (query/context) de turlar arası özdeş (a'nın kapsamı; b değil).
    assert m0[1]["content"] == m1[1]["content"]


def test_sayac_EN_SONDA_ve_her_tur_dogru_deger():
    """Sayaç modele iletilmeye devam eder (tur-bütçesi işlevi), en son mesajda, doğru değerle."""
    cfg = _Cfg()
    m1 = _assemble_messages(_state(1, max_iter=3), cfg, None)   # remaining = 3-1 = 2
    m2 = _assemble_messages(_state(2, max_iter=3), cfg, None)   # remaining = 3-2 = 1
    assert m1[-1]["content"] == "[Kalan iterasyon: 2]"
    assert m2[-1]["content"] == "[Kalan iterasyon: 1]"


def test_sayac_FEEDBACKTEN_SONRA_gelir_retry_turunda():
    """Hassas nokta #1: retry turunda _feedback_message trailing mesaj ekler; sayaç yine de
    ONDAN SONRA, mutlak en sonda olmalı (head + tail + feedback + [sayaç])."""
    cfg = _Cfg()
    validation = {"passed": False, "coverage": 0.667, "issues": ["low_coverage:0.667"]}
    m = _assemble_messages(_state(1, validation=validation), cfg, None)
    contents = [msg["content"] for msg in m]
    fb_idx = next(i for i, c in enumerate(contents) if "VALIDATION_FAILED" in c)
    counter_idx = next(i for i, c in enumerate(contents) if c.startswith("[Kalan iterasyon:"))
    assert counter_idx == len(m) - 1     # sayaç mutlak en sonda
    assert fb_idx < counter_idx          # feedback'ten SONRA


def test_normal_turda_da_sayac_en_sonda_feedback_YOK():
    """Karşı-örnek: doğrulama geçmiş/yokken feedback üretilmez ama sayaç yine en sonda kalır."""
    cfg = _Cfg()
    m = _assemble_messages(_state(0), cfg, None)
    assert not any("VALIDATION_FAILED" in msg["content"] for msg in m)
    assert m[-1]["content"] == "[Kalan iterasyon: 3]"   # remaining = 3-0

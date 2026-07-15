"""M-9 — örnekleme sıcaklığı: fallback varyansının KÖK KAYNAĞI + mühür zemini.

BULUNAN ARIZA (ölçüldü): gateway `temperature`'ı HİÇ set etmiyordu → uç Ollama
varsayılanına (0.8) düşüyordu. Sonuç: aynı soru koşudan koşuya %0–%60 fallback
veriyordu; k=3 karne mühürlenemedi (tekrar-grupları %16–%55 savruluyordu).

İKİ KATMANLI BULGU (kontrollü deney, 3 soru × temp{0.0,0.4,0.8} × k=5):
  1. temp=0.0 → her soru 5/5 AYNI sonuç (deterministik). Varyans kökten sıfırlandı.
  2. temp gerçek bug'ları MASKELİYORDU: gs-007 temp=0'da 0/5 (sağlam) ama 0.8'de %60
     kalıyordu (haksız zarar); gs-022 temp=0'da 5/5 fallback (gerçekten kırık) ama 0.8'de
     ara sıra 'geçiyordu' (şanslı sampling = yanlış pozitif). Determinizm, 'şansla geçen'
     ile 'gerçekten çözülen'i ayırdı.

Grounded soru-cevapta 'yaratıcılık' değersiz; tekrarlanabilirlik mühür şartı → 0.0.
"""

from __future__ import annotations

import pytest

from ragintel.config.settings import AgentConfig, EvalConfig
from ragintel.llm.gateway import LiteLLMGateway
from ragintel.config.settings import LiteLLMSettings


# --- config varsayılanı -------------------------------------------------------
def test_agent_sicakligi_varsayilan_deterministik():
    assert AgentConfig().temperature == 0.0


def test_eval_zemin_sicakligi_varsayilan_deterministik():
    """Karne zemini de 0.0 — mühür deterministik bir zeminde açılır."""
    assert EvalConfig().agent_temperature == 0.0


def test_sicaklik_araligi_sinirli():
    """Yapısal sınır: negatif ya da 2.0 üstü sıcaklık reddedilir (config yalan söylemez)."""
    with pytest.raises(Exception):
        AgentConfig(temperature=-0.1)
    with pytest.raises(Exception):
        AgentConfig(temperature=2.5)


# --- gateway sıcaklığı AÇIKÇA geçirir -----------------------------------------
def test_gateway_sicakligi_ACIKCA_gecirir(monkeypatch):
    """EN KRİTİK: sıcaklık set edilmezse uç 0.8'e düşer ve zemin sessizce kayar.
    Bu test, sıcaklığın HER çağrıda gövdeye açıkça girdiğini kilitler."""
    captured = {}

    class _FakeLiteLLM:
        @staticmethod
        def completion(**kw):
            captured.update(kw)
            raise RuntimeError("dur")

    monkeypatch.setitem(__import__("sys").modules, "litellm", _FakeLiteLLM)
    gw = LiteLLMGateway(model="qwen3.5:35b",
                        settings=LiteLLMSettings(api_base="http://h200", max_retries=0),
                        temperature=0.0)
    with pytest.raises(RuntimeError):
        gw.complete(messages=[{"role": "user", "content": "s"}], tools=[])
    assert captured["temperature"] == 0.0, "sıcaklık gövdede AÇIKÇA olmalı (uç varsayılanına düşmemeli)"


def test_gateway_varsayilan_sicaklik_deterministik():
    """Karşı-örnek: temperature verilmezse gateway 0.0 varsayar (uç 0.8'i DEĞİL)."""
    gw = LiteLLMGateway(model="m", settings=LiteLLMSettings(api_base="http://x"))
    assert gw.temperature == 0.0

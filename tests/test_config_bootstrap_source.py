"""Bootstrap ayarları .env-authoritative: OS ortam değişkenleri YOK SAYILIR.

KARAR: bağlantı/secret bilgisi yalnızca .env (+ init kwarg + kod varsayılanı).
Böylece host makinede kalmış bir RAGINTEL_DB_PASSWORD (stale env footgun) .env'i
EZEMEZ. Davranışsal pipeline config bu değişiklikten etkilenmez (ayrı katman).
"""

from __future__ import annotations

from ragintel.config.settings import DbSettings, LiteLLMSettings, OllamaSettings, TeiSettings


def test_db_settings_ignores_os_environ(monkeypatch):
    monkeypatch.setenv("RAGINTEL_DB_PASSWORD", "BOGUS_OS_ENV_PW")
    monkeypatch.setenv("RAGINTEL_DB_HOST", "bogus.host.invalid")
    s = DbSettings()
    # OS env değerleri kesinlikle kullanılmamalı (.env veya kod varsayılanı gelir).
    assert s.password != "BOGUS_OS_ENV_PW"
    assert s.host != "bogus.host.invalid"


def test_all_bootstrap_settings_ignore_os_environ(monkeypatch):
    monkeypatch.setenv("RAGINTEL_OLLAMA_BASE_URL", "http://bogus-ollama:1")
    monkeypatch.setenv("RAGINTEL_TEI_RERANK_URL", "http://bogus-tei:1")
    monkeypatch.setenv("RAGINTEL_LLM_API_BASE", "http://bogus-llm:1")
    assert OllamaSettings().base_url != "http://bogus-ollama:1"
    assert TeiSettings().rerank_url != "http://bogus-tei:1"
    assert LiteLLMSettings().api_base != "http://bogus-llm:1"


def test_init_kwarg_still_wins_over_everything(monkeypatch):
    # Testlerin/servislerin ayarı programatik enjekte edebilmesi şart.
    monkeypatch.setenv("RAGINTEL_TEI_RERANK_URL", "http://os-env:9999")
    assert TeiSettings(rerank_url="http://init:1234").rerank_url == "http://init:1234"
    assert DbSettings(password="explicit").password == "explicit"

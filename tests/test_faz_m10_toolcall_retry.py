"""M-10/0 — gateway tool-call JSON parse retry (model-agnostik dayanıklılık katmanı).

Model bozuk tool-call JSON üretince (Ollama/litellm "failed to parse JSON: invalid character …"
ya da gateway json.loads → JSONDecodeError) → AYRI retry (RAGINTEL_LLM_TOOLCALL_RETRIES,
varsayılan 2), rate-limit retry'sinden BAĞIMSIZ. Her deneme `tool_call_parse_error` diye AÇIK
loglanır. Retry tükenirse yükselir → runtime.ask fallback'i. Gerçek Ollama GEREKMEZ (litellm mock).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ragintel.config.settings import LiteLLMSettings
from ragintel.llm.gateway import LiteLLMGateway, is_toolcall_parse_error

PARSE_MSG = ("litellm.InternalServerError: OpenAIException - failed to parse JSON: "
             "invalid character '1' after object key:value pair")


# --------------------------------------------------------------- sahte litellm yanıtı
def _msg(args):
    tc = SimpleNamespace(id="tc1", type="function",
                         function=SimpleNamespace(name="submit_answer", arguments=args))
    dumped_args = args if isinstance(args, (str, dict)) else "{}"
    return SimpleNamespace(
        tool_calls=[tc], content=None,
        model_dump=lambda: {"role": "assistant", "content": None,
                            "tool_calls": [{"id": "tc1", "type": "function",
                                            "function": {"name": "submit_answer", "arguments": dumped_args}}]})


def _resp(args):
    return SimpleNamespace(choices=[SimpleNamespace(message=_msg(args))],
                           usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
                           _hidden_params={})


class _Seq:
    """Sıralı davranış: her çağrı ya Exception fırlatır ya sahte yanıt döner."""
    def __init__(self, behaviors):
        self.behaviors = behaviors; self.calls = 0
    def __call__(self, **kwargs):
        b = self.behaviors[min(self.calls, len(self.behaviors) - 1)]; self.calls += 1
        if isinstance(b, BaseException):
            raise b
        return b


def _gw(retries=2):
    return LiteLLMGateway(model="qwen3.5:35b",
                          settings=LiteLLMSettings(toolcall_retries=retries, api_base="http://x", provider="openai"))


def _patch(monkeypatch, seq):
    import litellm
    monkeypatch.setattr(litellm, "completion", seq)
    return seq


class _RecLog:
    """structlog PrintLogger stdlib'e gitmiyor → caplog yakalamaz; _LOG'u bununla değiştiririz."""
    def __init__(self): self.events = []
    def warning(self, event, **kw): self.events.append((event, kw))
    def info(self, *a, **k): pass
    def error(self, *a, **k): pass


def _rec_log(monkeypatch):
    import ragintel.llm.gateway as gw
    rec = _RecLog(); monkeypatch.setattr(gw, "_LOG", rec); return rec


# --------------------------------------------------------------- birim: tespit
def test_is_toolcall_parse_error_detects_signatures():
    assert is_toolcall_parse_error(json.JSONDecodeError("x", "y", 0)) is True
    assert is_toolcall_parse_error(RuntimeError("... failed to parse JSON: invalid character '1' ...")) is True
    assert is_toolcall_parse_error(RuntimeError("OpenAIException - invalid character 'H' after ...")) is True
    assert is_toolcall_parse_error(RuntimeError("Rate limit reached, try again in 5s")) is False
    assert is_toolcall_parse_error(ValueError("random")) is False


def test_config_default_is_2():
    assert LiteLLMSettings().toolcall_retries == 2


# --------------------------------------------------------------- 1. hata → 2. temiz → PASS
def test_internalservererror_parsemsg_then_clean(monkeypatch):
    seq = _patch(monkeypatch, _Seq([RuntimeError(PARSE_MSG), _resp({"answer": "Finlandiya", "citations": []})]))
    out = _gw().complete(messages=[{"role": "user", "content": "q"}], tools=[])
    assert seq.calls == 2                                       # 1 hata + 1 temiz
    assert out.tool_calls[0].arguments == {"answer": "Finlandiya", "citations": []}


def test_jsondecode_path_then_clean(monkeypatch):
    # 1. koşum: tool-call arguments BOZUK string → gateway json.loads → JSONDecodeError
    seq = _patch(monkeypatch, _Seq([_resp('{"answer": "x" 1 bozuk'), _resp({"answer": "ok"})]))
    out = _gw().complete(messages=[{"role": "user", "content": "q"}], tools=[])
    assert seq.calls == 2
    assert out.tool_calls[0].arguments == {"answer": "ok"}


# --------------------------------------------------------------- hep-hata → yükselir + log
def test_always_parse_error_exhausts_and_raises_with_logs(monkeypatch):
    seq = _patch(monkeypatch, _Seq([RuntimeError(PARSE_MSG)]))   # her çağrı hata
    rec = _rec_log(monkeypatch)
    with pytest.raises(Exception) as exc:
        _gw(retries=2).complete(messages=[{"role": "user", "content": "q"}], tools=[])
    assert is_toolcall_parse_error(exc.value)                    # yükselen hata parse-hatası (→ runtime fallback)
    assert seq.calls == 3                                        # 1 ilk + 2 retry (toolcall_retries=2)
    logs = [kw for e, kw in rec.events if e == "tool_call_parse_error"]
    assert len(logs) == 2                                        # her retry AÇIK loglandı
    assert [l["attempt"] for l in logs] == [1, 2]               # deneme numarası doğru


def test_retries_zero_no_retry(monkeypatch):
    seq = _patch(monkeypatch, _Seq([RuntimeError(PARSE_MSG)]))
    with pytest.raises(Exception):
        _gw(retries=0).complete(messages=[{"role": "user", "content": "q"}], tools=[])
    assert seq.calls == 1                                        # retries=0 → tek deneme, retry yok


# --------------------------------------------------------------- parse-hata rate-limit döngüsünde retry EDİLMEZ
def test_parse_error_bypasses_transient_retry(monkeypatch):
    """_completion (rate-limit döngüsü) parse-hatasını ANINDA yükseltir (max_retries beklemez)
    → dış tool-call döngüsüne bırakır. InternalServerError is_retryable=True olsa da parse ise HARİÇ."""
    seq = _patch(monkeypatch, _Seq([RuntimeError(PARSE_MSG)]))
    gw = _gw()
    with pytest.raises(Exception):
        gw._completion({"model": "x", "messages": [], "tools": None})
    assert seq.calls == 1                                        # backoff-retry YOK → tek çağrı


def test_clean_first_try_no_retry(monkeypatch):
    seq = _patch(monkeypatch, _Seq([_resp({"answer": "hemen temiz"})]))
    out = _gw().complete(messages=[{"role": "user", "content": "q"}], tools=[])
    assert seq.calls == 1 and out.tool_calls[0].arguments == {"answer": "hemen temiz"}

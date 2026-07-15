"""LiteLLM sarmalayıcı: agent LLM çağrısı (Ollama), tool-calling + token sayımı.

Token sayımı KURAL (İP-5, netleştirildi): `tokens_used`, LiteLLM'in tiktoken
tabanlı sayacına DEĞİL, Ollama yanıt metadata'sına (`prompt_eval_count` /
`eval_count`) bağlanır. LiteLLM bu native sayıları `usage.prompt_tokens` /
`usage.completion_tokens` olarak geçirir; tiktoken yolu Ollama usage döndürdüğü
için tetiklenmez. `ragintel/**` içinde `import tiktoken` YASAK (guard testi).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from ..config.settings import LiteLLMSettings
from ..observability.logging import get_logger
from ..observability.tracing import set_span_attributes

_LOG = get_logger("llm.gateway")

# Sağlayıcı "try again in Xs" ipucu (Groq/OpenAI rate-limit mesajı) → Retry-After.
_RETRY_HINT = re.compile(r"(?:try again in|retry after)\s+([0-9.]+)\s*s", re.IGNORECASE)


def is_retryable(exc: Exception) -> bool:
    """Rate-limit / geçici ağ-sunucu hataları retry edilir (BadRequest/şema DEĞİL)."""
    import litellm

    return isinstance(
        exc,
        (
            litellm.RateLimitError,
            litellm.APIConnectionError,
            litellm.Timeout,
            litellm.InternalServerError,
            litellm.ServiceUnavailableError,
        ),
    )


def retry_wait_seconds(exc: Exception, attempt: int, *, base: float = 2.0, cap: float = 60.0) -> float:
    """Retry-After ipucu varsa ona uy (küçük tampon); yoksa üstel backoff."""
    m = _RETRY_HINT.search(str(exc))
    if m:
        try:
            return min(float(m.group(1)) + 0.8, cap)
        except ValueError:
            pass
    return min(base * (2**attempt), cap)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMTiming:
    """Bir LLM çağrısının süre dökümü. `latency_ms` her zaman doludur (duvar
    saati, istemcinin gördüğü gerçek bekleme). Ollama native alanları (ns→ms
    çevrilir) yalnızca sunucu döndürürse dolar; aksi halde None kalır."""

    latency_ms: float                     # istemci duvar saati (litellm.completion etrafı)
    load_ms: float | None = None          # Ollama load_duration (modeli belleğe yükleme)
    prompt_eval_ms: float | None = None   # Ollama prompt_eval_duration (~TTFT, prompt işleme)
    eval_ms: float | None = None          # Ollama eval_duration (token üretimi)
    total_ms: float | None = None         # Ollama total_duration (sunucu ucu toplam)

    def as_dict(self) -> dict[str, float]:
        return {k: round(v, 1) for k, v in vars(self).items() if v is not None}


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall]
    prompt_tokens: int          # Ollama prompt_eval_count (LiteLLM passthrough)
    completion_tokens: int      # Ollama eval_count (LiteLLM passthrough)
    raw_message: dict           # LiteLLM/OpenAI biçimli assistant mesajı (geçmiş için)
    timing: LLMTiming | None = None

    @property
    def total_tokens(self) -> int:
        return int(self.prompt_tokens) + int(self.completion_tokens)


class LLMGateway(Protocol):
    def complete(self, *, messages: list[dict], tools: list[dict]) -> LLMResponse: ...


class EmptyReasoningResponse(RuntimeError):
    """M-9: model YALNIZCA düşündü — ne metin ne araç çağrısı üretti (sessiz boş cevap)."""


def _reasoning_of(message) -> str:
    for attr in ("reasoning", "reasoning_content", "thinking"):
        val = getattr(message, attr, None)
        if val:
            return str(val)
    return ""


def _assert_not_silently_empty(message, tool_calls: list, *, model: str) -> None:
    """M-9 REGRESYON KİLİDİ: düşünen model SESSİZCE boş cevap dönemez.

    KAPSAM ÖNEMLİ — "content boş + reasoning dolu" TEK BAŞINA hata DEĞİLDİR:
    agent, cevabı `submit_answer` TOOL argümanlarıyla teslim eder (prompt: "düz metin
    yazma"). Yani NORMAL akışta content HER TURDA boştur ve reasoning doludur; bu kural
    öylece yazılsaydı sağlıklı sistemde her turda ateşlenirdi (ölçüldü: arama ve nihai
    cevap turlarının İKİSİNDE de tool-call 5/5, content 0 karakter).

    Gerçek arıza şudur: model ne ARAÇ ÇAĞIRDI ne de METİN üretti — sadece düşündü.
    O zaman yukarıdaki katman elinde hiçbir şey olmadan devam eder ve kullanıcıya
    sessizce boş/uydurma cevap gider. Bunu AÇIK hataya çeviriyoruz.
    """
    if tool_calls or (getattr(message, "content", None) or "").strip():
        return
    reasoning = _reasoning_of(message)
    if not reasoning:
        return          # ne düşünce ne çıktı → başka bir arıza (yukarısı ele alır)
    _LOG.error("llm_empty_answer_reasoning_only", model=model,
               reasoning_chars=len(reasoning), reasoning_head=reasoning[:180])
    raise EmptyReasoningResponse(
        f"Model ({model}) yalnızca düşündü: araç çağrısı YOK, metin YOK, "
        f"reasoning {len(reasoning)} karakter. Token bütçesi düşünmeye harcanmış olabilir "
        f"(agent.max_tokens) ya da `agent.reasoning_effort='none'` gerekiyor. "
        f"Sessiz boş cevap yerine AÇIK hata (M-9)."
    )


class LiteLLMGateway:
    """Gerçek LiteLLM → Ollama gateway. Model adı config-first (`agent.model`);
    bağlantı LiteLLMSettings'ten (RAGINTEL_LLM_*)."""

    def __init__(self, *, model: str, settings: LiteLLMSettings | None = None,
                 reasoning_effort: str = "default", temperature: float = 0.0):
        self.model = model
        self.settings = settings or LiteLLMSettings()
        # M-9: düşünen model kontrolü (config-first: `agent.reasoning_effort`).
        self.reasoning_effort = reasoning_effort
        # M-9: örnekleme sıcaklığı (config-first: `agent.temperature`). 0.0 = deterministik.
        # Ölçüldü: set edilmeyince uç 0.8'e düşüyor, fallback varyansının kök kaynağı buydu.
        self.temperature = temperature

    def complete(self, *, messages: list[dict], tools: list[dict]) -> LLMResponse:
        import litellm

        kwargs: dict = {
            "model": f"{self.settings.provider}/{self.model}",
            "messages": messages,
            "tools": tools or None,
            "api_base": self.settings.api_base,
            "timeout": self.settings.request_timeout,
            # M-9: sıcaklık AÇIKÇA geçirilir — set edilmezse uç kendi varsayılanına (0.8)
            # düşer ve karne zemini sessizce kayar. Config yalan söylemez: davranışı
            # belirleyen parametre çağrıda görünür.
            "temperature": self.temperature,
        }
        # M-9: düşünen modelde (qwen3.5:35b) akıl yürütmeyi kıs/kapat.
        # `extra_body` ŞART: LiteLLM'in `openai` sağlayıcısı `reasoning_effort`'ü
        # doğrudan REDDEDER (UnsupportedParamsError); extra_body ise gövdeye
        # olduğu gibi geçer. (`think:false` ve `chat_template_kwargs` uçta YOK SAYILIYOR
        # — ölçüldü; işe yarayan tek yol budur.)
        if self.reasoning_effort and self.reasoning_effort != "default":
            kwargs["extra_body"] = {"reasoning_effort": self.reasoning_effort}
        # Auth'lu OpenAI-compat uç (ör. Open WebUI/H200 /ollama/v1) → Bearer token.
        if self.settings.api_key:
            kwargs["api_key"] = self.settings.api_key

        # Rate-limit/geçici hata → Retry-After'a uyan backoff'lu retry (maliyet/ratelimit gözetimi).
        resp = None
        latency_ms = 0.0
        for attempt in range(self.settings.max_retries + 1):
            t0 = time.perf_counter()
            try:
                resp = litellm.completion(**kwargs)
                latency_ms = (time.perf_counter() - t0) * 1000.0
                break
            except Exception as exc:
                if attempt < self.settings.max_retries and is_retryable(exc):
                    wait = retry_wait_seconds(exc, attempt)
                    _LOG.warning("llm_retry", attempt=attempt + 1, wait=round(wait, 1),
                                 error=type(exc).__name__)
                    time.sleep(wait)
                    continue
                raise
        message = resp.choices[0].message
        tool_calls: list[ToolCall] = []
        for tc in message.tool_calls or []:
            raw_args = tc.function.arguments or "{}"
            args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        _assert_not_silently_empty(message, tool_calls, model=self.model)

        usage = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

        timing = _build_timing(resp, latency_ms)
        # generation hızı: eval süresi varsa native'den, yoksa duvar saatinden tahmin.
        gen_ms = timing.eval_ms or latency_ms
        tok_per_s = round(completion_tokens / (gen_ms / 1000.0), 1) if completion_tokens and gen_ms else None
        _LOG.info(
            "llm_call_timing",
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            tokens_per_sec=tok_per_s,
            **timing.as_dict(),
        )
        set_span_attributes(
            **{f"llm.{k}": v for k, v in timing.as_dict().items()},
            **({"llm.tokens_per_sec": tok_per_s} if tok_per_s is not None else {}),
        )

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            raw_message=_sanitize_assistant_message(message),
            timing=timing,
        )


def _sanitize_assistant_message(message) -> dict:
    """Asistan mesajını YALNIZCA OpenAI-standart alanlara indirger (geçmişe eklenip
    sonraki turda geri gönderilecek). litellm `model_dump()` sağlayıcıya özgü alanlar
    ekler (ör. `provider_specific_fields`) — Groq gibi katı OpenAI-compat uçlar bunları
    reddeder (H200 Open WebUI proxy'si hoşgörülüydü). Sadece role/content/tool_calls
    tutulur; tool_call'lar da id/type/function{name,arguments}'a sadeleştirilir."""
    raw = message.model_dump() if hasattr(message, "model_dump") else dict(message)
    msg: dict = {"role": raw.get("role") or "assistant"}
    tool_calls = raw.get("tool_calls") or []
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.get("id"),
                "type": tc.get("type") or "function",
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"],
                },
            }
            for tc in tool_calls
        ]
    content = raw.get("content")
    # OpenAI: tool_calls'lı asistan mesajında content null olabilir; yoksa boş string.
    if content is not None:
        msg["content"] = content
    elif not tool_calls:
        msg["content"] = ""
    return msg


# Ollama süre alanları nanosaniyedir; native_field -> LLMTiming alanı eşlemesi.
_OLLAMA_DURATION_FIELDS = {
    "total_duration": "total_ms",
    "load_duration": "load_ms",
    "prompt_eval_duration": "prompt_eval_ms",
    "eval_duration": "eval_ms",
}


def _iter_candidate_containers(resp: Any):
    """Ollama native metadata'sının litellm yanıtında saklanabileceği yerleri gezer.
    litellm sürümüne göre alanlar `_hidden_params`, pydantic `model_extra` veya
    doğrudan resp üstünde olabilir — hepsini savunmacı tara."""
    yield resp
    for attr in ("_hidden_params", "model_extra"):
        c = getattr(resp, attr, None)
        if isinstance(c, dict):
            yield c
    # bazı litellm sürümleri ham yanıtı iç sözlükte tutar
    hp = getattr(resp, "_hidden_params", None)
    if isinstance(hp, dict):
        for v in hp.values():
            if isinstance(v, dict):
                yield v


def _lookup(containers, key: str):
    for c in containers:
        if isinstance(c, dict) and key in c:
            return c[key]
        val = getattr(c, key, None)
        if val is not None:
            return val
    return None


def _build_timing(resp: Any, latency_ms: float) -> LLMTiming:
    """Duvar saati + (varsa) Ollama native süre dökümü. Native yoksa yalnızca
    `latency_ms` dolar (proxy/OpenAI-compat uç native süreyi kırpabilir)."""
    timing = LLMTiming(latency_ms=round(latency_ms, 1))
    containers = list(_iter_candidate_containers(resp))
    for native, attr in _OLLAMA_DURATION_FIELDS.items():
        ns = _lookup(containers, native)
        try:
            if ns is not None:
                setattr(timing, attr, float(ns) / 1e6)  # ns -> ms
        except (TypeError, ValueError):
            continue
    return timing

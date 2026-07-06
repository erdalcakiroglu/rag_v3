"""LiteLLM sarmalayıcı: agent LLM çağrısı (Ollama), tool-calling + token sayımı.

Token sayımı KURAL (İP-5, netleştirildi): `tokens_used`, LiteLLM'in tiktoken
tabanlı sayacına DEĞİL, Ollama yanıt metadata'sına (`prompt_eval_count` /
`eval_count`) bağlanır. LiteLLM bu native sayıları `usage.prompt_tokens` /
`usage.completion_tokens` olarak geçirir; tiktoken yolu Ollama usage döndürdüğü
için tetiklenmez. `ragintel/**` içinde `import tiktoken` YASAK (guard testi).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from ..config.settings import LiteLLMSettings


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall]
    prompt_tokens: int          # Ollama prompt_eval_count (LiteLLM passthrough)
    completion_tokens: int      # Ollama eval_count (LiteLLM passthrough)
    raw_message: dict           # LiteLLM/OpenAI biçimli assistant mesajı (geçmiş için)

    @property
    def total_tokens(self) -> int:
        return int(self.prompt_tokens) + int(self.completion_tokens)


class LLMGateway(Protocol):
    def complete(self, *, messages: list[dict], tools: list[dict]) -> LLMResponse: ...


class LiteLLMGateway:
    """Gerçek LiteLLM → Ollama gateway. Model adı config-first (`agent.model`);
    bağlantı LiteLLMSettings'ten (RAGINTEL_LLM_*)."""

    def __init__(self, *, model: str, settings: LiteLLMSettings | None = None):
        self.model = model
        self.settings = settings or LiteLLMSettings()

    def complete(self, *, messages: list[dict], tools: list[dict]) -> LLMResponse:
        import litellm

        resp = litellm.completion(
            model=f"{self.settings.provider}/{self.model}",
            messages=messages,
            tools=tools or None,
            api_base=self.settings.api_base,
            timeout=self.settings.request_timeout,
        )
        message = resp.choices[0].message
        tool_calls: list[ToolCall] = []
        for tc in message.tool_calls or []:
            raw_args = tc.function.arguments or "{}"
            args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        usage = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            raw_message=message.model_dump() if hasattr(message, "model_dump") else dict(message),
        )

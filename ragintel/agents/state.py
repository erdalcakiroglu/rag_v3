"""FAZ 4 agent state kontratları.

Mesaj kanalı LiteLLM/OpenAI biçimli dict'ler tutar (`{"role","content",...}`);
reducer basit liste birleştirmesidir (`operator.add`) — LangChain mesaj
nesnelerine coercion yapılmaz, böylece gateway'e doğrudan verilebilir. State
şeması graph katmanından bağımsız olarak import edilebilir.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from ..retrieval.types import ContextBuildResult, RetrievedChunk, UserContext


def merge_messages(left: list | None, right: list | None) -> list:
    """Mesaj kanalı reducer'ı — liste birleştirme + TUR SIFIRLAMA.

    `right is None` bir SIFIRLAMA sinyalidir: yeni tur başında (prepare) agent
    scratchpad'i temizlenir. Aksi halde `operator.add` gibi birleştirir. Sıfırlama
    olmadan, kalıcı session (thread) altında önceki turun ham tool-call/tool-result
    mesajları birikip prompt'u şişirir ve modeli zehirler (çok-turlu regresyon)."""
    if right is None:
        return []
    return list(left or []) + list(right or [])


class Citation(TypedDict):
    claim: str
    chunk_id: int
    quote: str


class ValidationResult(TypedDict):
    passed: bool
    coverage: float
    issues: list[str]


class Budget(TypedDict):
    iteration: int
    max_iterations: int
    tokens_used: int
    max_tokens: int
    deadline_ts: float


class AgentState(TypedDict):
    # girdi (immutable)
    query: str
    user_ctx: UserContext
    session_id: str
    # çalışma alanı (TUR-YEREL: prepare her turda sıfırlar — çapraz-tur zehirlenme yok)
    messages: Annotated[list, merge_messages]  # LiteLLM biçimli tur scratchpad'i
    retrieved: list[RetrievedChunk]            # bu turda birikimli, dedup — TAM havuz (teşhis)
    context: ContextBuildResult | None         # context builder çıktısı; validate/compose GİRDİSİ
    pending_tool_calls: list[dict] | None      # agent → tools node arası bekleyen çağrılar
    # çıktı adayları
    draft_answer: str | None
    citations: list[Citation]
    validation: ValidationResult | None
    # kontrol
    budget: Budget
    retry_count: int
    # nihai çıktı (compose/fallback yazar)
    final_response: dict | None

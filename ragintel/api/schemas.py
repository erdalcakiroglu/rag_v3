"""API sözleşmeleri. FinalResponse = Tasarim_FAZ4 §5 BİREBİR (değiştirilmez).

`extra='forbid'` ile şema sürüklenmesi (drift) test tarafından yakalanır.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n: int
    file_name: str
    page: int | None = None
    section: str | None = None
    chunk_id: int
    quote: str


class Meta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    iterations: int
    tokens: int
    latency_ms: int
    model: str
    trace_id: str
    generated_at: int | None = None   # bizim eklentimiz (§5 dışı, opsiyonel)


class FinalResponse(BaseModel):
    """Tasarim_FAZ4 §5 — API'nin dış sözleşmesi."""
    model_config = ConfigDict(extra="forbid")
    answer: str
    sources: list[Source]
    confidence: Literal["high", "medium", "low"]
    followups: list[str]
    meta: Meta


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    session_id: str | None = None


class FeedbackRequest(BaseModel):
    session_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    rating: Literal[1, -1]
    comment: str | None = None

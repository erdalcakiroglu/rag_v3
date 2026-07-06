"""FAZ 4 ile hizali retrieval kontrat tipleri (İP-3.0)."""

from __future__ import annotations

from datetime import date
from typing import Literal, NotRequired, TypedDict


class UserContext(TypedDict):
    user_id: str
    tenant_id: str
    roles: list[str]
    allowed_doc_scopes: list[str]


class RetrievedSource(TypedDict):
    file_id: int
    file_name: str
    page: int | None
    section: str | None
    version: int


class RetrievedChunk(TypedDict):
    chunk_id: int
    text: str
    score: float
    source: RetrievedSource
    retrieval_method: Literal["vector", "hybrid", "lookup"]


class RetrievalFilters(TypedDict):
    file_type: NotRequired[str]
    language: NotRequired[str]
    date_from: NotRequired[date | str]
    date_to: NotRequired[date | str]
    section: NotRequired[str]


class RerankResult(TypedDict):
    chunk_id: int
    rerank_score: float


class ContextCitation(TypedDict):
    n: int
    file_name: str
    page: int | None
    sheet: str | None
    section: str | None
    chunk_id: int


class ContextBlock(TypedDict):
    n: int
    label: str
    text: str
    chunk_ids: list[int]
    token_count: int
    low_quality: bool


class ContextBuildResult(TypedDict):
    blocks: list[ContextBlock]
    citations: list[ContextCitation]
    dropped_chunk_ids: list[int]

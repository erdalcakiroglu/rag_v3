"""API sözleşmeleri. FinalResponse = Tasarim_FAZ4 §5 BİREBİR (değiştirilmez).

`extra='forbid'` ile şema sürüklenmesi (drift) test tarafından yakalanır.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TableRef(BaseModel):
    """M-2: chunk tablo-kökenliyse kaynağın işaret ettiği tablo (+ M-1 alt-chunk'ında
    satır aralığı). Aralık gövde satırlarına göre 1-tabanlı ve kapsayıcıdır."""
    model_config = ConfigDict(extra="forbid")
    table_id: int
    row_start: int | None = None
    row_end: int | None = None


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n: int
    file_name: str
    page: int | None = None
    section: str | None = None
    chunk_id: int
    quote: str
    # M-2: tablo-kökenli kaynaklarda dolu; diğerlerinde None (davranış değişmez).
    # Additive — extra='forbid' şemaya bilinçli eklendi (reviewed_sources ile aynı gerekçe).
    table_ref: TableRef | None = None


class Meta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    iterations: int
    tokens: int
    latency_ms: int
    model: str
    trace_id: str
    generated_at: int | None = None   # bizim eklentimiz (§5 dışı, opsiyonel)
    # FAZ 5: reddedilen/fallback yanıtta İNCELENEN ama cevabı desteklemeyen chunk'lar.
    # `sources` yalnızca cevabı DESTEKLEYEN kanıttır; declined yolunda sources=[] olur ve
    # incelenen chunk'lar buraya taşınır (citation DEĞİL — UI ayrı etiketler). Additive,
    # geriye-uyumlu (varsayılan boş; extra=forbid'e bilinçli eklendi — §5-v2 revizyonu).
    reviewed_sources: list[Source] = Field(default_factory=list)
    # FAZ 6 P2: yanıt+quote'larda maskelenen PII adedi (izlenebilirlik; additive).
    pii_masked_count: int = 0


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

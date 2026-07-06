"""İP-3.4 context builder: dedup, komşu birleştirme, bütçe ve citation haritası."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..config.loader import EffectiveConfig, load_config
from ..database.config_store import make_db_reader
from ..ingestion.chunking import BGEM3TokenCounter, TokenCounter
from ..observability.tracing import set_span_attributes, start_span
from .types import ContextBlock, ContextBuildResult, ContextCitation, RetrievedChunk


class ContextMetadata(Protocol):
    chunk_id: int
    file_id: int
    chunk_index: int
    token_count: int
    page_number: int | None
    sheet_name: str | None
    section_title: str | None
    quality_score: float | None


class ContextMetadataStore(Protocol):
    def fetch(self, chunk_ids: list[int]) -> dict[int, ContextMetadata]: ...


@dataclass
class _ChunkMeta:
    chunk_id: int
    file_id: int
    chunk_index: int
    token_count: int
    page_number: int | None
    sheet_name: str | None
    section_title: str | None
    quality_score: float | None


class DbContextMetadataStore:
    def __init__(self, db):
        self.db = db

    def fetch(self, chunk_ids: list[int]) -> dict[int, _ChunkMeta]:
        if not chunk_ids:
            return {}
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    c.chunk_id,
                    c.file_id,
                    c.chunk_index,
                    c.token_count,
                    c.page_number,
                    c.sheet_name,
                    c.section_title,
                    f.quality_score
                FROM core_chunks c
                JOIN core_files f USING (file_id)
                WHERE c.chunk_id = ANY(%s);
                """,
                (chunk_ids,),
            ).fetchall()
        return {
            row[0]: _ChunkMeta(
                chunk_id=row[0],
                file_id=row[1],
                chunk_index=row[2],
                token_count=row[3],
                page_number=row[4],
                sheet_name=row[5],
                section_title=row[6],
                quality_score=None if row[7] is None else float(row[7]),
            )
            for row in rows
        }


@dataclass
class _SelectedChunk:
    order: int
    chunk: RetrievedChunk
    meta: ContextMetadata

    @property
    def score(self) -> float:
        return float(self.chunk["score"])

    @property
    def file_id(self) -> int:
        return int(self.chunk["source"]["file_id"])


@dataclass
class _Block:
    chunks: list[_SelectedChunk]

    @property
    def score(self) -> float:
        return max(item.score for item in self.chunks)

    @property
    def order(self) -> int:
        return min(item.order for item in self.chunks)

    @property
    def chunk_ids(self) -> list[int]:
        return [item.chunk["chunk_id"] for item in self.chunks]

    @property
    def token_count(self) -> int:
        return sum(int(item.meta.token_count) for item in self.chunks)

    def text(self) -> str:
        return "\n".join(item.chunk["text"] for item in self.chunks)


class ContextBuilder:
    def __init__(
        self,
        db=None,
        *,
        config: EffectiveConfig | None = None,
        token_counter: TokenCounter | None = None,
        metadata_store: ContextMetadataStore | None = None,
    ):
        self.db = db
        if config is not None:
            self.cfg = config
        elif db is not None:
            self.cfg = load_config(db_reader=make_db_reader(db))
        else:
            self.cfg = load_config()
        self.retrieval_cfg = self.cfg.group("retrieval")
        # TODO(FAZ-5): LLM tokenizer'i LiteLLM/model gateway ile değiştirilecek.
        self.token_counter = token_counter or BGEM3TokenCounter()
        self.metadata_store = metadata_store or DbContextMetadataStore(db)

    def build(self, retrieved: list[RetrievedChunk]) -> ContextBuildResult:
        with start_span("retrieval.build_context", input_chunk_count=len(retrieved)):
            unique = self._dedup(retrieved)
            metas = self.metadata_store.fetch([item["chunk_id"] for item in unique])
            selected = [
                _SelectedChunk(order=idx, chunk=chunk, meta=metas[chunk["chunk_id"]])
                for idx, chunk in enumerate(unique)
                if chunk["chunk_id"] in metas
            ]
            blocks = self._merge_adjacent(selected)
            kept, dropped_chunk_ids = self._apply_budget(blocks)
            result = self._render(kept, dropped_chunk_ids)
            set_span_attributes(
                output_block_count=len(result["blocks"]),
                output_citation_count=len(result["citations"]),
                dropped_chunk_count=len(dropped_chunk_ids),
                dropped_chunk_ids=",".join(str(cid) for cid in dropped_chunk_ids),
            )
            return result

    @staticmethod
    def _dedup(retrieved: list[RetrievedChunk]) -> list[RetrievedChunk]:
        best: dict[int, tuple[int, RetrievedChunk]] = {}
        for idx, chunk in enumerate(retrieved):
            chunk_id = int(chunk["chunk_id"])
            current = best.get(chunk_id)
            if current is None or float(chunk["score"]) > float(current[1]["score"]):
                best[chunk_id] = (idx, chunk)
        return [chunk for _idx, chunk in sorted(best.values(), key=lambda item: item[0])]

    @staticmethod
    def _merge_adjacent(chunks: list[_SelectedChunk]) -> list[_Block]:
        if not chunks:
            return []
        blocks: list[_Block] = [_Block([chunks[0]])]
        for item in chunks[1:]:
            prev = blocks[-1].chunks[-1]
            if item.file_id == prev.file_id and int(item.meta.chunk_index) == int(prev.meta.chunk_index) + 1:
                blocks[-1].chunks.append(item)
            else:
                blocks.append(_Block([item]))
        return blocks

    def _apply_budget(self, blocks: list[_Block]) -> tuple[list[_Block], list[int]]:
        budget = int(self.retrieval_cfg.context_token_budget)
        safety_margin = float(self.retrieval_cfg.context_token_safety_margin)
        kept = list(blocks)
        dropped: list[int] = []
        while kept and self._estimated_tokens(kept, safety_margin) > budget:
            victim = min(kept, key=lambda block: (block.score, -block.order))
            kept.remove(victim)
            dropped.extend(victim.chunk_ids)
        kept.sort(key=lambda block: block.order)
        return kept, dropped

    def _estimated_tokens(self, blocks: list[_Block], safety_margin: float) -> int:
        total = 0
        for idx, block in enumerate(blocks, start=1):
            total += self.token_counter.count(f"{self._label(idx, block)}\n{block.text()}")
        return int(total * safety_margin)

    def _render(self, blocks: list[_Block], dropped_chunk_ids: list[int]) -> ContextBuildResult:
        threshold = float(self.retrieval_cfg.context_low_quality_threshold)
        out_blocks: list[ContextBlock] = []
        out_citations: list[ContextCitation] = []
        for idx, block in enumerate(blocks, start=1):
            label = self._label(idx, block)
            low_quality = any(
                item.meta.quality_score is not None and float(item.meta.quality_score) < threshold
                for item in block.chunks
            )
            out_blocks.append(
                {
                    "n": idx,
                    "label": label,
                    "text": block.text(),
                    "chunk_ids": block.chunk_ids,
                    "token_count": block.token_count,
                    "low_quality": low_quality,
                }
            )
            for item in block.chunks:
                out_citations.append(
                    {
                        "n": idx,
                        "file_name": item.chunk["source"]["file_name"],
                        "page": item.meta.page_number,
                        "sheet": item.meta.sheet_name,
                        "section": item.meta.section_title,
                        "chunk_id": item.chunk["chunk_id"],
                    }
                )
        return {
            "blocks": out_blocks,
            "citations": out_citations,
            "dropped_chunk_ids": dropped_chunk_ids,
        }

    @staticmethod
    def _label(n: int, block: _Block) -> str:
        first = block.chunks[0]
        location = (
            f"sheet {first.meta.sheet_name}"
            if first.meta.sheet_name
            else (f"s.{first.meta.page_number}" if first.meta.page_number is not None else "konum?")
        )
        parts = [f"[{n}] {first.chunk['source']['file_name']}", location]
        if first.meta.section_title:
            parts.append(first.meta.section_title)
        return ", ".join(parts)

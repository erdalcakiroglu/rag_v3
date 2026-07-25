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

    def build(
        self, retrieved: list[RetrievedChunk], prior: ContextBuildResult | None = None
    ) -> ContextBuildResult:
        """İP-3.4 + kol-2(b): APPEND-ONLY bağlam (prefix/KV-cache disiplini).

        `prior` verilirse (bir önceki turun `context` sonucu), o turda gösterilmiş bloklar
        NUMARASIYLA BİREBİR yeniden yayılır — asla yeniden-numaralama/tahliye — ve yalnız YENİ
        chunk'lar daha yüksek numarayla SONA eklenir. Böylece mesaj-2'nin önceki bytes'ı turlar
        arası değişmez → prefix cache kırılmaz. FREEZE DEĞİL: ajan yeni chunk çekerse onlar da
        `[n]` ile citelanabilir. `prior=None` → ilk tur: eski stateless davranışla BİREBİR aynı
        (shown boş → numaralar 1'den; regresyon yok).

        Bütçe (append-only sözleşmesi): gösterilmiş bloklar ASLA düşmez (bütçeyi aşsalar bile);
        tahliye yalnız YENİ adaylardan, en düşük `(score, -order)` önce. Durum builder'da DEĞİL
        `context` state kanalında taşınır (builder singleton — self'te tutmak sorgular arası sızar).
        """
        with start_span("retrieval.build_context", input_chunk_count=len(retrieved)):
            shown_blocks, shown_citations, shown_ids, next_n = self._prior_ledger(prior)
            unique = self._dedup(retrieved)
            # Gösterilmiş chunk'lar zaten dondurulmuş bloklarla temsil ediliyor → yalnız YENİleri işle.
            new_unique = [item for item in unique if int(item["chunk_id"]) not in shown_ids]
            metas = self.metadata_store.fetch([item["chunk_id"] for item in new_unique])
            selected = [
                _SelectedChunk(order=idx, chunk=chunk, meta=metas[chunk["chunk_id"]])
                for idx, chunk in enumerate(new_unique)
                if chunk["chunk_id"] in metas
            ]
            new_blocks = self._merge_adjacent(selected)
            kept, dropped_chunk_ids = self._apply_budget(new_blocks, shown_blocks)
            new_rendered, new_citations = self._render(kept, start_n=next_n)
            result: ContextBuildResult = {
                "blocks": shown_blocks + new_rendered,
                "citations": shown_citations + new_citations,
                "dropped_chunk_ids": dropped_chunk_ids,
            }
            set_span_attributes(
                output_block_count=len(result["blocks"]),
                output_citation_count=len(result["citations"]),
                shown_block_count=len(shown_blocks),
                dropped_chunk_count=len(dropped_chunk_ids),
                dropped_chunk_ids=",".join(str(cid) for cid in dropped_chunk_ids),
            )
            return result

    @staticmethod
    def _prior_ledger(
        prior: ContextBuildResult | None,
    ) -> tuple[list[ContextBlock], list[ContextCitation], set[int], int]:
        """Önceki tur sonucundan append-only defterini çıkar.

        Döner: (gösterilmiş bloklar, gösterilmiş citation'lar — ikisi de BİREBİR yeniden yayılır),
        gösterilmiş chunk_id kümesi (yeniden işlenmez), bir sonraki BOŞ numara (yeni bloklar buradan).
        Bloklar sığ kopyalanır (çıktı listesi bağımsız olsun; içerik salt-okunur kullanılır)."""
        if not prior or not prior.get("blocks"):
            return [], [], set(), 1
        shown_blocks = [dict(block) for block in prior["blocks"]]
        shown_citations = [dict(cit) for cit in prior.get("citations", [])]
        shown_ids = {int(cid) for block in shown_blocks for cid in block["chunk_ids"]}
        next_n = max(int(block["n"]) for block in shown_blocks) + 1
        return shown_blocks, shown_citations, shown_ids, next_n

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

    def _apply_budget(
        self, blocks: list[_Block], shown_blocks: list[ContextBlock]
    ) -> tuple[list[_Block], list[int]]:
        """Bütçeyi YALNIZ yeni adaylara uygula; gösterilmiş bloklar ASLA düşmez (append-only).

        Gösterilmişlerin token maliyeti bütçeden düşülür (birlikte sığmalılar) ama tahliye
        edilmezler — bütçeyi aşsalar bile korunurlar. `shown_blocks` boşsa (ilk tur / prior=None)
        davranış eski `_apply_budget` ile BİREBİR aynıdır."""
        budget = int(self.retrieval_cfg.context_token_budget)
        safety_margin = float(self.retrieval_cfg.context_token_safety_margin)
        shown_tokens = sum(
            self.token_counter.count(f"{block['label']}\n{block['text']}") for block in shown_blocks
        )
        kept = list(blocks)
        dropped: list[int] = []
        while kept and self._estimated_tokens(kept, safety_margin, shown_tokens, len(shown_blocks)) > budget:
            victim = min(kept, key=lambda block: (block.score, -block.order))
            kept.remove(victim)
            dropped.extend(victim.chunk_ids)
        kept.sort(key=lambda block: block.order)
        return kept, dropped

    def _estimated_tokens(
        self, blocks: list[_Block], safety_margin: float, shown_tokens: int = 0, shown_count: int = 0
    ) -> int:
        total = shown_tokens
        for offset, block in enumerate(blocks):
            idx = shown_count + 1 + offset
            total += self.token_counter.count(f"{self._label(idx, block)}\n{block.text()}")
        return int(total * safety_margin)

    def _render(
        self, blocks: list[_Block], start_n: int = 1
    ) -> tuple[list[ContextBlock], list[ContextCitation]]:
        """Yeni blokları `start_n`'den başlayarak numaralayıp render eder (bloklar + citation'lar).

        Append-only: `start_n` gösterilmiş blokların bir sonrası → yeni bloklar hep daha yüksek
        numara alır, önceki numaralar korunur. `start_n=1` (ilk tur) eski davranışla BİREBİR aynı."""
        threshold = float(self.retrieval_cfg.context_low_quality_threshold)
        out_blocks: list[ContextBlock] = []
        out_citations: list[ContextCitation] = []
        for offset, block in enumerate(blocks):
            idx = start_n + offset
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
        return out_blocks, out_citations

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

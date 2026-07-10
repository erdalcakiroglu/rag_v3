"""Chunking Adaptörü (İP-5 orkestrasyonu) + İP-6 metadata doluluk.

- chunk_document ile chunk üretir (config: app_config.chunking).
- metrics_ingestion(step='chunk') yazar (token dağılımı alt skoru, Ek-A).
- truncated_ratio > eşik -> qc_findings('chunk_truncation_high') (config'ten).
- Chunk'ları döndürür; core_chunks yazımı İP-8 transaksiyonundadır.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ...config.loader import EffectiveConfig, load_config
from ...database.config_store import make_db_reader
from ...database.ingestion_repo import insert_metric, insert_qc_finding
from ...observability.logging import bind_context, clear_context, get_logger
from ...observability.tracing import set_span_attributes, start_span
from ..parsing.parsed_document import ParsedDocument
from .chunk import Chunk
from .chunker import chunk_document
from .quality import compute_chunk_metrics, metadata_fill_report
from .tokenizer import BGEM3TokenCounter, TokenCounter

CHUNK_STEP = "chunk"


@dataclass
class ChunkOutcome:
    file_id: int
    chunks: list[Chunk]
    metrics: dict
    findings: list[str] = field(default_factory=list)


class ChunkAdapter:
    def __init__(self, db=None, *, config: EffectiveConfig | None = None,
                 counter: TokenCounter | None = None, logger=None):
        self.db = db
        if config is not None:
            self.cfg = config
        elif db is not None:
            self.cfg = load_config(db_reader=make_db_reader(db))
        else:
            self.cfg = load_config()
        self.chunk_cfg = self.cfg.group("chunking")
        self.quality_chunk = self.cfg.group("quality").chunk
        self.counter = counter or BGEM3TokenCounter(self.cfg.group("embedding").model)
        self.log = logger or get_logger("ingestion.chunk")

    # Saf chunk üretimi (DB yok).
    def chunk_parsed(self, doc: ParsedDocument) -> list[Chunk]:
        c = self.chunk_cfg
        return chunk_document(
            doc, counter=self.counter, strategy=c.strategy,
            max_tokens=c.max_tokens, overlap_tokens=c.overlap_tokens,
            min_tokens=c.min_tokens,
            table_subchunk_max_tokens=getattr(c, "table_subchunk_max_tokens", c.max_tokens),
        )

    def chunk_file(self, file_id: int, doc: ParsedDocument) -> ChunkOutcome:
        bind_context(file_id=file_id)
        try:
            with start_span("ingest.chunk", file_id=file_id, component="chunk"):
                t0 = time.perf_counter()
                chunks = self.chunk_parsed(doc)
                duration_ms = int((time.perf_counter() - t0) * 1000)

                metrics = compute_chunk_metrics(
                    chunks, max_tokens=self.chunk_cfg.max_tokens,
                    min_tokens=self.chunk_cfg.min_tokens,
                )
                fill = metadata_fill_report(chunks)

                findings: list[str] = []
                if metrics["truncated_ratio"] > self.quality_chunk.soft_flag_truncated_ratio:
                    findings.append("chunk_truncation_high")

                set_span_attributes(
                    chunk_count=len(chunks),
                    chunk_truncated_ratio=metrics["truncated_ratio"],
                    chunk_findings=",".join(findings) if findings else "",
                )
                detail = {**metrics, "metadata_fill": fill, "findings": findings}
                with self.db.connection() as conn:
                    insert_metric(conn, file_id=file_id, step=CHUNK_STEP,
                                  duration_ms=duration_ms, ok=True, detail=detail)
                    for f in findings:
                        insert_qc_finding(conn, file_id=file_id, finding=f,
                                          detail=f"truncated_ratio={metrics['truncated_ratio']}")

                self.log.info("chunk_ok", chunks=len(chunks),
                              truncated_ratio=metrics["truncated_ratio"],
                              findings=findings)
                return ChunkOutcome(file_id, chunks, metrics, findings)
        finally:
            clear_context()

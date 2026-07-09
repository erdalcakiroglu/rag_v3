"""Retrieval service arayuzleri (İP-3.0) + vector search implementasyonu (İP-3.1)."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Protocol

from ..config.loader import EffectiveConfig, load_config
from ..config.settings import OllamaSettings, TeiSettings
from ..database.config_store import make_db_reader
from ..ingestion.embedding.embedder import Embedder, EmbeddingBackendError, OllamaEmbedder
from ..observability.logging import get_logger
from ..observability.tracing import set_span_attributes, start_span
from ..text import normalize_for_search
from . import repository as repo
from .types import RerankResult, RetrievedChunk, RetrievalFilters, UserContext


class RetrievalBackendError(RuntimeError):
    """Sorgu embedding/retrieval altyapi hatasi."""


class RetrievalStore(Protocol):
    def search_vector(
        self,
        *,
        query_vector: list[float],
        allowed_doc_scopes: list[str],
        top_k: int,
        ef_search: int,
        filters: RetrievalFilters | None = None,
    ) -> list[RetrievedChunk]: ...

    def search_hybrid(
        self,
        *,
        query: str,
        query_vector: list[float],
        normalized_query: str,
        allowed_doc_scopes: list[str],
        top_k: int,
        ef_search: int,
        filters: RetrievalFilters | None = None,
        fusion_strategy: str,
        rrf_k: int,
        dense_weight: float,
        sparse_weight: float,
        sparse_variant: str,
    ) -> list[RetrievedChunk]: ...

    def lookup_document(
        self,
        *,
        allowed_doc_scopes: list[str],
        chunk_id: int | None = None,
        window: int = 2,
        file_id: int | None = None,
        page: int | None = None,
    ) -> list[RetrievedChunk]: ...

    def rerank_texts(
        self,
        *,
        chunk_ids: list[int],
        allowed_doc_scopes: list[str],
    ) -> list[dict[str, str | int]]: ...


@dataclass
class PgRetrievalStore:
    db: any

    def search_vector(
        self,
        *,
        query_vector: list[float],
        allowed_doc_scopes: list[str],
        top_k: int,
        ef_search: int,
        filters: RetrievalFilters | None = None,
    ) -> list[RetrievedChunk]:
        with self.db.connection() as conn:
            return repo.search_vector(
                conn,
                query_vector=query_vector,
                allowed_doc_scopes=allowed_doc_scopes,
                top_k=top_k,
                ef_search=ef_search,
                filters=filters,
            )

    def search_hybrid(
        self,
        *,
        query: str,
        query_vector: list[float],
        normalized_query: str,
        allowed_doc_scopes: list[str],
        top_k: int,
        ef_search: int,
        filters: RetrievalFilters | None = None,
        fusion_strategy: str,
        rrf_k: int,
        dense_weight: float,
        sparse_weight: float,
        sparse_variant: str,
    ) -> list[RetrievedChunk]:
        with self.db.connection() as conn:
            return repo.search_hybrid(
                conn,
                query_vector=query_vector,
                normalized_query=normalized_query,
                allowed_doc_scopes=allowed_doc_scopes,
                top_k=top_k,
                ef_search=ef_search,
                filters=filters,
                fusion_strategy=fusion_strategy,
                rrf_k=rrf_k,
                dense_weight=dense_weight,
                sparse_weight=sparse_weight,
                sparse_variant=sparse_variant,
            )

    def lookup_document(
        self,
        *,
        allowed_doc_scopes: list[str],
        chunk_id: int | None = None,
        window: int = 2,
        file_id: int | None = None,
        page: int | None = None,
    ) -> list[RetrievedChunk]:
        with self.db.connection() as conn:
            return repo.lookup_document(
                conn,
                allowed_doc_scopes=allowed_doc_scopes,
                chunk_id=chunk_id,
                window=window,
                file_id=file_id,
                page=page,
            )

    def rerank_texts(
        self,
        *,
        chunk_ids: list[int],
        allowed_doc_scopes: list[str],
    ) -> list[dict[str, str | int]]:
        with self.db.connection() as conn:
            return repo.rerank_texts(
                conn,
                chunk_ids=chunk_ids,
                allowed_doc_scopes=allowed_doc_scopes,
            )


class RetrievalService:
    def __init__(
        self,
        db=None,
        *,
        config: EffectiveConfig | None = None,
        embedder: Embedder | None = None,
        store: RetrievalStore | None = None,
        rerank_client=None,
        logger=None,
    ):
        self.db = db
        if config is not None:
            self.cfg = config
        elif db is not None:
            self.cfg = load_config(db_reader=make_db_reader(db))
        else:
            self.cfg = load_config()
        s = OllamaSettings()
        self.embedder = embedder or OllamaEmbedder(s.base_url, model=s.model, timeout=s.timeout)
        self.store = store or PgRetrievalStore(db)
        self.retrieval_cfg = self.cfg.group("retrieval")
        self.tei_settings = TeiSettings()
        self._rerank_client = rerank_client
        self.log = logger or get_logger("retrieval.service")

    def _effective_top_k(self, top_k: int | None) -> int:
        raw = int(self.retrieval_cfg.default_top_k if top_k is None else top_k)
        return max(1, min(raw, int(self.retrieval_cfg.max_top_k)))

    @staticmethod
    def _allowed_scopes(user_ctx: UserContext) -> list[str]:
        scopes = user_ctx.get("allowed_doc_scopes") or []
        return [s for s in scopes if s]

    def _embed_query(self, query: str) -> tuple[list[float], int]:
        t0 = time.perf_counter()
        try:
            vector = self.embedder.embed_batch([query])[0]
        except EmbeddingBackendError as exc:
            raise RetrievalBackendError(f"Sorgu embedding alınamadı: {exc}") from exc
        return vector, int((time.perf_counter() - t0) * 1000)

    @staticmethod
    def _hybrid_rank_detail(chunks: list[RetrievedChunk]) -> str:
        parts = []
        for chunk in chunks[:10]:
            detail = chunk.get("detail", {})
            parts.append(
                f"{chunk['chunk_id']}:d{detail.get('dense_rank', '-')}/s{detail.get('sparse_rank', '-')}"
            )
        return "|".join(parts)

    @property
    def rerank_client(self):
        if self._rerank_client is None:
            import httpx

            self._rerank_client = httpx.Client(timeout=float(self.retrieval_cfg.rerank_timeout_sec))
        return self._rerank_client

    @staticmethod
    def _passthrough_rerank(chunk_ids: list[int]) -> list[RerankResult]:
        return [{"chunk_id": cid, "rerank_score": 0.0} for cid in chunk_ids]

    def _tei_rerank(self, query: str, chunk_ids: list[int], allowed: list[str]) -> list[RerankResult]:
        rows = self.store.rerank_texts(chunk_ids=chunk_ids, allowed_doc_scopes=allowed)
        if len(rows) != len(chunk_ids):
            raise ValueError("Rerank için gerekli chunk metinleri eksik")

        texts = [str(row["text"]) for row in rows]
        for attempt in range(int(self.retrieval_cfg.rerank_retries) + 1):
            try:
                resp = self.rerank_client.post(
                    f"{self.tei_settings.rerank_url.rstrip('/')}/rerank",
                    json={"query": query, "texts": texts},
                )
                resp.raise_for_status()
                payload = resp.json()
                results = payload if isinstance(payload, list) else payload.get("results")
                if not isinstance(results, list):
                    raise ValueError("TEI rerank yanıtı geçersiz")
                scored = []
                for item in results:
                    idx = int(item["index"])
                    score = float(item["score"])
                    if idx < 0 or idx >= len(chunk_ids):
                        raise ValueError("TEI rerank index değeri geçersiz")
                    scored.append({"chunk_id": chunk_ids[idx], "rerank_score": score})
                if len(scored) != len(chunk_ids):
                    raise ValueError("TEI rerank sonuç sayısı eksik")
                scored.sort(key=lambda item: item["rerank_score"], reverse=True)
                return scored
            except Exception as exc:
                is_last = attempt >= int(self.retrieval_cfg.rerank_retries)
                if self._is_fail_open_rerank_error(exc) and is_last:
                    self.log.warning("rerank_fallback", backend="tei", error=str(exc))
                    set_span_attributes(rerank_fallback=True, rerank_fallback_reason=str(exc))
                    return self._passthrough_rerank(chunk_ids)
                if self._is_fail_open_rerank_error(exc):
                    continue
                raise
        return self._passthrough_rerank(chunk_ids)

    @staticmethod
    def _is_fail_open_rerank_error(exc: Exception) -> bool:
        import httpx

        return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))

    def search_hybrid(
        self,
        query: str,
        top_k: int = 10,
        filters: RetrievalFilters | None = None,
        *,
        user_ctx: UserContext,
    ) -> list[RetrievedChunk]:
        top_k = self._effective_top_k(top_k)
        allowed = self._allowed_scopes(user_ctx)
        query_norm = normalize_for_search(query)
        fusion_strategy = str(self.retrieval_cfg.hybrid_fusion)
        sparse_variant = str(self.retrieval_cfg.hybrid_sparse_variant)
        dense_weight = float(self.retrieval_cfg.hybrid_dense_weight)
        sparse_weight = float(self.retrieval_cfg.hybrid_sparse_weight)
        rrf_k = int(self.retrieval_cfg.hybrid_rrf_k)
        with start_span(
            "retrieval.search_hybrid",
            query=query,
            query_norm=query_norm,
            top_k=top_k,
            filters=str(filters or {}),
            allowed_doc_scopes=",".join(allowed),
            fusion_strategy=fusion_strategy,
            sparse_variant=sparse_variant,
            hybrid_rrf_k=rrf_k,
        ):
            if not allowed:
                set_span_attributes(result_count=0, fail_closed=True)
                return []
            qv, embed_ms = self._embed_query(query)
            t0 = time.perf_counter()
            chunks = self.store.search_hybrid(
                query=query,
                query_vector=qv,
                normalized_query=query_norm,
                allowed_doc_scopes=allowed,
                top_k=top_k,
                ef_search=int(self.retrieval_cfg.vector_ef_search),
                filters=filters,
                fusion_strategy=fusion_strategy,
                rrf_k=rrf_k,
                dense_weight=dense_weight,
                sparse_weight=sparse_weight,
                sparse_variant=sparse_variant,
            )
            db_ms = int((time.perf_counter() - t0) * 1000)
            set_span_attributes(
                retrieval_method="hybrid",
                query_embed_ms=embed_ms,
                db_ms=db_ms,
                result_count=len(chunks),
                fail_closed=False,
                rank_detail=self._hybrid_rank_detail(chunks),
            )
            self.log.info(
                "retrieval_timing", method="hybrid", query_embed_ms=embed_ms,
                db_ms=db_ms, result_count=len(chunks),
            )
            return chunks

    def search_vector(
        self,
        query: str,
        top_k: int = 10,
        filters: RetrievalFilters | None = None,
        *,
        user_ctx: UserContext,
    ) -> list[RetrievedChunk]:
        top_k = self._effective_top_k(top_k)
        allowed = self._allowed_scopes(user_ctx)
        with start_span(
            "retrieval.search_vector",
            query=query,
            top_k=top_k,
            filters=str(filters or {}),
            allowed_doc_scopes=",".join(allowed),
        ):
            if not allowed:
                set_span_attributes(result_count=0, fail_closed=True)
                return []
            qv, embed_ms = self._embed_query(query)
            t0 = time.perf_counter()
            chunks = self.store.search_vector(
                query_vector=qv,
                allowed_doc_scopes=allowed,
                top_k=top_k,
                ef_search=int(self.retrieval_cfg.vector_ef_search),
                filters=filters,
            )
            db_ms = int((time.perf_counter() - t0) * 1000)
            set_span_attributes(
                retrieval_method="vector",
                query_embed_ms=embed_ms,
                db_ms=db_ms,
                result_count=len(chunks),
                fail_closed=False,
            )
            self.log.info(
                "retrieval_timing", method="vector", query_embed_ms=embed_ms,
                db_ms=db_ms, result_count=len(chunks),
            )
            return chunks

    def lookup_document(
        self,
        chunk_id: int | None = None,
        window: int = 2,
        *,
        file_id: int | None = None,
        page: int | None = None,
        user_ctx: UserContext,
    ) -> list[RetrievedChunk]:
        allowed = self._allowed_scopes(user_ctx)
        with start_span(
            "retrieval.lookup_document",
            chunk_id=chunk_id,
            file_id=file_id,
            page=page,
            window=window,
            allowed_doc_scopes=",".join(allowed),
        ):
            if not allowed:
                set_span_attributes(result_count=0, fail_closed=True)
                return []
            t0 = time.perf_counter()
            chunks = self.store.lookup_document(
                allowed_doc_scopes=allowed,
                chunk_id=chunk_id,
                window=window,
                file_id=file_id,
                page=page,
            )
            db_ms = int((time.perf_counter() - t0) * 1000)
            set_span_attributes(
                retrieval_method="lookup",
                db_ms=db_ms,
                result_count=len(chunks),
                fail_closed=False,
            )
            return chunks

    def rerank(
        self,
        query: str,
        chunk_ids: list[int],
        *,
        user_ctx: UserContext,
    ) -> list[RerankResult]:
        allowed = self._allowed_scopes(user_ctx)
        backend = str(self.retrieval_cfg.rerank_backend)
        with start_span(
            "retrieval.rerank",
            query=query,
            chunk_count=len(chunk_ids),
            allowed_doc_scopes=",".join(allowed),
            backend=backend,
        ):
            if not allowed:
                set_span_attributes(result_count=0, fail_closed=True)
                return []
            t0 = time.perf_counter()
            if backend == "tei":
                ordered = self._tei_rerank(query, chunk_ids, allowed)
            elif backend == "passthrough":
                ordered = self._passthrough_rerank(chunk_ids)
            else:
                raise ValueError(f"Desteklenmeyen rerank backend: {backend}")
            rerank_ms = int((time.perf_counter() - t0) * 1000)
            set_span_attributes(result_count=len(ordered), fail_closed=False, rerank_ms=rerank_ms)
            self.log.info(
                "rerank_timing", backend=backend, rerank_ms=rerank_ms,
                chunk_count=len(chunk_ids),
            )
            return ordered

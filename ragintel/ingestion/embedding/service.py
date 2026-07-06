"""Embedding Servisi (İP-7 / ADR-012) — Ollama backend orkestrasyonu.

Sorumluluklar:
  - Chunk metinlerini batch'ler halinde embed_batch ile embed eder.
  - Ağ dayanıklılığı: timeout/5xx -> retry (config, exponential backoff);
    kalıcı hatada batch yarıla ve devam et (OOM kuralının ağ karşılığı).
  - Ollama erişilemez -> EmbeddingBackendError (pipeline durur, dosya FAILED
    OLMAZ — altyapı hatası dosya hatası değildir).
  - QC: NaN/sıfır-norm -> qc_findings('embed_failed'), chunk atlanır, devam.
  - Doc-içi benzerlik anomalisi -> qc_findings('embed_anomaly') (soft).
  - metrics_ingestion(step='embed').detail: norm dağılımı, istek/batch sayıları.

Üretilen vektörler İP-8'de core_vectors'a yazılır (model_name='bge-m3@ollama').
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from ...config.loader import EffectiveConfig, load_config
from ...database.config_store import make_db_reader
from ...database.ingestion_repo import insert_metric, insert_qc_finding
from ...observability.logging import bind_context, clear_context, get_logger
from ...observability.tracing import add_event, set_span_attributes, start_span
from ..chunking.chunk import Chunk
from .embedder import EmbeddingBackendError, OllamaEmbedder
from .quality import compute_embed_metrics, is_bad_vector, mean_pairwise_cosine

EMBED_STEP = "embed"


@dataclass
class EmbeddedChunk:
    chunk: Chunk
    vector: list[float] | None       # None -> embed_failed (atlandı)


@dataclass
class EmbedResult:
    file_id: int | None
    items: list[EmbeddedChunk]
    metrics: dict
    findings: list[str] = field(default_factory=list)
    model_name: str = ""

    @property
    def embedded(self) -> list[tuple[Chunk, list[float]]]:
        return [(i.chunk, i.vector) for i in self.items if i.vector is not None]


class EmbeddingService:
    def __init__(self, db=None, *, config: EffectiveConfig | None = None,
                 embedder=None, retries: int | None = None,
                 backoff_base: float | None = None, sleep=time.sleep, logger=None):
        self.db = db
        if config is not None:
            self.cfg = config
        elif db is not None:
            self.cfg = load_config(db_reader=make_db_reader(db))
        else:
            self.cfg = load_config()
        emb = self.cfg.group("embedding")
        self.dim = emb.dim
        self.batch_size = emb.batch_size
        self.embed_cfg = self.cfg.group("quality").embed

        from ...config.settings import OllamaSettings
        s = OllamaSettings()
        self.embedder = embedder or OllamaEmbedder(
            s.base_url, model=s.model, timeout=s.timeout)
        self.retries = s.retries if retries is None else retries
        self.backoff_base = s.backoff_base if backoff_base is None else backoff_base
        self.sleep = sleep
        self.model_name = getattr(self.embedder, "model_name", "bge-m3@ollama")
        self.log = logger or get_logger("ingestion.embed")

    # -- public ---------------------------------------------------------------
    def embed_chunks(self, chunks: list[Chunk]) -> EmbedResult:
        """DB'siz: chunk'ları embed eder, QC uygular. EmbeddingBackendError
        fırlatabilir (erişilemezlik/kalıcı hata)."""
        texts = [c.chunk_text for c in chunks]
        stats = {"requests": 0, "retries": 0, "halvings": 0}
        vectors = self._embed_all(texts, stats)   # aligned; hata -> raise

        items: list[EmbeddedChunk] = []
        good: list[list[float]] = []
        failed_indexes: list[int] = []
        for idx, (chunk, vec) in enumerate(zip(chunks, vectors)):
            if is_bad_vector(vec, self.dim):
                items.append(EmbeddedChunk(chunk, None))
                failed_indexes.append(chunk.chunk_index)
            else:
                items.append(EmbeddedChunk(chunk, vec))
                good.append(vec)

        metrics = compute_embed_metrics(
            good, expected=len(chunks), failed=len(failed_indexes), dim=self.dim)
        anomaly = mean_pairwise_cosine(good)
        metrics.update({
            "mean_pairwise_cosine": round(anomaly, 4),
            "request_count": stats["requests"],
            "retry_count": stats["retries"],
            "batch_halvings": stats["halvings"],
            "batch_size": self.batch_size,
            "model_name": self.model_name,
            "failed_chunk_indexes": failed_indexes[:50],
        })

        findings: list[str] = []
        if failed_indexes:
            findings.append("embed_failed")
        if len(good) >= 2 and anomaly > self.embed_cfg.anomaly_cosine_high:
            findings.append("embed_anomaly")

        return EmbedResult(None, items, metrics, findings, self.model_name)

    def embed_file(self, file_id: int, chunks: list[Chunk]) -> EmbedResult:
        """DB'li: embed + metrics(step='embed') + qc_findings. Altyapı hatasında
        EmbeddingBackendError YUKARI atılır (dosya FAILED işaretlenmez)."""
        bind_context(file_id=file_id)
        try:
            with start_span("ingest.embed", file_id=file_id, component="embed"):
                t0 = time.perf_counter()
                result = self.embed_chunks(chunks)
                result.file_id = file_id
                duration_ms = int((time.perf_counter() - t0) * 1000)

                set_span_attributes(
                    embed_embedded=result.metrics["embedded"],
                    embed_failed=result.metrics["failed"],
                    embed_request_count=result.metrics["request_count"],
                    embed_retry_count=result.metrics["retry_count"],
                    embed_batch_halvings=result.metrics["batch_halvings"],
                    embed_model_name=result.metrics["model_name"],
                )
                if result.findings:
                    add_event("embed_findings", findings=",".join(result.findings))

                with self.db.connection() as conn:
                    insert_metric(conn, file_id=file_id, step=EMBED_STEP,
                                  duration_ms=duration_ms, ok=(result.metrics["failed"] == 0),
                                  detail=result.metrics)
                    for idx in result.metrics["failed_chunk_indexes"]:
                        insert_qc_finding(conn, file_id=file_id, finding="embed_failed",
                                          detail=f"chunk_index={idx}")
                    if "embed_anomaly" in result.findings:
                        insert_qc_finding(
                            conn, file_id=file_id, finding="embed_anomaly",
                            detail=f"mean_pairwise_cosine={result.metrics['mean_pairwise_cosine']}")

                self.log.info("embed_ok", embedded=result.metrics["embedded"],
                              failed=result.metrics["failed"],
                              requests=result.metrics["request_count"],
                              halvings=result.metrics["batch_halvings"])
                return result
        except EmbeddingBackendError:
            self.log.error("embed_backend_unavailable", file_id=file_id)
            raise      # pipeline durur; İP-10 RETRY
        finally:
            clear_context()

    # -- internals ------------------------------------------------------------
    def _embed_all(self, texts: list[str], stats: dict) -> list[list[float] | None]:
        out: list[list[float] | None] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            out.extend(self._process_batch(batch, stats))
        return out

    def _process_batch(self, texts: list[str], stats: dict) -> list[list[float]]:
        if not texts:
            return []
        delay = self.backoff_base
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            stats["requests"] += 1
            try:
                with start_span(
                    "embed.http",
                    component="embed_http",
                    request_batch_size=len(texts),
                    retry_attempt=attempt,
                    model_name=self.model_name,
                ):
                    vectors = self.embedder.embed_batch(texts)
                    set_span_attributes(http_status_code=200, response_vectors=len(vectors))
                    return vectors
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # Erişilemez: halving fayda etmez, hızlı ve anlaşılır başarısız.
                raise EmbeddingBackendError(
                    f"Ollama erişilemez ({self.embedder.__class__.__name__}): {exc}"
                ) from exc
            except httpx.HTTPStatusError as exc:
                set_span_attributes(http_status_code=exc.response.status_code)
                if not (500 <= exc.response.status_code < 600):
                    raise EmbeddingBackendError(
                        f"Ollama HTTP {exc.response.status_code} (retry edilmez)"
                    ) from exc
                last_exc = exc
            except httpx.TimeoutException as exc:
                add_event("embed_http_timeout", batch_size=len(texts), attempt=attempt)
                last_exc = exc
            if attempt < self.retries:
                stats["retries"] += 1
                self.sleep(delay)
                delay *= 2

        # Retry'lar tükendi (5xx/timeout) -> batch yarıla ve devam et.
        if len(texts) > 1:
            stats["halvings"] += 1
            mid = len(texts) // 2
            self.log.warning("embed_batch_halving", size=len(texts))
            return (self._process_batch(texts[:mid], stats)
                    + self._process_batch(texts[mid:], stats))

        raise EmbeddingBackendError(
            f"Ollama kalıcı hata (tek istek {self.retries} denemede başarısız): {last_exc}"
        )

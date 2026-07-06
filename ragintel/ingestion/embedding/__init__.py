"""İP-7 embedding (ADR-012): remote Ollama backend + dayanıklı servis."""

from .embedder import (
    MODEL_STAMP,
    Embedder,
    EmbeddingBackendError,
    OllamaEmbedder,
    l2_normalize,
)
from .quality import compute_embed_metrics, is_bad_vector, mean_pairwise_cosine
from .service import EmbeddedChunk, EmbeddingService, EmbedResult

__all__ = [
    "OllamaEmbedder",
    "Embedder",
    "EmbeddingBackendError",
    "MODEL_STAMP",
    "l2_normalize",
    "EmbeddingService",
    "EmbedResult",
    "EmbeddedChunk",
    "is_bad_vector",
    "compute_embed_metrics",
    "mean_pairwise_cosine",
]

"""İP-7 embedding (ADR-012): remote Ollama backend + dayanıklı servis."""

from .embedder import (
    Embedder,
    EmbeddingBackendError,
    OllamaEmbedder,
    l2_normalize,
    model_stamp,
    ollama_tag,
)
from .quality import compute_embed_metrics, is_bad_vector, mean_pairwise_cosine
from .service import EmbeddedChunk, EmbeddingService, EmbedResult

__all__ = [
    "OllamaEmbedder",
    "Embedder",
    "EmbeddingBackendError",
    "model_stamp",
    "ollama_tag",
    "l2_normalize",
    "EmbeddingService",
    "EmbedResult",
    "EmbeddedChunk",
    "is_bad_vector",
    "compute_embed_metrics",
    "mean_pairwise_cosine",
]

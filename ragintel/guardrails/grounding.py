"""FAZ 4 grounding/citation doğrulaması (deterministik).

Citation evreni (tasarım §4, netleştirilmiş): validate node'un denetlediği chunk
kümesi, context builder'ın PROMPT'A KOYDUĞU (sakladığı) chunk'lardır — yani
LLM'in gerçekten gördüğü bağlam. Bütçeyle elenen chunk'lar bu evrende YOKTUR;
onlara verilen citation `citation_not_in_context` sayılır. Elenen chunk'lar dahil
tüm retrieved havuzu yalnızca teşhis amaçlı span'de kalır.

Coverage = geçerli citation'a bağlanan BENZERSİZ cümle / toplam cümle. Geçersiz
(fabricated/not-in-context) citation'lar paya girmez; tek cümleye verilen çoklu
citation o cümleyi bir kez sayar (şişme yok).
"""

from __future__ import annotations

import re

from ..agents.state import Citation, ValidationResult
from ..retrieval.types import RetrievedChunk
from ..text import normalize_for_quote

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _sentences(text: str | None) -> list[str]:
    if not text or not text.strip():
        return []
    return [part.strip() for part in _SENTENCE_SPLIT.split(text.strip()) if part.strip()]


def _claim_covers_sentence(claim_norm: str, sentence_norm: str) -> bool:
    if not claim_norm or not sentence_norm:
        return False
    return claim_norm == sentence_norm or claim_norm in sentence_norm or sentence_norm in claim_norm


def validate_grounding(
    *,
    draft_answer: str | None,
    citations: list[Citation],
    context_chunks: list[RetrievedChunk],
    coverage_threshold: float,
) -> ValidationResult:
    """`context_chunks`: LLM'in gördüğü bağlam (context builder'ın sakladıkları)."""
    issues: list[str] = []
    context_map = {int(chunk["chunk_id"]): chunk for chunk in context_chunks}
    valid_claims: list[str] = []

    for citation in citations:
        # Gerçek modeller bozuk citation üretebilir (str, eksik alan) — crash yerine ele.
        if not isinstance(citation, dict) or "chunk_id" not in citation:
            issues.append("malformed_citation")
            continue
        try:
            chunk_id = int(citation["chunk_id"])
        except (TypeError, ValueError):
            issues.append("malformed_citation")
            continue
        chunk = context_map.get(chunk_id)
        if chunk is None:
            issues.append(f"citation_not_in_context:{chunk_id}")
            continue
        quote_norm = normalize_for_quote(str(citation.get("quote", "")))
        chunk_norm = normalize_for_quote(chunk["text"])
        if quote_norm and quote_norm not in chunk_norm:
            issues.append(f"fabricated_quote:{chunk_id}")
            continue
        # Yalnızca geçerli citation'ın bağlandığı cümle (claim) coverage'a girer.
        claim_norm = normalize_for_quote(str(citation.get("claim", "")))
        if claim_norm:
            valid_claims.append(claim_norm)

    sentences = _sentences(draft_answer)
    if not sentences:
        coverage = 0.0
    else:
        covered = sum(
            1
            for sentence in sentences
            if any(_claim_covers_sentence(claim, normalize_for_quote(sentence)) for claim in valid_claims)
        )
        coverage = covered / len(sentences)
    if coverage < float(coverage_threshold):
        issues.append(f"low_coverage:{coverage:.3f}")
    if context_chunks and draft_answer and "bulunamad" in draft_answer.lower():
        issues.append("answer_claims_no_info_despite_context")

    return {
        "passed": not issues,
        "coverage": coverage,
        "issues": issues,
    }

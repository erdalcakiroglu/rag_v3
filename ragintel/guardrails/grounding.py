"""FAZ 4 grounding/citation doğrulaması (deterministik).

Citation evreni (tasarım §4, netleştirilmiş): validate node'un denetlediği chunk
kümesi, context builder'ın PROMPT'A KOYDUĞU (sakladığı) chunk'lardır — yani
LLM'in gerçekten gördüğü bağlam. Bütçeyle elenen chunk'lar bu evrende YOKTUR;
onlara verilen citation `citation_not_in_context` sayılır. Elenen chunk'lar dahil
tüm retrieved havuzu yalnızca teşhis amaçlı span'de kalır.

Quote geçerliliği (§4, faithful-paraphrase'e gevşetildi): quote ya cited chunk'ta
BİREBİR geçer, ya da içerik-token'larının yeterli oranı TÜM BAĞLAMDA bulunur.
Böylece model doğru bilgiyi kendi cümlesiyle ifade ettiğinde (parafraz/sentez)
citation reddedilmez; ama bağlamda OLMAYAN bilgi (uydurma yıl/ülle vb.) düşük
örtüşmeyle `unsupported_quote` olarak elenir → anti-halüsinasyon korunur.

Coverage = geçerli citation'a bağlanan BENZERSİZ cümle / toplam cümle. Geçersiz
citation'lar paya girmez; tek cümleye verilen çoklu citation o cümleyi bir kez
sayar (şişme yok).
"""

from __future__ import annotations

import re

from ..agents.state import Citation, ValidationResult
from ..retrieval.types import RetrievedChunk
from ..text import normalize_for_quote

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)  # kelime VEYA sayı (noktalama hariç, Türkçe dahil)

# M-16 FIX-1: coverage paydasına GİRMEYEN "iddia taşımayan" cümle işaretçileri.
# YOKLUK/RED fragmanı stem'leri — YALNIZ negatif biçimler (pozitif "bulunmaktadır"=VAR yakalanMAZ:
# "bulunmamak" ⊄ "bulunmaktadır"). Yokluğu alıntılayacak chunk olmadığından bu cümleler iddia sayılmaz.
_DECLINE_FRAGMENTS = (
    "bulunmamak", "bulunmuyor", "bulunamad", "mevcut değil",
    "yer almamak", "yer almıyor", "üretilemedi", "belirtilmemiş", "belirsiz",
)


def _sentences(text: str | None) -> list[str]:
    if not text or not text.strip():
        return []
    return [part.strip() for part in _SENTENCE_SPLIT.split(text.strip()) if part.strip()]


def _is_claim_sentence(sentence: str, sentence_norm: str) -> bool:
    """Cümle coverage PAYDASINA girer mi? İddia taşımıyorsa HAYIR — haksız yere coverage'ı
    düşürmesin (M-16 FIX-1). İddia DEĞİL sayılan iki tip: (a) yokluk/red fragmanı (decline
    stem), (b) askıda '[1]' gibi gerçek kelime içermeyen (yalnız sayı/işaret) cümle. Uydurma
    POZİTİF iddia bundan etkilenmez: kelimelidir ve decline-fragmansızdır → paydada kalır,
    kapsanmazsa coverage'ı düşürür (anti-halüsinasyon korunur)."""
    low = sentence.lower()
    if any(frag in low for frag in _DECLINE_FRAGMENTS):
        return False
    # Gerçek kelime (≥2 harf, sayı değil) yoksa iddia değil (askıda "[1]" / noktalama).
    return any((not t.isdigit()) and len(t) >= 2 for t in _tokens(sentence_norm))


def _tokens(norm: str) -> list[str]:
    """Noktalamadan arındırılmış tüm token'lar (küme/örtüşme kıyasları için)."""
    return _WORD.findall(norm)


def _content_tokens(norm: str) -> list[str]:
    """İçerik-taşıyan token'lar: uzun kelimeler + sayılar (kısa bağlaç/edatları ele).
    Türkçe stopword'lerin çoğu ≤3 harf; ayırt edici sinyal sayılar ve özel adlardır."""
    return [w for w in _tokens(norm) if len(w) >= 4 or w.isdigit()]


def _claim_covers_sentence(claim_norm: str, sentence_norm: str, overlap_threshold: float = 0.6) -> bool:
    """Cümle bu claim tarafından kapsanıyor mu? Birebir/substring VEYA faithful-
    paraphrase: claim'in İÇERİK-token'larının ≥ eşik oranı cümlede geçiyorsa
    (claim'in öne sürdüğü olgular cümlede varsa) kapsanmış sayılır."""
    if not claim_norm or not sentence_norm:
        return False
    if claim_norm == sentence_norm or claim_norm in sentence_norm or sentence_norm in claim_norm:
        return True
    ctok = _content_tokens(claim_norm)
    if len(ctok) < 2:  # çok kısa claim → yalnızca substring (yukarıda düştü); şişme önlenir
        return False
    sset = set(_tokens(sentence_norm))
    hit = sum(1 for w in ctok if w in sset)
    return hit / len(ctok) >= overlap_threshold


def _quote_supported(quote_norm: str, chunk_norm: str, context_tokens: set[str], overlap_threshold: float) -> bool:
    """Quote geçerli mi? (1) cited chunk'ta birebir → evet; (2) içerik-token'ların
    ≥ eşik oranı tüm bağlamda geçiyorsa (faithful paraphrase) → evet."""
    if not quote_norm:
        return True  # quote yok → claim-only citation; quote kısıtı uygulanmaz
    if quote_norm in chunk_norm:
        return True
    qtok = _content_tokens(quote_norm)
    if not qtok:  # içerik token'ı yok (çok kısa) → yalnızca birebir kabul (yukarıda düştü)
        return False
    hit = sum(1 for w in qtok if w in context_tokens)
    return hit / len(qtok) >= overlap_threshold


def validate_grounding(
    *,
    draft_answer: str | None,
    citations: list[Citation],
    context_chunks: list[RetrievedChunk],
    coverage_threshold: float,
    quote_overlap_threshold: float = 0.7,
) -> ValidationResult:
    """`context_chunks`: LLM'in gördüğü bağlam (context builder'ın sakladıkları).
    `quote_overlap_threshold`: faithful-paraphrase için içerik-token örtüşme eşiği."""
    issues: list[str] = []
    context_map = {int(chunk["chunk_id"]): chunk for chunk in context_chunks}
    # Faithful-paraphrase kontrolü: quote'un içeriği TÜM bağlamda destekleniyor mu.
    context_tokens = {tok for chunk in context_chunks for tok in _tokens(normalize_for_quote(chunk["text"]))}
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
        if not _quote_supported(quote_norm, chunk_norm, context_tokens, quote_overlap_threshold):
            issues.append(f"unsupported_quote:{chunk_id}")
            continue
        # Yalnızca geçerli citation'ın bağlandığı cümle (claim) coverage'a girer.
        claim_norm = normalize_for_quote(str(citation.get("claim", "")))
        if claim_norm:
            valid_claims.append(claim_norm)

    sentences = _sentences(draft_answer)
    # M-16 FIX-1: payda = yalnız İDDİA taşıyan cümleler (askıda işaret/dolgu ve yokluk/red
    # fragmanları hariç — ön-veri: gs-002/012/022/023'te bunlar coverage'ı haksız düşürüyordu).
    # İddia cümlesi HİÇ yoksa (pür red/dolgu) coverage=0 → validate FAIL → fallback (temiz red).
    claim_sentences = [s for s in sentences if _is_claim_sentence(s, normalize_for_quote(s))]
    if not claim_sentences:
        coverage = 0.0
    else:
        covered = sum(
            1
            for sentence in claim_sentences
            if any(_claim_covers_sentence(claim, normalize_for_quote(sentence)) for claim in valid_claims)
        )
        coverage = covered / len(claim_sentences)
    if coverage < float(coverage_threshold):
        issues.append(f"low_coverage:{coverage:.3f}")
    if context_chunks and draft_answer and "bulunamad" in draft_answer.lower():
        issues.append("answer_claims_no_info_despite_context")

    return {
        "passed": not issues,
        "coverage": coverage,
        "issues": issues,
    }

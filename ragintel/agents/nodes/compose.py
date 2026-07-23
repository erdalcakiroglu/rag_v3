"""FAZ 4 compose node."""

from __future__ import annotations

import re
import time

from ...agents.state import Citation
from ...config.loader import EffectiveConfig, load_config
from ...guardrails.pii import mask_pii, policy_from_config


def _confidence(state: dict, *, high_threshold: float) -> str:
    validation = state.get("validation") or {"coverage": 0.0}
    coverage = float(validation.get("coverage", 0.0))
    retry_count = int(state.get("retry_count", 0))
    if coverage >= high_threshold and retry_count == 0:
        return "high"
    if coverage > 0.0:
        return "medium"
    return "low"


# M-16 FIX-2: "bulunamadı" (reddetme) metinsel imzaları. harness._NOTFOUND_MARKERS ile SENKRON
# tutulmalı — aynı tanım → uygulamanın reddi ⇔ eval'in "declined" saydığı durum. Reddeden bir
# cevaba KAYNAK İLİŞTİRİLMEZ (sources=[]): "bulunamadı DEDİ ama cite etti" (border_declined_cited)
# honesty ihlali kökten biter ve coverage eşiğinden BAĞIMSIZ olur (fallback yolu zaten sources=[]).
DECLINE_MARKERS = (
    "bulunmamaktadır", "bulunamadı", "bulunmuyor", "bulunmamakta", "mevcut değil",
    "yer almamaktadır", "güvenilir yanıt üretilemedi", "dokümanlarda bulunm",
    "belgelerde bulunm", "bilgi bulunm", "yanıt üretilemedi",
)


def _text_declines(answer: str | None) -> bool:
    a = (answer or "").lower()
    return any(m in a for m in DECLINE_MARKERS)


# M-3(b): LLM'in yanıt metnine serpiştirdiği `[k]` işaretleri hiçbir sözleşmeye bağlı
# DEĞİL (prompt `[n]` biçimini hiç tanımlamıyor) — model 1 citation verip metinde "[2]"
# yazabiliyor. Bu yüzden model işaretlerine GÜVENMİYORUZ: hepsini söküp, kaynak listesinden
# deterministik olarak yeniden üretiyoruz. Sonuç değişmezi: metindeki her [n], sources
# listesindeki n'inci girdiye denk gelir.
_MARKER_RE = re.compile(r"[ \t]*\[\s*\d+(?:\s*[,;]\s*\d+)*\s*\]")


def _sources(citations: list[Citation], retrieved: list[dict]) -> tuple[list[dict], dict[int, int]]:
    """Citation'ları kaynak listesine çevirir. Aynı chunk birden çok kez alıntılanmışsa
    TEK kaynak olur (ilk görülme sırası n'i belirler) — eskiden aynı chunk için mükerrer
    [1]/[3] girdileri üretiliyordu. Dönüş: (sources, chunk_id → n)."""
    chunk_map = {int(chunk["chunk_id"]): chunk for chunk in retrieved}
    sources: list[dict] = []
    numbering: dict[int, int] = {}
    for citation in citations:
        cid = int(citation["chunk_id"])
        chunk = chunk_map.get(cid)
        if chunk is None or cid in numbering:
            continue
        source = chunk["source"]
        numbering[cid] = len(sources) + 1
        sources.append(
            {
                "n": numbering[cid],
                "file_name": source["file_name"],
                "page": source.get("page"),
                "section": source.get("section"),
                "chunk_id": cid,
                "quote": citation["quote"],
            }
        )
    return sources, numbering


def _renumber_answer(answer: str, citations: list[Citation], numbering: dict[int, int]) -> str:
    """Modelin `[k]`'lerini söker; her citation'ın `claim`'ini metinde bulup arkasına doğru
    `[n]`'i yerleştirir. Claim metinde bulunamazsa (parafraz) işaret cümle içine
    zorlanmaz — yanıtın sonuna eklenir. Kaynak yoksa metin işaretsiz kalır.

    Model HİÇ işaret koymadıysa biz de UYDURMAYIZ: yanıt metni aynen kalır. (Değişmez
    tek yönlüdür — "metindeki her [n] geçerli bir kaynağa denk gelir"; her kaynağın
    metinde işareti olması gerekmez.)"""
    if not _MARKER_RE.search(answer or ""):
        return (answer or "").strip()
    text = _MARKER_RE.sub("", answer or "").strip()
    if not text or not numbering:
        return text

    inserts: list[tuple[int, int]] = []   # (pozisyon, n)
    trailing: list[int] = []
    for citation in citations:
        n = numbering.get(int(citation["chunk_id"]))
        if n is None:
            continue
        end = _find_claim_end(text, citation.get("claim") or "")
        if end is None:
            if n not in trailing:
                trailing.append(n)
        elif (end, n) not in inserts:
            inserts.append((end, n))

    for pos, n in sorted(inserts, key=lambda p: p[0], reverse=True):
        text = f"{text[:pos]} [{n}]{text[pos:]}"
    if trailing:
        text = text.rstrip() + " " + "".join(f"[{n}]" for n in trailing)
    return text


def _find_claim_end(text: str, claim: str) -> int | None:
    """Claim'in metindeki bitiş indeksi (yoksa None). Önce birebir, sonra harf-duyarsız."""
    claim = _MARKER_RE.sub("", claim or "").strip().rstrip(".")
    if not claim:
        return None
    idx = text.find(claim)
    if idx < 0:
        idx = text.casefold().find(claim.casefold())
    return idx + len(claim) if idx >= 0 else None


def compose_response(
    state: dict,
    *,
    config: EffectiveConfig | None = None,
    trace_id: str | None = None,
    declined: bool | None = None,
) -> dict:
    """FAZ 5 §5-v2: `sources` YALNIZCA cevabı destekleyen kanıttır. Reddedilen/fallback
    yolunda (`declined`) sources=[] olur ve incelenen-ama-yetersiz chunk'lar
    `meta.reviewed_sources`'a taşınır (citation DEĞİL). `declined=None` ise confidence=low
    reddetme sayılır; `declined=True` (fallback) confidence'ı da low'a zorlar."""
    cfg = config or load_config()
    agent_cfg = cfg.group("agent")
    citations = state.get("citations", [])
    retrieved = state.get("retrieved", [])
    examined, numbering = _sources(citations, retrieved)

    confidence = _confidence(state, high_threshold=float(agent_cfg.confidence_high_coverage_threshold))
    # M-16 FIX-2: validate GEÇSE bile cevap metni "bulunamadı" diyorsa reddetme say → sources=[]
    # (aşağıda). Böylece coverage eşiğinden bağımsız olarak reddeden cevap kaynak uydurmaz.
    if declined is not None:
        is_declined = declined
    else:
        is_declined = confidence == "low" or _text_declines(state.get("draft_answer"))
    if is_declined:
        confidence = "low"

    sources = [] if is_declined else examined
    reviewed = examined if is_declined else []

    # M-3(b): reddedilen yolda sources=[] → metinde askıda [n] KALMASIN (numbering={}).
    draft = _renumber_answer(state.get("draft_answer") or "", citations,
                             {} if is_declined else numbering)

    # FAZ 6 P2: output PII maskeleme — yanıt VE citation/reviewed quote'ları. İzlenebilir sayaç.
    policy = policy_from_config(cfg)
    answer, pii_count = mask_pii(draft, policy)
    for src in sources + reviewed:
        if src.get("quote"):
            src["quote"], c = mask_pii(src["quote"], policy)
            pii_count += c

    return {
        "answer": answer,
        "sources": sources,
        "confidence": confidence,
        "followups": [],
        "meta": {
            "iterations": int(state.get("budget", {}).get("iteration", 0)),
            "tokens": int(state.get("budget", {}).get("tokens_used", 0)),
            "latency_ms": 0,
            "model": "",
            "trace_id": trace_id or "",
            "generated_at": int(time.time()),
            "reviewed_sources": reviewed,
            "pii_masked_count": pii_count,
        },
    }

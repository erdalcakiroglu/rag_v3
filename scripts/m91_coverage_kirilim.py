"""M-16 FIX-1 ÖN-VERİ — coverage'ın CÜMLE-CÜMLE kırılımı (kod öncesi, ZORUNLU).

İnatçı fallback'ler (gs-002/022/023) coverage'a takılıyor. FIX-1 kararı iki kökten HANGİSİ
olduğuna bağlı:
  (i)  FRAMING: uncovered cümle atıfsız bağlayıcı/geçiş cümlesi (az içerik-token) → paydadan çıkar.
  (ii) ÖRTÜŞME: uncovered cümle GERÇEK claim ama modelin citation claim'iyle içerik-örtüşmesi
       <0.6 (Türkçe biçim farkı: "bulunmamaktadır" vs "bulunmuyor") → claim-eşleşme eşiği sorunu.

Bu betik her fallback için draft cümlelerini tek tek basar: covered? + içerik-token sayısı +
en iyi claim örtüşme oranı. Böylece FIX-1'in doğru kökü VERİYLE seçilir.

KULLANIM: docker exec -i ragintel-api env GOLDEN=v0.1 python - < scripts/m91_coverage_kirilim.py
"""
from __future__ import annotations

import os

from ragintel.agents.graph import run_agent
from ragintel.agents.nodes.validate import _context_chunks
from ragintel.eval import harness
from ragintel.guardrails.grounding import (
    _claim_covers_sentence,
    _content_tokens,
    _quote_supported,
    _sentences,
    _tokens,
)
from ragintel.text import normalize_for_quote

GOLDEN = os.environ.get("GOLDEN", "v0.1")
MODEL = os.environ.get("POC_MODEL") or os.environ.get("RAGINTEL_LLM_MODEL") or "qwen3.5:35b"
TARGETS = ["gs-v0-002", "gs-v0-022", "gs-v0-023", "gs-v0-012"]
QOV = 0.7  # quote_overlap_threshold (config default)


def _short(s, n=120):
    s = " ".join((str(s) or "").split())
    return s if len(s) <= n else s[:n] + "…"


def _best_overlap(claim_norm: str, sentence_norm: str) -> float:
    """_claim_covers_sentence'ın iç örtüşme oranı (teşhis için görünür kıl)."""
    if claim_norm in sentence_norm or sentence_norm in claim_norm:
        return 1.0
    ctok = _content_tokens(claim_norm)
    if len(ctok) < 2:
        return 0.0
    sset = set(_tokens(sentence_norm))
    return sum(1 for w in ctok if w in sset) / len(ctok)


db, cfg, model, app = harness.build_eval_app(MODEL)
try:
    with db.connection() as conn:
        by_id = {r["id"]: r for r in harness.repo.list_golden_records(conn, GOLDEN)}
    print(f"# M-16 FIX-1 ön-veri — coverage CÜMLE kırılımı — golden={GOLDEN} agent={model}\n")

    for gid in TARGETS:
        rec = by_id.get(gid)
        if rec is None:
            print(f"### {gid} — KAYIT YOK\n"); continue
        initial = {"query": rec["question"],
                   "user_ctx": {"user_id": "eval", "tenant_id": "eval", "roles": ["eval"],
                                "allowed_doc_scopes": [rec["doc_scope"]]},
                   "session_id": f"cov-{gid}", "retrieved": []}
        out = run_agent(app, initial)
        draft = out.get("draft_answer") or ""
        citations = out.get("citations") or []
        ctx_chunks = _context_chunks(out)
        # valid_claims: quote'u desteklenen citation'ların claim'i (validate_grounding ile aynı)
        ctx_map = {int(c["chunk_id"]): c for c in ctx_chunks}
        ctx_tokens = {t for c in ctx_chunks for t in _tokens(normalize_for_quote(c["text"]))}
        valid_claims = []
        for cit in citations:
            try:
                cid = int(cit["chunk_id"])
            except (TypeError, ValueError, KeyError):
                continue
            ch = ctx_map.get(cid)
            if ch is None:
                continue
            qn = normalize_for_quote(str(cit.get("quote", "")))
            if not _quote_supported(qn, normalize_for_quote(ch["text"]), ctx_tokens, QOV):
                continue
            cln = normalize_for_quote(str(cit.get("claim", "")))
            if cln:
                valid_claims.append(cln)

        sents = _sentences(draft)
        covered_n = 0
        print(f"### {gid}  citations={len(citations)} geçerli_claim={len(valid_claims)}  cümle={len(sents)}")
        for i, s in enumerate(sents, 1):
            sn = normalize_for_quote(s)
            cov = any(_claim_covers_sentence(cl, sn) for cl in valid_claims)
            best = max((_best_overlap(cl, sn) for cl in valid_claims), default=0.0)
            ntok = len(_content_tokens(sn))
            covered_n += 1 if cov else 0
            tag = "COVERED" if cov else ("FRAMING?" if ntok < 3 else "CLAIM-örtüşme")
            print(f"    S{i} [{tag}] tok={ntok} en_iyi_örtüşme={best:.2f}  {_short(s, 110)}")
        cov_rate = covered_n / len(sents) if sents else 0.0
        print(f"    → coverage = {covered_n}/{len(sents)} = {cov_rate:.3f}\n")

    print("############ FIX-1 OKUMA ############")
    print("Uncovered cümleler çoğunlukla FRAMING? (tok<3) → paydadan çıkar (framing exempt).")
    print("Uncovered cümleler CLAIM-örtüşme (tok≥3, örtüşme 0.4-0.6) → claim-eşleşme eşiğini gevşet.")
finally:
    try:
        db.close()
    except Exception:
        pass

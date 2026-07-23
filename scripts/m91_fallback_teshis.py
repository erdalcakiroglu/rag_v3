"""M-9.1 — 6 FALLBACK sorusunun teşhisi (ham cevap + getirilen bağlam).

M-9.1 karnesinde 6 answerable soru fallback verdi (agent 'bulunamadı' dedi) →
answer_relevancy=0.0. context_recall'a göre iki kök şüphesi:
  • retrieval ıskası (recall<1): kanıt getirilemedi → agent DOĞRU davranıp reddetti.
  • agent aşırı-temkinli (recall=1.0): kanıt TAM getirildi ama agent yine reddetti → agent kusuru.

Bu betik judge çağırmadan sadece agent'ı 6 soruda koşar; ham cevabı + getirilen bağlam
başlıklarını basar → hangi kök gerçek, gözle karar verilir. Checkpoint yazılmadığı için
ham cevaplar diske düşmemişti; bu onları geri üretir.

KULLANIM (H200): docker exec -i ragintel-api env GOLDEN=v0.1 python - < scripts/m91_fallback_teshis.py
"""
from __future__ import annotations

import os

from ragintel.eval import harness

GOLDEN = os.environ.get("GOLDEN", "v0.1")
MODEL = os.environ.get("POC_MODEL") or os.environ.get("RAGINTEL_LLM_MODEL") or "qwen3.5:35b"

# M-9.1 fallback kümesi + o koşudaki context_recall (kök şüphesini etiketlemek için).
TARGETS = {
    "gs-v0-002": ("retrieval-şüphe", 0.5),
    "gs-v0-012": ("retrieval-şüphe", 0.333),
    "gs-v0-019": ("agent-şüphe", 1.0),
    "gs-v0-022": ("agent-şüphe", 1.0),
    "gs-v0-023": ("agent-şüphe", 1.0),
    "gs-v0-026": ("agent-şüphe", 1.0),
}


def _short(s: str, n: int = 100) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n] + "…"


db, cfg, model, app = harness.build_eval_app(MODEL)
try:
    with db.connection() as conn:
        records = harness.repo.list_golden_records(conn, GOLDEN)
    by_id = {r["id"]: r for r in records}
    print(f"# M-9.1 fallback teşhisi — golden={GOLDEN} agent={model}  (judge YOK)\n")

    for gid, (kok_suphe, recall) in TARGETS.items():
        rec = by_id.get(gid)
        if rec is None:
            print(f"### {gid} — KAYIT YOK ({GOLDEN} setinde bulunamadı)\n")
            continue
        row = harness.run_question(app, rec, ctx_cap=int(cfg.group("eval").ctx_cap))
        fb = harness.is_fallback(row)
        ctxs = row["contexts"]
        print(f"### {gid}  [{rec['category']}]  şüphe={kok_suphe}  recall(karne)={recall}")
        print(f"    SORU     : {_short(rec['question'], 160)}")
        print(f"    İDEAL    : {_short(rec['ideal_answer'], 160)}")
        print(f"    CEVAP    : {_short(row['answer'], 300)}")
        print(f"    fallback?: {fb}   confidence={row['confidence']}   "
              f"iters={row['iterations']}   sources={len(row['sources'])}   n_ctx={len(ctxs)}")
        for i, c in enumerate(ctxs[:6], 1):
            print(f"      ctx[{i}] {_short(c, 120)}")
        # Kanıt gerçekten geldi mi: ideal cevabın anahtar kelimeleri bağlamda geçiyor mu?
        joined = " ".join(ctxs).lower()
        key_terms = [w for w in rec["ideal_answer"].split() if len(w) > 4][:8]
        hit = [w for w in key_terms if w.lower() in joined]
        print(f"    kanıt-izi: ideal anahtar {len(hit)}/{len(key_terms)} bağlamda "
              f"({', '.join(hit[:6]) or '—'})\n")

    print("############ TEŞHİS OKUMA ############")
    print("recall<1 + kanıt-izi düşük → RETRIEVAL ıskası (derinlik/hybrid).")
    print("recall=1 + kanıt-izi YÜKSEK ama fallback=True → AGENT aşırı-temkinli red (asıl kurtarma).")
finally:
    try:
        db.close()
    except Exception:
        pass

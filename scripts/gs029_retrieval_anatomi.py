"""gs-v0-029 LATENCY OUTLIER ÖN-VERİ — retrieval+embed+DB kovasını anatomile (salt-ölçüm).

ÇIPA (docs/M15_Latency_Anatomisi.md:145): bir koşumda gs-v0-029 24.1s sürdü, LLM payı %30;
**16.8s retrieval+embed+DB tarafındaydı** (zemin ~1.5s, %11). "Tek gözlem, incelenmedi."

ÖN-VERİ ÇATALI (kod ÖNCESİ): 16.8s kovası WARM tekrarlarda
  (a) ~1.5s'e mi oturuyor  → tek-seferlik artefakt (warming penceresi / Ollama contention /
      soğuk TEI-timeout); per-query kalıcı sorun YOK → warm-up zaten kapsıyor, aksiyon yok.
  (b) reprodüktif yüksek mi → embed_ms / db_ms / rerank_ms'ten HANGİSİ diye lokalize et.

BU BETİK KOD/CONFIG DEĞİŞTİRMEZ. Retrieval kovasını graph'tan İZOLE ölçer:
  service.search_hybrid  → embed_ms + db_ms (gerçek yol, retrieval_timing loglar)
  service.rerank         → rerank_ms (backend tei/passthrough; TEI soğuksa timeout+fail-open)
İzole embed_ms ayrıca _embed_query ile alınır (log'a bağımlı kalmamak için).

KULLANIM: docker exec -i ragintel-api env GOLDEN=v0.1 REPEATS=5 python - < scripts/gs029_retrieval_anatomi.py
  (FULL=1 verilirse ek olarak tek uçtan-uca run_agent koşup gerçek bucket toplamını da basar.)
"""
from __future__ import annotations

import os
import statistics
import time

from ragintel.eval import harness
from ragintel.retrieval import RetrievalService

GOLDEN = os.environ.get("GOLDEN", "v0.1")
MODEL = os.environ.get("POC_MODEL") or os.environ.get("RAGINTEL_LLM_MODEL") or "qwen3.5:35b"
REPEATS = int(os.environ.get("REPEATS", "5"))
TARGET = os.environ.get("QID", "gs-v0-029")
FULL = os.environ.get("FULL") == "1"


def _ms(v):
    return f"{v:7.1f}ms"


def _endpoint(emb) -> str:
    for a in ("api_base", "base_url", "endpoint", "url", "host"):
        v = getattr(emb, a, None)
        if v:
            return f"{a}={v}"
    return f"<{type(emb).__name__}>"


db, cfg, model, app = harness.build_eval_app(MODEL)
service = RetrievalService(db=db, config=cfg)
rc = service.retrieval_cfg

print(f"# gs-029 RETRIEVAL ANATOMİ — golden={GOLDEN} soru={TARGET} repeat={REPEATS} agent_model={model}")
print("# config (DOKUNULMADI): "
      f"rerank_backend={getattr(rc, 'rerank_backend', '?')} "
      f"rerank_timeout_sec={getattr(rc, 'rerank_timeout_sec', '?')} "
      f"default_top_k={getattr(rc, 'default_top_k', '?')} "
      f"vector_ef_search={getattr(rc, 'vector_ef_search', '?')} "
      f"hybrid_fusion={getattr(rc, 'hybrid_fusion', '?')}")
print(f"# embedder: {_endpoint(service.embedder)}")
print(f"# ÇIPA: outlier=16.8s (retrieval+embed+DB), zemin~1.5s. WARM tekrar bunu çürütür/onaylar.\n")

with db.connection() as conn:
    records = harness.repo.list_golden_records(conn, GOLDEN)
rec = next((r for r in records if r["id"] == TARGET), None)
if rec is None:
    raise SystemExit(f"ABORT: {TARGET} golden={GOLDEN} içinde yok")

q = rec["question"]
user_ctx = {"user_id": "eval", "tenant_id": "eval", "roles": ["eval"],
            "allowed_doc_scopes": [rec["doc_scope"]]}
print(f"SORU: {q}\ndoc_scope={rec['doc_scope']}\n")

# --- WARM: soğuk-başlangıç cezalarını ölçüm-dışına taşı (runtime.warm_up ile aynı mantık) ---
service.embedder.embed_batch(["ısınma"])
warm_chunks = service.search_hybrid(q, user_ctx=user_ctx)
warm_ids = [int(c["chunk_id"]) for c in warm_chunks]
if warm_ids:
    service.rerank(q, warm_ids, user_ctx=user_ctx)
print(f"# WARM bitti (search sonuç={len(warm_chunks)} chunk). Ölçüm tekrarları warm zeminde.\n")

rows = []
for i in range(1, REPEATS + 1):
    # izole embed (log'a bağımlı olmayan kanıt)
    _, embed_iso_ms = service._embed_query(q)

    t0 = time.perf_counter()
    chunks = service.search_hybrid(q, user_ctx=user_ctx)
    search_wall = (time.perf_counter() - t0) * 1000.0
    ids = [int(c["chunk_id"]) for c in chunks]

    t1 = time.perf_counter()
    if ids:
        service.rerank(q, ids, user_ctx=user_ctx)
    rerank_wall = (time.perf_counter() - t1) * 1000.0

    bucket = search_wall + rerank_wall
    rows.append((embed_iso_ms, search_wall, rerank_wall, bucket, len(ids)))
    print(f"  rep {i}: embed(izole)={_ms(embed_iso_ms)}  search_wall(embed+db)={_ms(search_wall)}  "
          f"rerank_wall={_ms(rerank_wall)}  KOVA={_ms(bucket)}  chunk={len(ids)}")

print("\n############ SONUÇ (warm, ms) ############")
for name, idx in (("embed(izole)", 0), ("search_wall", 1), ("rerank_wall", 2), ("KOVA(retrieval+embed+DB+rerank)", 3)):
    vals = [r[idx] for r in rows]
    print(f"  {name:34s} min={min(vals):8.1f}  med={statistics.median(vals):8.1f}  max={max(vals):8.1f}")

bucket_med = statistics.median([r[3] for r in rows]) / 1000.0
print("\n############ OKUMA ############")
if bucket_med <= 3.0:
    print(f"  KOVA med={bucket_med:.2f}s → zemin (~1.5s) mertebesinde, 16.8s REPRODÜKTİF DEĞİL.")
    print("  16.8s tek-seferlik artefakt (warming penceresi / Ollama contention / soğuk TEI-timeout).")
    print("  warm-up (tokenizer+embedder) bunu zaten startup'a taşıyor; per-query kalıcı sorun YOK.")
    print("  → gs-029 outlier'ı AKSİYON YOK ile kapanmaya aday; kalan risk yalnız eşzamanlılık (§5, ayrı iş).")
else:
    print(f"  KOVA med={bucket_med:.2f}s → 16.8s REPRODÜKTİF. Yukarıdaki embed/search/rerank")
    print("  kırılımından hangi bileşenin şiştiğini oku (embed=Ollama, db=pgvector, rerank=TEI/timeout).")

if FULL:
    from ragintel.agents.graph import run_agent
    print("\n############ FULL: uçtan-uca run_agent (LLM dahil) ############")
    initial = {"query": q, "user_ctx": user_ctx, "session_id": "gs029-anatomi", "retrieved": []}
    t = time.perf_counter()
    out = run_agent(app, initial)
    total = time.perf_counter() - t
    fr = out.get("final_response") or {}
    print(f"  uçtan-uca={total:.2f}s  conf={fr.get('confidence')}  kaynak={len(fr.get('sources') or [])}")
    print("  (retrieval_timing/rerank_timing/llm_call_timing log satırları stdout'ta kovaları verir.)")

try:
    db.close()
except Exception:
    pass

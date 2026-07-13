"""İP-2.3 — Uçtan uca eval harness (DEV-MODE, judge=groq/dev-mode).

Akış:
1. Golden v0 (DB) → 31 answerable + 5 unanswerable.
2. Her soru GERÇEK agent graph'ından geçer (Groq LLM + gerçek retrieval) →
   {question, answer, contexts, ground_truth} + iterasyon/confidence/sources.
3. Answerable → 4 RAGAS-tarzı metrik, temperature=0 + 3 koşu MEDYANI (judge varyansı).
4. Unanswerable → RAGAS'a GİRMEZ; deterministik DÜRÜSTLÜK kontrolü (sistem
   "bulunamadı"/fallback döndü mü, kaynak uydurdu mu?).
5. Rapor: kategori kırılımı + İTERASYON DAĞILIMI (max_iterations kararının verisi).

Sonuçlar `judge=groq/dev-mode` etiketli: RESMİ KARNE DEĞİL (bkz. judge.py).
"""

from __future__ import annotations

import json
import os
import time

from ..agents.graph import build_agent_graph, run_agent
from ..agents.tools import ToolRegistry
from ..config.loader import load_config
from ..config.settings import DbSettings, LiteLLMSettings
from ..database import Database
from ..database.config_store import make_db_reader
from ..llm.gateway import LiteLLMGateway
from ..observability.logging import get_logger
from ..retrieval import ContextBuilder, RetrievalService
from . import repository as repo
from .judge import (
    JUDGE_LABEL,
    Judge,
    JudgeEmbedder,
    answer_relevancy,
    context_precision,
    context_recall,
    faithfulness,
    median,
)

_LOG = get_logger("eval.harness")

_METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
# MVP dev-göstergeleri (spec İP-2.3): geçse de geçmese de sayı raporlanır.
_TARGETS = {"faithfulness": 0.85, "context_precision": 0.80}
# Unanswerable dürüstlük: sistem doğru şekilde "bulunamadı" dedi mi?
_NOTFOUND_MARKERS = (
    "bulunmamaktadır", "bulunamadı", "bulunmuyor", "bulunmamakta", "mevcut değil",
    "yer almamaktadır", "güvenilir yanıt üretilemedi", "dokümanlarda bulunm",
    "belgelerde bulunm", "bilgi bulunm", "yanıt üretilemedi",
)
# M-4: efektif değer `eval.ctx_cap` (DB > ENV > default); bu yalnızca fallback.
_CTX_CAP = 10  # judge maliyeti: en fazla bu kadar bağlam parçası değerlendirilir


def _load_ck(path: str | None) -> dict:
    """Checkpoint: {"answers":{id:row}, "scores":{id:scores}, "errors":{id:msg}}."""
    ck = {"answers": {}, "scores": {}, "errors": {}}
    if path and os.path.exists(path):
        data = json.load(open(path, encoding="utf-8"))
        for k in ck:
            ck[k] = data.get(k, {})
    return ck


def _save_ck(path: str | None, ck: dict) -> None:
    if not path:
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ck, fh, ensure_ascii=False)
    os.replace(tmp, path)  # atomik yazım (kesinti güvenli)


def _rate_limited(exc: Exception) -> bool:
    """Gateway/judge retry'ları tükendikten sonra hâlâ rate-limit → günlük kap: durakla."""
    import litellm

    if isinstance(exc, litellm.RateLimitError):
        return True
    s = str(exc).lower()
    return "rate limit" in s or "tokens per" in s or "too large" in s


def _cat_order(cats):
    order = ["single_fact", "citation_sensitive", "synthesis", "table_based", "multi_hop", "unanswerable"]
    return [c for c in order if c in cats] + sorted(c for c in cats if c not in order)


# Eval ajan modeli: Groq'ta tool-calling GÜVENİLİR olan model. llama-3.3-70b bizim
# tool şemalarında (özellikle iç içe citations'lı submit_answer + integer top_k)
# server-side şema doğrulamasında düşüyor; qwen/qwen3-32b temiz tool-call üretir
# (mühürlü H200 modeliyle — qwen3.5:35b — aynı aile). Judge tool kullanmaz → llama iyi.
DEFAULT_AGENT_MODEL = "qwen/qwen3-32b"
DEFAULT_JUDGE_MODEL = "llama-3.3-70b-versatile"


def build_eval_app(agent_model: str | None = None):
    """Eval için graph + çevre nesneleri (bulut gateway + gerçek retrieval, checkpoint yok).

    M-7: ajan modeli önceliği CLI > **DB `eval.agent_model`** > .env > kod default.

    NEDEN DB-OTORİTER (öncelik değişti): eskiden `.env RAGINTEL_LLM_MODEL` DB'yi EZİYORDU.
    Sonuç: `.env`'de kalmış bir provider-swap override'ı (deepseek-v4-pro) yüzünden gate,
    KARNENİN AGENT'INDAN BAŞKA bir modelle koşuyor ve bunu hiçbir yere yazmıyordu —
    ölçüm zemini sessizce kayıyordu (karneyle kıyaslanamaz sayılar "regresyon" sanıldı).
    Karnenin agent'ı DAVRANIŞSAL bir karardır; yeri DB'dir (config-first), makinede
    kalmış bir .env satırı değil. `.env` yalnızca DB boşsa devreye girer.
    """
    db = Database(DbSettings()).open()
    cfg = load_config(db_reader=make_db_reader(db))
    model = (agent_model or cfg.group("eval").agent_model
             or LiteLLMSettings().model or DEFAULT_AGENT_MODEL)
    gateway = LiteLLMGateway(model=model, settings=LiteLLMSettings())
    service = RetrievalService(db=db, config=cfg)
    context_builder = ContextBuilder(db=db, config=cfg)
    registry = ToolRegistry(service)
    app = build_agent_graph(
        gateway=gateway, context_builder=context_builder, registry=registry, config=cfg, checkpointer=None
    )
    return db, cfg, model, app


def _contexts_from_out(out: dict, cap: int = _CTX_CAP) -> list[str]:
    ctx = out.get("context") or {}
    blocks = ctx.get("blocks") or []
    if blocks:
        texts = [str(b.get("text", "")) for b in blocks]
    else:
        texts = [str(c.get("text", "")) for c in (out.get("retrieved") or [])]
    return [t for t in texts if t.strip()][:cap]


def run_question(app, rec: dict, *, ctx_cap: int = _CTX_CAP) -> dict:
    """Bir golden kaydı agent graph'ından geçirir; eval satırı döndürür."""
    initial = {
        "query": rec["question"],
        "user_ctx": {
            "user_id": "eval", "tenant_id": "eval", "roles": ["eval"],
            "allowed_doc_scopes": [rec["doc_scope"]],
        },
        "session_id": f"eval-{rec['id']}",
        "retrieved": [],
    }
    out = run_agent(app, initial)
    final = out.get("final_response") or {}
    return {
        "id": rec["id"],
        "category": rec["category"],
        "answerable": bool(rec["answerable"]),
        "question": rec["question"],
        "ground_truth": rec["ideal_answer"],
        "answer": str(final.get("answer") or ""),
        "contexts": _contexts_from_out(out, ctx_cap),
        "confidence": final.get("confidence", ""),
        "sources": final.get("sources") or [],
        "iterations": int((final.get("meta") or {}).get("iterations", 0)),
    }


def _score_answerable(judge: Judge, embedder: JudgeEmbedder, row: dict, runs: int) -> dict:
    """4 metrik × `runs` koşu → metrik başına MEDYAN."""
    per_run = {m: [] for m in _METRICS}
    for _ in range(runs):
        per_run["faithfulness"].append(faithfulness(judge, row["answer"], row["contexts"]))
        per_run["answer_relevancy"].append(
            answer_relevancy(judge, embedder, row["question"], row["answer"])
        )
        per_run["context_precision"].append(
            context_precision(judge, row["question"], row["contexts"], row["ground_truth"])
        )
        per_run["context_recall"].append(
            context_recall(judge, row["ground_truth"], row["contexts"])
        )
    return {m: round(median(per_run[m]), 4) for m in _METRICS}


def _honesty(row: dict) -> dict:
    """Unanswerable dürüstlük: 'bulunamadı' dedi mi (declined) VE kaynak uydurmadı mı?
    Tanım KATI: cevapsızda herhangi citation = fail. `kind` sadece raporlama için sınıf
    (fail/pass'i DEĞİŞTİRMEZ): honest | border_declined_cited (bulunamadı DEDİ ama cite etti)
    | fabricated_confident (net halüsinasyon: declined yok + cite)."""
    ans = (row["answer"] or "").lower()
    declined = row["confidence"] == "low" or any(m in ans for m in _NOTFOUND_MARKERS)
    fabricated = len(row["sources"]) > 0
    honest = bool(declined and not fabricated)
    if honest:
        kind = "honest"
    elif declined and fabricated:
        kind = "border_declined_cited"
    elif fabricated:
        kind = "fabricated_confident"
    else:
        kind = "other"
    return {
        "id": row["id"], "confidence": row["confidence"], "n_sources": len(row["sources"]),
        "iterations": row["iterations"], "declined": declined, "fabricated_sources": fabricated,
        "honest": honest, "kind": kind,
    }


def _aggregate(scored: list[dict]) -> dict:
    def mean_of(rows, m):
        vals = [r[m] for r in rows]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    overall = {m: mean_of(scored, m) for m in _METRICS}
    overall["n"] = len(scored)
    by_cat = {}
    for cat in _cat_order({r["category"] for r in scored}):
        rows = [r for r in scored if r["category"] == cat]
        by_cat[cat] = {m: mean_of(rows, m) for m in _METRICS}
        by_cat[cat]["n"] = len(rows)
    return {"overall": overall, "by_category": by_cat}


def _iteration_stats(rows: list[dict]) -> dict:
    iters = [r["iterations"] for r in rows]
    dist: dict[int, int] = {}
    for i in iters:
        dist[i] = dist.get(i, 0) + 1
    by_cat = {}
    for cat in _cat_order({r["category"] for r in rows}):
        cvals = [r["iterations"] for r in rows if r["category"] == cat]
        by_cat[cat] = round(sum(cvals) / len(cvals), 2) if cvals else 0.0
    return {
        "mean": round(sum(iters) / len(iters), 2) if iters else 0.0,
        "max": max(iters) if iters else 0,
        "distribution": {str(k): dist[k] for k in sorted(dist)},
        "by_category": by_cat,
    }


def evaluate(*, version: str = "v0", limit: int | None = None, runs: int = 3,
             agent_model: str | None = None, judge_model: str | None = None,
             question_delay: float = 1.0, out_path: str | None = None) -> dict:
    """Uçtan uca eval; `limit` → dry-run. `out_path` → checkpoint/resume (gece koşusu):
    her soru/skor sonrası kaydedilir; günlük rate-limit kapına takılınca zarifçe DURAKLAR
    (status=paused), tekrar koşulunca kaldığı yerden devam eder; tamamlanınca status=complete."""
    db, cfg, model, app = build_eval_app(agent_model)
    try:
        with db.connection() as conn:
            records = repo.list_golden_records(conn, version)
        if not records:
            raise RuntimeError(f"'{version}' golden set DB'de yok — önce yükleyin.")

        answerable = [r for r in records if r["answerable"]]
        unanswerable = [r for r in records if not r["answerable"]]
        if limit is not None:
            # dry-run: ilk `limit` answerable + en çok 2 unanswerable (dürüstlük yolu da denensin)
            answerable = answerable[:limit]
            unanswerable = unanswerable[: min(2, limit)]

        judge = Judge(model=judge_model or LiteLLMSettings().model
                      or cfg.group("eval").judge_model or DEFAULT_JUDGE_MODEL)
        embedder = JudgeEmbedder()
        t0 = time.perf_counter()
        ck = _load_ck(out_path)
        queue = answerable + unanswerable
        by_id = {r["id"]: r for r in queue}
        paused = None  # ("answer"|"score", id) — günlük kap durağı

        # 1) Dataset üretimi (resumable) — checkpoint'te olan sorular atlanır
        _LOG.info("eval_dataset_start", answerable=len(answerable), unanswerable=len(unanswerable),
                  judge=judge.label, agent_model=model, resumed=len(ck["answers"]))
        for idx, rec in enumerate(queue):
            if rec["id"] in ck["answers"] or rec["id"] in ck["errors"]:
                continue
            try:
                row = run_question(app, rec, ctx_cap=int(cfg.group("eval").ctx_cap))
                ck["answers"][rec["id"]] = row
                _save_ck(out_path, ck)
                _LOG.info("eval_answered", id=rec["id"], iterations=row["iterations"],
                          confidence=row["confidence"], answerable=rec["answerable"])
            except Exception as exc:
                if _rate_limited(exc):
                    paused = ("answer", rec["id"])
                    _LOG.warning("eval_paused_ratelimit", phase="answer", id=rec["id"])
                    break
                ck["errors"][rec["id"]] = str(exc)[:200]
                _save_ck(out_path, ck)
                _LOG.warning("eval_answer_failed", id=rec["id"], error=str(exc)[:160])
            if question_delay and idx < len(queue) - 1:
                time.sleep(question_delay)  # TPM yumuşatma (gateway backoff'a ek throttle)

        # 2) RAGAS-tarzı skorlama (answerable, `runs` koşu medyanı; resumable)
        if paused is None:
            for rid, row in ck["answers"].items():
                if not row.get("answerable") or rid in ck["scores"]:
                    continue
                try:
                    ck["scores"][rid] = _score_answerable(judge, embedder, row, runs)
                    _save_ck(out_path, ck)
                    _LOG.info("eval_scored", id=rid, **ck["scores"][rid])
                except Exception as exc:
                    if _rate_limited(exc):
                        paused = ("score", rid)
                        _LOG.warning("eval_paused_ratelimit", phase="score", id=rid)
                        break
                    ck["errors"][rid] = str(exc)[:200]
                    _save_ck(out_path, ck)

        # 3) Rapor derleme (kısmi veya tam)
        ans_rows = [r for r in ck["answers"].values() if r.get("answerable")]
        unans_rows = [r for r in ck["answers"].values() if not r.get("answerable")]
        scored = [
            {"id": rid, "category": ck["answers"][rid]["category"],
             "iterations": ck["answers"][rid]["iterations"], "confidence": ck["answers"][rid]["confidence"],
             **ck["scores"][rid]}
            for rid in ck["scores"] if rid in ck["answers"]
        ]
        honesty_rows = [_honesty(r) for r in unans_rows]
        honest_pass = sum(1 for h in honesty_rows if h["honest"])
        agg = _aggregate(scored)
        iters = _iteration_stats(ans_rows + unans_rows)
        targets = {
            k: {"target": v, "value": agg["overall"][k], "pass": agg["overall"][k] >= v}
            for k, v in _TARGETS.items()
        }
        done_answers = len(ck["answers"]) + len(ck["errors"])
        complete = paused is None and done_answers >= len(queue) and len(scored) == len(ans_rows)
        return {
            "judge": judge.label, "judge_model": judge.model, "agent_model": model,
            "golden": version, "mode": "report", "dev_mode": True, "runs": runs, "limit": limit,
            "status": "complete" if complete else "paused",
            "paused_at": {"phase": paused[0], "id": paused[1]} if paused else None,
            "progress": {"answered": len(ck["answers"]), "scored": len(scored),
                         "queue": len(queue), "answerable": len(answerable)},
            "elapsed_sec": round(time.perf_counter() - t0, 1),
            "checkpoint": out_path,
            "dataset": {"answerable_run": len(ans_rows), "unanswerable_run": len(unans_rows),
                        "answered_with_context": sum(1 for r in ans_rows if r.get("contexts")),
                        "errors": [{"id": k, "error": v} for k, v in ck["errors"].items()]},
            "ragas": {**agg, "per_question": scored},
            "honesty": {"pass": honest_pass, "total": len(honesty_rows),
                        "score": f"{honest_pass}/{len(honesty_rows)}", "per_question": honesty_rows},
            "iterations": iters,
            "targets": targets,
        }
    finally:
        db.close()


def format_report(result: dict) -> str:
    L = []
    L.append(f"=== İP-2.3 Eval Raporu — judge={result['judge']} (DEV-MODE, resmi karne DEĞİL) ===")
    st = result.get("status", "complete")
    pr = result.get("progress", {})
    status_line = f"DURUM: {st.upper()}"
    if st == "paused":
        pa = result.get("paused_at") or {}
        status_line += (f" (günlük kap — {pa.get('phase')}@{pa.get('id')}) · "
                        f"ilerleme: {pr.get('answered')}/{pr.get('queue')} yanıt, "
                        f"{pr.get('scored')}/{pr.get('answerable')} skor → tekrar koşunca devam eder")
    L.append(status_line)
    L.append(f"golden={result['golden']} · agent={result['agent_model']} · judge={result['judge_model']} "
             f"· runs={result['runs']} (medyan) · süre={result['elapsed_sec']}s"
             + (f" · DRY-RUN limit={result['limit']}" if result.get("limit") else ""))
    ds = result["dataset"]
    L.append(f"dataset: {ds['answerable_run']} answerable + {ds['unanswerable_run']} unanswerable"
             + (f" · {len(ds['errors'])} HATA" if ds["errors"] else ""))

    ov = result["ragas"]["overall"]
    cols = list(_METRICS)
    header = "kategori".ljust(20) + "n   " + "  ".join(c[:13].ljust(13) for c in cols)
    L.append("\n-- RAGAS-tarzı metrikler (answerable) --")
    L.append(header)
    L.append("-" * len(header))
    L.append("GENEL".ljust(20) + f"{ov['n']:<4}" + "  ".join(f"{ov[c]:<13.3f}" for c in cols))
    for cat, m in result["ragas"]["by_category"].items():
        L.append(cat.ljust(20) + f"{m['n']:<4}" + "  ".join(f"{m[c]:<13.3f}" for c in cols))

    L.append("\n-- Dev-hedef kıyas (gösterge; geçmese de sayı geçerli) --")
    for k, t in result["targets"].items():
        L.append(f"  {k:20s} {t['value']:.3f}  (hedef ≥{t['target']}) → {'GEÇTİ' if t['pass'] else 'ALTINDA'}")

    h = result["honesty"]
    L.append(f"\n-- Unanswerable dürüstlük (RAGAS dışı, deterministik): {h['score']} --")
    for r in h["per_question"]:
        flag = "✓" if r["honest"] else "✗"
        L.append(f"  {flag} {r['id']}  [{r.get('kind','')}] conf={r['confidence']} kaynak={r['n_sources']} "
                 f"declined={r['declined']} uydurma={r['fabricated_sources']} iter={r['iterations']}")

    it = result["iterations"]
    L.append(f"\n-- İterasyon dağılımı (max_iterations kararı verisi) — ort={it['mean']} max={it['max']} --")
    L.append("  tur→adet: " + ", ".join(f"{k}:{v}" for k, v in it["distribution"].items()))
    L.append("  kategori ort: " + ", ".join(f"{c}={v}" for c, v in it["by_category"].items()))
    if result["dataset"]["errors"]:
        L.append("\n-- HATALAR --")
        for e in result["dataset"]["errors"]:
            L.append(f"  {e['id']}: {e['error']}")
    return "\n".join(L)

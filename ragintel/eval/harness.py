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
    gateway = LiteLLMGateway(model=model, settings=LiteLLMSettings(),
                             reasoning_effort=str(cfg.group("agent").reasoning_effort),
                             temperature=float(cfg.group("agent").temperature))
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
    val = out.get("validation") or {}
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
        # M-17/D4: honesty ölçütü coverage'ı okur (validate'in ZATEN hesapladığı iddia-kapsama
        # oranı). Checkpoint'e yazılır ki _honesty gate anında görebilsin.
        "coverage": val.get("coverage"),
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
    """Unanswerable dürüstlük (M-17, D4): 'bulunamadı' dedi mi (declined) VE bağlanmamış
    iddia bırakmadı mı?

    `fabricated = kaynak VAR ve coverage < 1.0` — yani validate'e göre en az bir iddia cümlesi
    geçerli citation'a bağlanmamış. Saf ret (kaynak=0) kısa-devre honest'tır: deterministik ret
    metnidir, serbest-form iddia barındırmaz (M-17 TEYİT-3 ile canlı doğrulandı: 9/9 satır sabit
    şablon). NEDEN eski `len(sources)>0` (D0) değil: reddedip söylediği bağlamı GEÇERLİ kaynağa
    bağlayan cevabı (coverage=1.0) D0 haksızca 'uydurma' sayıyordu (`border_declined_cited`).
    coverage'ı ölçüte taşımak bu haksız cezayı kaldırır; grounding'in ZATEN hesapladığı oranı
    OKUR, yeni hesap yapmaz, entailment gerektirmez.

    KALAN DELİK: coverage sözcük-örtüşmesidir, anlamsal destek değil → ATIFLI-uydurma yalnız
    entailment ON ile kapanır. Ama coverage<1.0 olan atıflı-EKSİK cevabı D4 zaten fail eder,
    o yüzden fabrication maskelenmez.

    coverage YOKSA (eski checkpoint / validation'sız satır) ve kaynak varsa: KATI davran
    (fabricated=True). Doğrulayamadığımız bir grounding'i honest saymayız.

    `honest_strict` eski D0'dır — Δ raporu ("tanımsal kayma, davranış değil") için taşınır.
    `kind` yalnız raporlama sınıfıdır (fail/pass'i DEĞİŞTİRMEZ): honest | declined_uncovered
    (reddetti ama bağlanmamış iddia var) | fabricated_confident (net halüsinasyon: declined yok
    + kaynak) | other."""
    ans = (row["answer"] or "").lower()
    declined = row["confidence"] == "low" or any(m in ans for m in _NOTFOUND_MARKERS)
    n_src = len(row["sources"])
    cov = row.get("coverage")
    covered = isinstance(cov, (int, float)) and not isinstance(cov, bool) and float(cov) >= 1.0
    fabricated = n_src > 0 and not covered
    honest = bool(declined and not fabricated)
    honest_strict = bool(declined and n_src == 0)   # eski D0 — Δ için
    if honest:
        kind = "honest"
    elif declined and fabricated:
        kind = "declined_uncovered"
    elif fabricated:
        kind = "fabricated_confident"
    else:
        kind = "other"
    return {
        "id": row["id"], "confidence": row["confidence"], "n_sources": n_src,
        "coverage": cov, "iterations": row["iterations"], "declined": declined,
        "fabricated_sources": fabricated, "honest": honest, "honest_strict": honest_strict,
        "kind": kind,
    }


def is_fallback(row: dict) -> bool:
    """Cevaplanabilir bir soruya 'bulunamadı' döndüyse bu bir FALLBACK'tir.

    M-9: Fallback, TANIMI GEREĞİ sadıktır (hiçbir iddia öne sürmez) ve bağlamı da
    'isabetli' sayılır → faithfulness/context_precision'ı YAPISAL olarak şişirir.
    Ölçüldü: fallback veren satırlar faithfulness 0.937 / answer_relevancy 0.000;
    gerçek cevap verenler 0.985 / 0.692. Genel ortalama bu yüzden yanıltıcıdır.
    """
    ans = (row.get("answer") or "").lower()
    return any(m in ans for m in _NOTFOUND_MARKERS)


def _aggregate(scored: list[dict]) -> dict:
    def mean_of(rows, m):
        vals = [r[m] for r in rows]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    def blok(rows: list[dict]) -> dict:
        d = {m: mean_of(rows, m) for m in _METRICS}
        d["n"] = len(rows)
        return d

    cevaplananlar = [r for r in scored if not r.get("fallback")]
    overall = blok(scored)
    by_cat = {cat: blok([r for r in scored if r["category"] == cat])
              for cat in _cat_order({r["category"] for r in scored})}
    # ANSWERED-ONLY: tek dürüst kalite özeti — fallback'lerin şişirmesi olmadan.
    answered_only = blok(cevaplananlar)
    answered_by_cat = {cat: blok([r for r in cevaplananlar if r["category"] == cat])
                       for cat in _cat_order({r["category"] for r in cevaplananlar})}
    n_fb = len(scored) - len(cevaplananlar)
    return {
        "overall": overall,
        "by_category": by_cat,
        "answered_only": {"overall": answered_only, "by_category": answered_by_cat},
        "fallback": {
            "count": n_fb, "total": len(scored),
            "rate": round(n_fb / len(scored), 4) if scored else 0.0,
            "ids": [r["id"] for r in scored if r.get("fallback")],
        },
    }


def _fallback_by_repeat(scored: list[dict], agent_runs: int) -> dict:
    """Tekrar başına fallback oranı — DAĞILIM, tek sayı değil. Koşumlar arası fark
    büyükse ortalama tek başına yanıltır (gürültü tabanı ölçüldü: ±6/31)."""
    out = {}
    for i in range(agent_runs):
        rows = [r for r in scored if r.get("repeat") == i]
        n_fb = sum(1 for r in rows if r.get("fallback"))
        out[str(i)] = {"count": n_fb, "total": len(rows),
                       "rate": round(n_fb / len(rows), 4) if rows else 0.0}
    return out


def _kararsiz(scored: list[dict]) -> list[str]:
    """Tekrarlar arasında YÖN DEĞİŞTİREN sorular (bazen cevap, bazen fallback).
    Bunlar eşiğin kıyısında salınır; mühürlenen sayının güven aralığını bunlar belirler."""
    by_q: dict[str, set] = {}
    for r in scored:
        by_q.setdefault(r.get("rec_id", r["id"]), set()).add(bool(r.get("fallback")))
    return sorted(q for q, v in by_q.items() if len(v) > 1)


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
             question_delay: float = 1.0, out_path: str | None = None,
             all_unanswerable: bool = False, agent_runs: int = 1) -> dict:
    """Uçtan uca eval; `limit` → dry-run. `out_path` → checkpoint/resume (gece koşusu):
    her soru/skor sonrası kaydedilir; günlük rate-limit kapına takılınca zarifçe DURAKLAR
    (status=paused), tekrar koşulunca kaldığı yerden devam eder; tamamlanınca status=complete.

    `agent_runs` (M-9 kuralı): soru başına AGENT tekrar sayısı. Bu sistemde tek agent koşumu
    GÜRÜLTÜDÜR — ölçüldü: aynı 31 soruda arka arkaya iki koşum 12'sinde yön değiştirdi
    (gürültü tabanı ±6/31). `runs` yalnızca JUDGE'ı medyanlıyordu; agent tarafı tek örnekti.
    Kalite iddiası taşıyan her A/B ve her MÜHÜR karnesi `agent_runs=3` ile koşar; fallback
    oranı tekrar-dağılımıyla birlikte raporlanır. Varsayılan 1 → eski davranış (dry-run/smoke)."""
    db, cfg, model, app = build_eval_app(agent_model)
    try:
        with db.connection() as conn:
            records = repo.list_golden_records(conn, version)
        if not records:
            raise RuntimeError(f"'{version}' golden set DB'de yok — önce yükleyin.")

        answerable = [r for r in records if r["answerable"]]
        unanswerable = [r for r in records if not r["answerable"]]
        if limit is not None:
            answerable = answerable[:limit]
            if not all_unanswerable:
                # dry-run: ilk `limit` answerable + en çok 2 unanswerable
                unanswerable = unanswerable[: min(2, limit)]
            # M-7: gate smoke'unda `all_unanswerable=True` → 5'in TAMAMI koşulur.
            # NEDEN: honesty smoke'ta HARD'dır (exit 1 taşır) ve 2 soruyla ölçülürse
            # eşik (0.76) fiilen 2/2 şart koşar; tek soruluk sapma gate'i kırmızıya
            # çevirir. Ölçüldü: ardışık iki smoke koşusu 1.000 ve 0.500 verdi (aynı kod,
            # aynı korpus). honesty JUDGE KULLANMAZ (kural tabanlı: 'bulunamadı' dedi mi
            # + kaynak uydurmadı mı) → 3 ek soru yalnızca 3 agent çağrısı, judge token'ı
            # HARCAMAZ. Böylece karnedeki 5-soruluk zeminle AYNI temelde ölçülür.

        judge = Judge(model=judge_model or LiteLLMSettings().model
                      or cfg.group("eval").judge_model or DEFAULT_JUDGE_MODEL)
        # Gömme modeli DB otoritesinden (M-4) — retrieval/service.py:211 ile aynı idiom.
        embedder = JudgeEmbedder(model=cfg.group("embedding").model)
        t0 = time.perf_counter()
        ck = _load_ck(out_path)
        queue = answerable + unanswerable
        by_id = {r["id"]: r for r in queue}
        paused = None  # ("answer"|"score", id) — günlük kap durağı

        # 1) Dataset üretimi (resumable) — checkpoint'te olan sorular atlanır
        _LOG.info("eval_dataset_start", answerable=len(answerable), unanswerable=len(unanswerable),
                  judge=judge.label, agent_model=model, resumed=len(ck["answers"]))
        # agent_runs>1 → her soru k kez koşar; anahtar "id#tekrar" olur (k=1'de anahtar
        # DEĞİŞMEZ: eski checkpoint'ler ve smoke/dry-run yolu bozulmaz).
        plan = [(rec, i, rec["id"] if agent_runs == 1 else f"{rec['id']}#{i}")
                for rec in queue for i in range(agent_runs)]
        for idx, (rec, tekrar, key) in enumerate(plan):
            # ATLAMA ÖLÇÜTÜ = YANITLANMIŞ OLMAK, "denenmiş olmak" DEĞİL. Eskiden hata kaydı
            # da atlatıyordu (`or key in ck["errors"]`) — yani bir satır bir kez hataya
            # düştüğünde AYNI checkpoint'le bir daha ASLA denenmiyordu. Karne (bkz. `eksik`)
            # o satırı doğru biçimde "missing" ilan ediyor ama koşum onu kuyruğa geri
            # koymuyordu: kısmi bir karne tekrar koşularak KAPATILAMIYORDU. Ölçüldü
            # (2026-08-14, v1-bddk M-17): kaçış düzeltmesi kurulduktan sonra koşum
            # `elapsed_sec: 0.0`, sıfır `llm_call_start` ile bitti ve u12'nin üç satırı
            # yine "missing" kaldı — tek LLM çağrısı bile yapılmadı.
            # Hata KALICI bir hüküm değil, o denemenin sonucudur; kalıcılığı yeniden
            # koşum kanıtlar, checkpoint varsayamaz.
            if key in ck["answers"]:
                continue
            yeniden = key in ck["errors"]
            try:
                row = run_question(app, rec, ctx_cap=int(cfg.group("eval").ctx_cap))
                row["rec_id"], row["repeat"] = rec["id"], tekrar
                ck["answers"][key] = row
                ck["errors"].pop(key, None)   # başarı eski hatayı geçersizler; karne bayat hata basmaz
                _save_ck(out_path, ck)
                _LOG.info("eval_answered", id=key, iterations=row["iterations"],
                          confidence=row["confidence"], answerable=rec["answerable"],
                          retry=yeniden)
            except Exception as exc:
                if _rate_limited(exc):
                    paused = ("answer", key)
                    _LOG.warning("eval_paused_ratelimit", phase="answer", id=key)
                    break
                ck["errors"][key] = str(exc)[:200]
                _save_ck(out_path, ck)
                _LOG.warning("eval_answer_failed", id=key, error=str(exc)[:160], retry=yeniden)
            if question_delay and idx < len(plan) - 1:
                time.sleep(question_delay)  # TPM yumuşatma (gateway backoff'a ek throttle)

        # 2) RAGAS-tarzı skorlama (answerable, `runs` koşu medyanı; resumable)
        if paused is None:
            for rid, row in ck["answers"].items():
                if not row.get("answerable") or rid in ck["scores"]:
                    continue
                try:
                    ck["scores"][rid] = _score_answerable(judge, embedder, row, runs)
                    ck["errors"].pop(rid, None)   # skor kolu zaten yeniden deniyordu; hata kaydı bayatlamasın
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
             "fallback": is_fallback(ck["answers"][rid]),
             "rec_id": ck["answers"][rid].get("rec_id", rid),
             "repeat": ck["answers"][rid].get("repeat", 0),
             **ck["scores"][rid]}
            for rid in ck["scores"] if rid in ck["answers"]
        ]
        honesty_rows = [_honesty(r) for r in unans_rows]
        # Payda İKİ TANE: ölçülen satır (`total`) ve ölçülmesi GEREKEN satır (`expected`).
        # İkisi ayrılmazsa kayıp satır oranı yukarı çeker — "15/15" ile "15/18" arasındaki
        # fark davranış değil, eksik ölçümdür ve karnede görünmelidir.
        unans_plan = [key for rec, _, key in plan if not rec["answerable"]]
        honest_pass = sum(1 for h in honesty_rows if h["honest"])
        strict_pass = sum(1 for h in honesty_rows if h["honest_strict"])   # eski D0 — Δ için
        agg = _aggregate(scored)
        if agent_runs > 1:
            agg["fallback"]["by_repeat"] = _fallback_by_repeat(scored, agent_runs)
            agg["fallback"]["kararsiz_sorular"] = _kararsiz(scored)
        iters = _iteration_stats(ans_rows + unans_rows)
        targets = {
            k: {"target": v, "value": agg["overall"][k], "pass": agg["overall"][k] >= v}
            for k, v in _TARGETS.items()
        }
        # EKSİK SATIR = ölçüm zemini kaybı, ilerleme değil. Eskiden hatalar "yapıldı"
        # sayılıyordu (done = answers + errors) ve karne 3 satırını KAYBETMİŞ olmasına
        # rağmen status=complete + "15/15" basıyordu: payda sessizce 18'den 15'e
        # düşüyor, oran bundan HABERSİZ okunuyordu. Ölçüldü (2026-08-13, v1-bddk):
        # u12'nin üç tekrarı da 120 s LiteLLM timeout'una düştü, karne yine "complete".
        # Hata kaydı bir satırı ÖLÇÜLMEMİŞ yapar; ölçülmemiş satır tam karne olamaz.
        eksik = [key for _, _, key in plan if key not in ck["answers"]]
        complete = paused is None and not eksik and len(scored) == len(ans_rows)
        durum = "complete" if complete else ("paused" if paused else "partial")
        return {
            "judge": judge.label, "judge_model": judge.model, "agent_model": model,
            # M-9: sıcaklık MÜHÜR ZEMİNİDİR — zemini belirleyen parametre mühürde görünür,
            # gate model-zemini kontrolü de bunu izler (sapma → exit 2).
            "agent_temperature": float(cfg.group("agent").temperature),
            "golden": version, "mode": "report", "dev_mode": True, "runs": runs,
            "agent_runs": agent_runs, "limit": limit,
            "status": durum,
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
                        "score": f"{honest_pass}/{len(honesty_rows)}",
                        "expected": len(unans_plan),
                        "missing": [k for k in unans_plan if k not in ck["answers"]],
                        # M-17 Δ: eski D0 (strict) vs yeni D4 — "tanımsal kayma, davranış değil"
                        "strict_pass": strict_pass, "definition": "D4",
                        "strict_score": f"{strict_pass}/{len(honesty_rows)}",
                        "per_question": honesty_rows},
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
    elif st == "partial":
        # HATA ≠ İLERLEME: satır ölçülemedi. Sayılar geçerli ama DAR paydadan.
        status_line += (f" (satır kaybı — {len(result['dataset']['errors'])} hata) · "
                        f"ilerleme: {pr.get('answered')}/{pr.get('queue')} yanıt "
                        f"→ `--out` ile tekrar koşulursa kalanlar tamamlanır")
    L.append(status_line)
    L.append(f"golden={result['golden']} · agent={result['agent_model']} · judge={result['judge_model']} "
             f"· temp={result.get('agent_temperature', '?')} "
             f"· runs={result['runs']} (medyan)"
             + (f" · agent_runs={result['agent_runs']}" if result.get('agent_runs', 1) > 1 else "")
             + f" · süre={result['elapsed_sec']}s"
             + (f" · DRY-RUN limit={result['limit']}" if result.get("limit") else ""))
    ds = result["dataset"]
    L.append(f"dataset: {ds['answerable_run']} answerable + {ds['unanswerable_run']} unanswerable"
             + (f" · {len(ds['errors'])} HATA" if ds["errors"] else ""))

    ov = result["ragas"]["overall"]
    cols = list(_METRICS)
    header = "kategori".ljust(20) + "n   " + "  ".join(c[:13].ljust(13) for c in cols)

    # M-9: FALLBACK ORANI önce gelir — metrikler onsuz okunamaz (fallback tanımı gereği
    # "sadık" olduğu için faithfulness/context_precision'ı YAPISAL olarak şişirir).
    fb = result["ragas"].get("fallback") or {}
    if fb:
        L.append(f"\n-- FALLBACK ORANI: {fb['count']}/{fb['total']} "
                 f"(%{100 * fb['rate']:.0f}) — cevaplanabilir soruya 'bulunamadı' --")
        if fb.get("by_repeat"):
            L.append("   tekrar dağılımı: " + " · ".join(
                f"#{i}: {d['count']}/{d['total']} (%{100 * d['rate']:.0f})"
                for i, d in fb["by_repeat"].items()))
        if fb.get("kararsiz_sorular"):
            L.append(f"   KARARSIZ (tekrarlar arası yön değiştiren) {len(fb['kararsiz_sorular'])} soru: "
                     + ", ".join(fb["kararsiz_sorular"]))
        if fb["ids"]:
            L.append("   " + ", ".join(fb["ids"]))

    ao = (result["ragas"].get("answered_only") or {}).get("overall")
    if ao:
        L.append("\n-- RAGAS (ANSWERED-ONLY — fallback'ler HARİÇ; tek dürüst kalite özeti) --")
        L.append(header)
        L.append("-" * len(header))
        L.append("GENEL".ljust(20) + f"{ao['n']:<4}" + "  ".join(f"{ao[c]:<13.3f}" for c in cols))
        for cat, m in result["ragas"]["answered_only"]["by_category"].items():
            L.append(cat.ljust(20) + f"{m['n']:<4}" + "  ".join(f"{m[c]:<13.3f}" for c in cols))

    L.append("\n-- RAGAS (TÜM answerable — fallback'ler DÂHİL; şişkin, tek başına okunmaz) --")
    L.append(header)
    L.append("-" * len(header))
    L.append("GENEL".ljust(20) + f"{ov['n']:<4}" + "  ".join(f"{ov[c]:<13.3f}" for c in cols))
    for cat, m in result["ragas"]["by_category"].items():
        L.append(cat.ljust(20) + f"{m['n']:<4}" + "  ".join(f"{m[c]:<13.3f}" for c in cols))

    L.append("\n-- Dev-hedef kıyas (gösterge; geçmese de sayı geçerli) --")
    for k, t in result["targets"].items():
        L.append(f"  {k:20s} {t['value']:.3f}  (hedef ≥{t['target']}) → {'GEÇTİ' if t['pass'] else 'ALTINDA'}")

    h = result["honesty"]
    _delta = h.get("strict_score")
    _dsuffix = (f"  (Δ tanım: eski-D0 {_delta} → yeni-{h.get('definition','D4')} {h['score']}; "
                f"kayma DAVRANIŞTAN DEĞİL, ÖLÇÜTTEN)") if _delta and _delta != h["score"] else ""
    L.append(f"\n-- Unanswerable dürüstlük (RAGAS dışı, deterministik): {h['score']}{_dsuffix} --")
    if h.get("missing"):
        L.append(f"   EKSİK {len(h['missing'])}/{h['expected']} satır ÖLÇÜLEMEDİ "
                 f"({', '.join(h['missing'])}) — oran {h['total']} satırlık DAR paydadan; "
                 f"tam karne değildir")
    for r in h["per_question"]:
        flag = "✓" if r["honest"] else "✗"
        _cov = r.get("coverage")
        _covs = f"{_cov:.2f}" if isinstance(_cov, (int, float)) and not isinstance(_cov, bool) else "—"
        L.append(f"  {flag} {r['id']}  [{r.get('kind','')}] conf={r['confidence']} kaynak={r['n_sources']} "
                 f"coverage={_covs} declined={r['declined']} uydurma={r['fabricated_sources']} "
                 f"iter={r['iterations']}")

    it = result["iterations"]
    L.append(f"\n-- İterasyon dağılımı (max_iterations kararı verisi) — ort={it['mean']} max={it['max']} --")
    L.append("  tur→adet: " + ", ".join(f"{k}:{v}" for k, v in it["distribution"].items()))
    L.append("  kategori ort: " + ", ".join(f"{c}={v}" for c, v in it["by_category"].items()))
    if result["dataset"]["errors"]:
        L.append("\n-- HATALAR --")
        for e in result["dataset"]["errors"]:
            L.append(f"  {e['id']}: {e['error']}")
    return "\n".join(L)

"""`python -m ragintel.eval load|retrieval` (İP-2.1a loader + İP-2.4 benchmark)."""

from __future__ import annotations

import argparse
import json
import sys


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _open_db():
    from ..config.settings import DbSettings
    from ..database import Database
    return Database(DbSettings()).open()


def _cmd_load(args) -> int:
    from .loader import load_golden_set
    db = _open_db()
    try:
        res = load_golden_set(db, args.path, set_version=args.version)
        print(json.dumps(res.__dict__, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


def _cmd_retrieval(args) -> int:
    from ..config.loader import load_config
    from ..database.config_store import make_db_reader
    from ..retrieval import RetrievalService
    from . import repository as repo
    from .retrieval_benchmark import (
        ServiceRetriever, corpus_fingerprint, from_db_rows, from_golden_records,
        map_gold_chunks, run_benchmark, format_summary,
    )

    db = _open_db()
    try:
        # Kaynak: --from-file (onay öncesi JSONL) veya DB set_version.
        if args.from_file:
            from .models import load_golden_jsonl
            records = from_golden_records(load_golden_jsonl(args.from_file))
            src = f"file:{args.from_file}"
        else:
            with db.connection() as conn:
                rows = repo.list_golden_records(conn, args.golden)
            if not rows:
                print(f"HATA: '{args.golden}' set'inde kayıt yok (yüklendi mi?). "
                      f"--from-file ile JSONL'den de koşabilirsiniz.", file=sys.stderr)
                return 2
            records = from_db_rows(rows)
            src = f"db:{args.golden}"

        with db.connection() as conn:
            mapping = map_gold_chunks(conn, records)
            # Paydayı ilan et: hangi korpusta ölçtüğümüz karnenin üstünde yazsın.
            corpus = corpus_fingerprint(conn, [r.doc_scope for r in records])

        cfg = load_config(db_reader=make_db_reader(db))
        service = RetrievalService(db=db, config=cfg)
        retriever = ServiceRetriever(service, args.variant)
        result = run_benchmark(records, mapping, retriever, top_k=args.top_k)
        result["source"] = src
        result["corpus"] = corpus

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            print(format_summary(result))
        return 0
    finally:
        db.close()


def _cmd_run(args) -> int:
    from .harness import evaluate, format_report

    result = evaluate(version=args.golden, limit=args.limit, runs=args.runs,
                      agent_model=args.agent_model, judge_model=args.judge_model,
                      question_delay=args.question_delay, out_path=args.out,
                      agent_runs=args.agent_runs,
                      all_unanswerable=args.all_unanswerable)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_report(result))
    # paused (günlük kap) → 10: scheduler tekrar koşup devam etsin; complete → 0
    return 0 if result.get("status", "complete") == "complete" else 10


def _cmd_gate(args) -> int:
    """CI eval gate: golden'ı koşar, eşiklerle kıyaslar. pass=0 / fail=1 / altyapı=2.

    ÖN-KOŞULLAR (judge'a GİTMEDEN, ucuz ve deterministik — ikisi de exit 2):
      1. evidence çözünürlüğü (`gates.evidence_precondition`) — korpus/parse kaydı mı?
      2. model zemini (`gates.model_ground_precondition`) — karnenin agent/judge'ı mı?
    İkisi de KALİTE regresyonu değil, ÖLÇÜM ZEMİNİNİN kaymasıdır → exit 2, exit 1 değil.

    SİNYAL-VARYANS EŞLEMESİ (M-7, smoke):
      HARD (exit 1)     : honesty_ratio — 5 soruda deterministik kontrol.
      ADVISORY (exit 0) : faithfulness / context_precision — n=5 + tek koşum judge
                          gürültüsü hard-fail taşıyamaz (kurt-çocuk etkisi korumanın
                          kendisini öldürür). RAPORLANIR, susturulmaz.
    Otoriter hard karar: NIGHTLY TAM koşu (36, runs=3) — orada hepsi HARD.
    """
    from ..config.loader import load_config
    from ..config.settings import DbSettings
    from ..database import Database
    from ..database.config_store import make_db_reader
    from .gates import (effective_models, evidence_precondition, format_gate, gate_decision,
                    model_ground_precondition, thresholds_from_config)
    from .harness import evaluate

    try:
        db = Database(DbSettings()).open()
        try:
            cfg = load_config(db_reader=make_db_reader(db))
            with db.connection() as conn:
                pre = evidence_precondition(conn, args.golden)
            # M-7: model-zemini ön-koşulu — gate KARNENİN modelleriyle mi koşuyor?
            # (evidence ile aynı sınıf: kalite değil, ÖLÇÜM ZEMİNİ kontrolü)
            if pre is None:
                pre = model_ground_precondition(
                    cfg, agent_model=args.agent_model, judge_model=args.judge_model,
                    allow_drift=args.allow_model_drift)
            models = effective_models(cfg, agent_model=args.agent_model,
                                      judge_model=args.judge_model)
        finally:
            db.close()
    except Exception as exc:  # DB/ön-koşul kurulum hatası → altyapı (exit 2)
        print(f"=== EVAL GATE — ERROR ⚠ (altyapı, exit 2) ===\nön-koşul kurulamadı: {str(exc)[:200]}")
        return 2

    if pre is not None:
        # Ön-koşul düştü: judge'a HİÇ gidilmeden burada dur (token harcanmaz).
        if args.json:
            print(json.dumps({"code": pre.code, "reason": pre.reason, "checks": [],
                              "thresholds": None, "models": models,
                              "phase": "precondition"},
                             ensure_ascii=False, indent=2))
        else:
            print(f"=== EVAL GATE — ERROR ⚠ (altyapı: ön-koşul, exit {pre.code}) ===\n"
                  f"efektif modeller: agent={models['agent']} · judge={models['judge']} · "
                  f"iterative_scan={models['iterative_scan']}\n{pre.reason}")
        return pre.code

    try:
        thr = thresholds_from_config(cfg)
        result = evaluate(version=args.golden, limit=(5 if args.smoke else None), runs=args.runs,
                          agent_model=args.agent_model, judge_model=args.judge_model,
                          # honesty smoke'ta HARD → karneyle AYNI 5-soruluk zeminde ölçülmeli
                          # (judge kullanmaz; 3 ek agent çağrısı, token yakmaz).
                          all_unanswerable=args.smoke)
    except Exception as exc:  # harness kurulum/koşum hatası → altyapı (exit 2)
        print(f"=== EVAL GATE — ERROR ⚠ (altyapı, exit 2) ===\nharness çalıştırılamadı: {str(exc)[:200]}")
        return 2

    result["models"] = models          # M-7: ölçüm zemini HER koşumda raporlanır
    outcome = gate_decision(result, thr, smoke=args.smoke)
    if args.json:
        print(json.dumps({"code": outcome.code, "reason": outcome.reason,
                          "checks": [{"name": n, "value": v, "threshold": t, "ok": ok,
                                      "severity": "hard" if hard else "advisory"}
                                     for n, v, t, ok, hard in outcome.checks],
                          "thresholds": vars(thr),
                          "models": models},   # ölçüm zemini: sayılar hangi agent/judge/ANN ile üretildi
                         ensure_ascii=False, indent=2))
    else:
        print(format_gate(outcome, result, thr, smoke=args.smoke))
    return outcome.code


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    parser = argparse.ArgumentParser(prog="python -m ragintel.eval")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ld = sub.add_parser("load", help="Golden set JSONL'i DB'ye yükle")
    ld.add_argument("path")
    ld.add_argument("--version", required=True)

    rt = sub.add_parser("retrieval", help="Retrieval benchmark (İP-2.4)")
    rt.add_argument("--golden", default="v0", help="DB set_version (varsayılan v0)")
    rt.add_argument("--from-file", default=None, help="DB yerine JSONL dosyasından koş (onay öncesi)")
    rt.add_argument("--variant", choices=("vector", "hybrid"), required=True)
    rt.add_argument("--top-k", type=int, default=None, help="Retrieval derinliği (varsayılan max(k)=20)")
    rt.add_argument("--json", action="store_true", help="Tam JSON sonuç")

    rn = sub.add_parser("run", help="Uçtan uca RAGAS-tarzı eval (İP-2.3, DEV-MODE judge=groq)")
    rn.add_argument("--golden", default="v0", help="DB set_version (varsayılan v0)")
    rn.add_argument("--mode", default="report", choices=("report",), help="report (fail etmez, gösterge)")
    rn.add_argument("--limit", type=int, default=None, help="Dry-run: ilk N answerable (+2 unanswerable)")
    rn.add_argument("--all-unanswerable", action="store_true",
                    help="`--limit` unanswerable kolunu KIRPMASIN. Varsayılan yol kolu "
                         "min(2, limit) ile keser; M-17 dürüstlük ölçümü kolun TAMAMINI "
                         "ister, yoksa 6 kayıtlık kol sessizce 2'ye iner ve oran yanlış "
                         "paydadan çıkar. `--limit 0 --all-unanswerable` = yalnız dürüstlük "
                         "kolu (judge çağrısı yok).")
    rn.add_argument("--runs", type=int, default=3, help="Judge medyanı için koşu sayısı (varsayılan 3)")
    rn.add_argument("--agent-runs", type=int, default=1,
                    help="Soru başına AGENT tekrar sayısı. MÜHÜR KARNESİ ve kalite iddiası "
                         "taşıyan her A/B için 3 ŞARTTIR: tek agent koşumu bu sistemde "
                         "gürültüdür (ölçülen taban ±6/31). Fallback oranı tekrar-dağılımıyla verilir.")
    rn.add_argument("--agent-model", default=None, help="Ajan LLM (varsayılan qwen/qwen3-32b — tool-calling)")
    rn.add_argument("--judge-model", default=None, help="Judge LLM (varsayılan llama-3.3-70b-versatile)")
    rn.add_argument("--question-delay", type=float, default=1.0, help="Sorular arası throttle sn (TPM)")
    rn.add_argument("--out", default=None, help="Checkpoint dosyası (gece koşusu resume; kap'a takılınca devam)")
    rn.add_argument("--json", action="store_true", help="Tam JSON sonuç")

    gt = sub.add_parser("gate", help="CI eval gate (İP-8): eşiklerle kıyas, pass=0/fail=1/altyapı=2")
    gt.add_argument("--golden", default="v0", help="DB set_version (varsayılan v0)")
    gt.add_argument("--smoke", action="store_true", help="Hızlı mod: 5 soru (her push); tam 36 nightly")
    gt.add_argument("--runs", type=int, default=1, help="Judge medyanı koşu sayısı (gate'te vars. 1)")
    gt.add_argument("--agent-model", default=None)
    gt.add_argument("--judge-model", default=None)
    gt.add_argument("--json", action="store_true", help="Tam JSON sonuç")
    gt.add_argument("--allow-model-drift", action="store_true",
                    help="Karnenin modelinden BİLİNÇLİ sapmaya izin ver (sayılar mühürle "
                         "kıyaslanamaz; yalnızca keşif amaçlı)")

    args = parser.parse_args(argv)
    if args.cmd == "load":
        return _cmd_load(args)
    if args.cmd == "retrieval":
        return _cmd_retrieval(args)
    if args.cmd == "run":
        return _cmd_run(args)
    if args.cmd == "gate":
        return _cmd_gate(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())

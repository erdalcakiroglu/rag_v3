"""backlog #8 ÖN-VERİ — entailment-ON'un DEĞERİNİ ölç (kod öncesi, salt-ölçüm).

BAĞLAM: Live c5f4e45'te honesty D4=15/15; gs-034 coverage=1.0 ×3. Grounding-gap
(lever A) BAYAT çıktı — yapacak prompt düzeltmesi yok. Geriye TEK soru kaldı:
6 `border_declined_cited` satırı (gs-034×3, gs-036×3) reddediyor AMA citation ekliyor;
`coverage=1.0` SÖZCÜK-ÖRTÜŞMESİ, anlamsal destek DEĞİL. Entailment ON bu satırlarda
GERÇEK bir cited-fabrication yakalar mı, yoksa yalnız latency mi ekler?

BU BETİK CONFIG'İ DEĞİŞTİRMEZ — `validate_entailment` KAPALI kalır. Yalnız "açık olsaydı
ne derdi"yi ölçer (validate.py:_run_entailment ile BİREBİR aynı çağrı: draft_answer +
citations → check_entailment). Böylece guardrail'i açmadan değerini görürüz.

VERİ EGEMENLİĞİ: judge LOKAL olmalı (H200 Ollama). api_base lokal değilse betik ABORT eder
— belge içeriği buluta GİTMEZ. Etiket + model + api_base basılır (kanıt).

KULLANIM: docker exec -i ragintel-api env GOLDEN=v0.1 REPEATS=3 python - < scripts/gs034_entailment_probe.py
"""
from __future__ import annotations

import os
import sys

from ragintel.agents.graph import run_agent
from ragintel.config.settings import LiteLLMSettings
from ragintel.eval import harness
from ragintel.guardrails import EntailmentJudge, check_entailment

GOLDEN = os.environ.get("GOLDEN", "v0.1")
MODEL = os.environ.get("POC_MODEL") or os.environ.get("RAGINTEL_LLM_MODEL") or "qwen3.5:35b"
REPEATS = int(os.environ.get("REPEATS", "3"))


def _short(s, n=300):
    s = " ".join((str(s) or "").split())
    return s if len(s) <= n else s[:n] + "…"


def _is_local(api_base: str) -> bool:
    b = (api_base or "").lower()
    return "11434" in b or "localhost" in b or "127.0.0.1" in b


# --- VERİ EGEMENLİĞİ ÖN-KAPISI: judge lokal değilse hiç başlama ---
_settings = LiteLLMSettings()
_judge_model = (os.environ.get("ENT_MODEL") or _settings.model or MODEL)
if not _is_local(_settings.api_base):
    print(f"ABORT: judge api_base LOKAL DEĞİL ({_settings.api_base!r}). Belge içeriği buluta "
          f"gitmesin diye prob durduruldu. H200'de (Ollama localhost:11434) koş.")
    sys.exit(2)

judge = EntailmentJudge(model=_judge_model, settings=_settings)
print(f"# backlog #8 entailment-ON DEĞER PROBU — golden={GOLDEN} repeat={REPEATS}")
print(f"# judge: model={judge.model} label={judge.label} api_base={_settings.api_base} (LOKAL ✓)")
print("# NOT: config değişmez; validate_entailment KAPALI kalır — bu yalnız simülasyon.\n")

db, cfg, model, app = harness.build_eval_app(MODEL)
agent_cfg = cfg.group("agent")
print(f"# canlı entailment ayarı (config, DOKUNULMADI): "
      f"validate_entailment={getattr(agent_cfg, 'validate_entailment', '?')} "
      f"validate_entailment_model={getattr(agent_cfg, 'validate_entailment_model', '?')!r}\n")

flagged_lines = 0   # entailment WOULD-FAIL eden satır sayısı
cited_lines = 0     # citation'ı olan (entailment koşan) satır sayısı
try:
    with db.connection() as conn:
        records = [r for r in harness.repo.list_golden_records(conn, GOLDEN)
                   if not r["answerable"]]

    for rec in records:
        print(f"### {rec['id']}  SORU: {_short(rec['question'], 140)}")
        for rep in range(1, REPEATS + 1):
            initial = {"query": rec["question"],
                       "user_ctx": {"user_id": "eval", "tenant_id": "eval", "roles": ["eval"],
                                    "allowed_doc_scopes": [rec["doc_scope"]]},
                       "session_id": f"gs034ent-{rec['id']}-{rep}", "retrieved": []}
            out = run_agent(app, initial)
            cits = out.get("citations") or []
            draft = str(out.get("draft_answer") or "")
            final = out.get("final_response") or {}
            conf = final.get("confidence")

            if not cits:
                print(f"  -- rep {rep}  conf={conf} kaynak=0 → entailment N/A (citation yok, saf ret)")
                continue

            cited_lines += 1
            ent = check_entailment(question=rec["question"], answer=draft, citations=cits, judge=judge)
            would_fail = bool(ent.issues) and not ent.skipped
            flagged_lines += int(would_fail)
            verdict = ("SKIPPED (judge erişilemedi)" if ent.skipped
                       else "WOULD-FAIL ⚠" if would_fail else "temiz ✓")
            print(f"  -- rep {rep}  conf={conf} kaynak={len(cits)}  ENTAILMENT: {verdict}")
            if ent.issues:
                print(f"       issues={ent.issues}")
            for d in ent.detail:
                mark = "" if (d["supported"] and not d["hypothetical_as_fact"]) else "  <== SORUN"
                print(f"       chunk[{d['chunk_id']}] supported={d['supported']} "
                      f"hypothetical_as_fact={d['hypothetical_as_fact']}{mark}")
            print(f"       DRAFT: {_short(draft, 260)}")
            for c in cits:
                print(f"       cite[{c.get('chunk_id')}] iddia={_short(c.get('claim'), 110)} "
                      f"| alıntı={_short(c.get('quote'), 90)}")
        print()

    print("############ SONUÇ ############")
    print(f"  citation'lı satır (entailment koştu): {cited_lines}")
    print(f"  entailment WOULD-FAIL eden satır:     {flagged_lines}")
    print("\n############ OKUMA ############")
    if flagged_lines == 0:
        print("  entailment HİÇBİR şey flag'lemedi → D4=15/15 anlamsal olarak da dürüst;")
        print("  entailment ON honesty KAZANDIRMAZ, yalnız +1 LLM/soru latency ekler → lever B RAFA,")
        print("  backlog #8 aksiyon YOK olarak KAPANIR (zaten dürüst, ölçüldü).")
    else:
        print("  entailment gerçek cited-fabrication YAKALADI → coverage sözcük-örtüşmesi bunu")
        print("  maskeliyordu. lever B hak edilmiş bir k=3 A/B değerlendirmeyi (honesty↑ vs latency)")
        print("  kazanır. Flag'lenen satırların DRAFT/iddia metnini elle doğrula (judge yanılabilir).")
finally:
    try:
        db.close()
    except Exception:
        pass

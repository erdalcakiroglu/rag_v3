"""M-16 ÖN-VERİ — 6 fallback'in KARAR ANATOMİSİ (kod öncesi, ZORUNLU).

M-9.1: 6 answerable fallback verdi; teşhis kanıtın bağlamda MEVCUT olduğunu gösterdi
(agent aşırı-temkinli red). Bu betik "NEDEN reddetti"yi kategorize eder:
  (a) prompt-driven empty-citation refuse — model BOŞ citations ile submit_answer çağırdı
      (v2 REDDETME maddesi: "ilgili ama yetersiz/dolaylı bilgi dahil → boş citations").
  (b) coverage/grounding eşiği kesti — model DOLU citations verdi ama validate low_coverage/
      unsupported_claim ile eledi, retry sonrası fallback.
  (c) bütçe/döngü — iterasyon veya deadline bitti (sert durdurucu).

run_agent tüm `out` state'ini döndürür (fallback node draft'ı EZMEZ) → tek invoke'tan
validation.issues + coverage + citations + draft + retry_count okunur. Judge YOK.

ÇIKTI ayrıca AKTİF PROMPT SÜRÜMÜNÜ basar (M-9.1 hangi prompt'la koştu — v2 sıkı-decline mı,
v3 kısmi-cevap mı?). Bu, fix'in "v3'ü aktive et" mi yoksa "v4 yaz" mı olduğunu belirler.

KULLANIM: docker exec -i ragintel-api env GOLDEN=v0.1 python - < scripts/m91_fallback_anatomi.py
"""
from __future__ import annotations

import os

from ragintel.agents.graph import run_agent
from ragintel.agents.prompts import PROMPT_VERSIONS, load_system_prompt
from ragintel.eval import harness

GOLDEN = os.environ.get("GOLDEN", "v0.1")
MODEL = os.environ.get("POC_MODEL") or os.environ.get("RAGINTEL_LLM_MODEL") or "qwen3.5:35b"
TARGETS = ["gs-v0-002", "gs-v0-012", "gs-v0-019", "gs-v0-022", "gs-v0-023", "gs-v0-026"]


def _short(s, n=220):
    s = " ".join((str(s) or "").split())
    return s if len(s) <= n else s[:n] + "…"


def _prompt_version(body: str) -> str:
    for tag, ver_body in PROMPT_VERSIONS.items():
        if body.strip() == ver_body.strip():
            return tag
    return "ÖZEL/DB-override (koddaki v1/v2/v3'e birebir uymuyor)"


db, cfg, model, app = harness.build_eval_app(MODEL)
try:
    agent_cfg = cfg.group("agent")
    cov_thr = float(agent_cfg.validation_coverage_threshold)
    ent_on = bool(getattr(agent_cfg, "validate_entailment", False))
    active_body = load_system_prompt(cfg)
    active_tag = _prompt_version(active_body)
    try:
        db_active = str(getattr(cfg.group("prompts"), "agent_system_active", "") or "")
    except Exception:
        db_active = "(prompts grubu yok)"

    print(f"# M-16 ön-veri — fallback KARAR ANATOMİSİ — golden={GOLDEN} agent={model}")
    print(f"# AKTİF PROMPT: kod-eşleşme={active_tag}  DB.agent_system_active='{db_active}'")
    print(f"# coverage_threshold={cov_thr}  validate_entailment(v2)={ent_on}")
    print(f"# prompt ilk satır: {_short(active_body.splitlines()[0], 90)}\n")

    with db.connection() as conn:
        by_id = {r["id"]: r for r in harness.repo.list_golden_records(conn, GOLDEN)}

    for gid in TARGETS:
        rec = by_id.get(gid)
        if rec is None:
            print(f"### {gid} — KAYIT YOK\n"); continue
        initial = {
            "query": rec["question"],
            "user_ctx": {"user_id": "eval", "tenant_id": "eval", "roles": ["eval"],
                         "allowed_doc_scopes": [rec["doc_scope"]]},
            "session_id": f"anatomi-{gid}", "retrieved": [],
        }
        out = run_agent(app, initial)
        final = out.get("final_response") or {}
        val = out.get("validation") or {}
        cits = out.get("citations") or []
        draft = out.get("draft_answer")
        budget = out.get("budget") or {}
        iters = int(budget.get("iteration", 0))
        max_it = int(budget.get("max_iterations", 0))
        retry = int(out.get("retry_count", 0))
        declined = str(final.get("answer", "")).startswith("Cevap bulunamadı")

        # Kategori çıkarımı
        if iters >= max_it and not val.get("passed"):
            kok = "(c) BÜTÇE/döngü — iterasyon tükendi"
        elif not cits and declined:
            kok = "(a) PROMPT — model BOŞ citations ile reddetti"
        elif cits and not val.get("passed"):
            kok = "(b) COVERAGE/grounding eşiği eledi"
        elif not cits and not declined:
            kok = "(a?) citation'sız taslak (content_no_tool)"
        else:
            kok = "(?) sınıflanamadı"

        print(f"### {gid}  [{rec['category']}]  → {kok}")
        print(f"    SORU     : {_short(rec['question'], 140)}")
        print(f"    MODEL DRAFT (fallback ezmeden): {_short(draft, 240)}")
        print(f"    citations={len(cits)}  validate.passed={val.get('passed')}  "
              f"coverage={val.get('coverage')}  (eşik={cov_thr})")
        print(f"    issues   : {val.get('issues')}")
        print(f"    iters={iters}/{max_it}  retry_count={retry}  final.confidence={final.get('confidence')}  "
              f"declined={declined}\n")

    print("############ ANATOMİ OKUMA ############")
    print("(a) prompt baskın → düzeltme = prompt (v3 aktive / v4). (b) coverage baskın → eşik + prompt.")
    print("(c) bütçe → iterasyon/prompt verimliliği. AKTİF PROMPT v2 ise: v3 zaten bu 4 soruyu hedefliyor.")
finally:
    try:
        db.close()
    except Exception:
        pass

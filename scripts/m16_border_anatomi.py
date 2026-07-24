"""M-16 FIX-2 ÖN-VERİ — border_declined_cited iki vakanın ANATOMİSİ (kod öncesi, ZORUNLU).

FIX-1 sonrası dürüstlük 9/15; 6 hatanın TAMAMI gs-v0-034 (×3) + gs-v0-036 (×3),
ikisi de `border_declined_cited` = "bulunamadı DEDİ ama kaynak iliştirdi".

GENİŞ FIX-2 ÇÜRÜTÜLMÜŞTÜ: "bulunmamaktadır" hem meşru NEGATİF-OLGU cevaplarında
(gs-002/012/023 — dünya hakkında: "Türkiye'de karbon vergisi bulunmamaktadır")
hem gerçek REDDETMEDE (doküman hakkında: "dokümanlarda bulunmamaktadır") geçiyor.
Bu yüzden marker'a bakan her kural negatif-olguyu da keser → yasak.

HİPOTEZ (bu betik doğrulayacak/çürütecek): gerçek red cümlesi DOKÜMAN KÜMESİNE atıf
yapar (doküman/belge/kaynak/bağlam/metin/sağlanan bilgi); negatif olgu DÜNYAYA atıf
yapar. Ayırt edici buysa FIX-2 dar olabilir; değilse başka eksen aranacak.

Basılanlar: nihai answer, hangi _NOTFOUND_MARKERS eşleşti, confidence, sources
(+quote), validate coverage/issues, ve FIX-1 cümle ayrımı (iddia mı red-fragmanı mı)
+ her red cümlesinde doküman-atfı var mı.

KULLANIM: docker exec -i ragintel-api env GOLDEN=v0.1 python - < scripts/m16_border_anatomi.py
"""
from __future__ import annotations

import os

from ragintel.agents.graph import run_agent
from ragintel.eval import harness
from ragintel.eval.harness import _NOTFOUND_MARKERS
from ragintel.guardrails.grounding import _is_claim_sentence, _sentences
from ragintel.text import normalize_for_quote

GOLDEN = os.environ.get("GOLDEN", "v0.1")
MODEL = os.environ.get("POC_MODEL") or os.environ.get("RAGINTEL_LLM_MODEL") or "qwen3.5:35b"
TARGETS = ["gs-v0-034", "gs-v0-036"]

# Hipotez sınaması: red cümlesi doküman kümesine mi atıf yapıyor?
_DOC_REFS = ("doküman", "dokuman", "belge", "kaynak", "bağlam", "baglam", "metin",
             "sağlanan", "saglanan", "verilen", "sunulan", "içerik", "icerik")


def _short(s, n=400):
    s = " ".join((str(s) or "").split())
    return s if len(s) <= n else s[:n] + "…"


db, cfg, model, app = harness.build_eval_app(MODEL)
try:
    with db.connection() as conn:
        by_id = {r["id"]: r for r in harness.repo.list_golden_records(conn, GOLDEN)}
    print(f"# M-16 FIX-2 ön-veri — border anatomisi — golden={GOLDEN} agent={model}")
    print(f"# eşik: coverage={cfg.group('agent').validation_coverage_threshold} "
          f"entailment={getattr(cfg.group('agent'), 'validate_entailment', '?')}\n")

    for gid in TARGETS:
        rec = by_id.get(gid)
        if rec is None:
            print(f"### {gid} — KAYIT YOK\n"); continue
        print(f"### {gid}  (unanswerable)\n    SORU: {_short(rec['question'], 160)}")
        initial = {"query": rec["question"],
                   "user_ctx": {"user_id": "eval", "tenant_id": "eval", "roles": ["eval"],
                                "allowed_doc_scopes": [rec["doc_scope"]]},
                   "session_id": f"border-{gid}", "retrieved": []}
        out = run_agent(app, initial)
        final = out.get("final_response") or {}
        answer = str(final.get("answer") or "")
        val = out.get("validation") or {}

        hits = [m for m in _NOTFOUND_MARKERS if m in answer.lower()]
        print(f"    CEVAP: {_short(answer)}")
        print(f"    conf={final.get('confidence')} kaynak={len(final.get('sources') or [])} "
              f"iter={out.get('budget', {}).get('iteration')} retry={out.get('retry_count')}")
        print(f"    validate: passed={val.get('passed')} coverage={val.get('coverage')} "
              f"issues={val.get('issues')}")
        print(f"    EŞLEŞEN MARKER: {hits or '— (declined yalnız confidence=low ile)'}")
        for s in (final.get("sources") or []):
            print(f"      [src {s.get('n')}] chunk={s.get('chunk_id')} "
                  f"quote={_short(s.get('quote'), 110)}")

        print("    -- cümle ayrımı (FIX-1 gözüyle) --")
        for i, sent in enumerate(_sentences(answer), 1):
            claim = _is_claim_sentence(sent, normalize_for_quote(sent))
            low = sent.lower()
            docref = [d for d in _DOC_REFS if d in low]
            tag = "İDDİA" if claim else "RED-FRAGMANI"
            ref = f" doküman-atfı={docref}" if docref else " doküman-atfı=YOK"
            print(f"      S{i} [{tag}]{'' if claim else ref}  {_short(sent, 130)}")
        print()

    print("############ FIX-2 OKUMA ############")
    print("(1) Red cümlesinde doküman-atfı VAR, negatif-olgu cevaplarında YOK →")
    print("    dar FIX-2 mümkün: 'doküman-atıflı yokluk ifadesi ⇒ sources=[]'.")
    print("(2) Doküman-atfı yoksa/ayırt etmiyorsa → marker ekseni ÖLÜ; yapısal eksen")
    print("    (ör. cevabın SORUYU yanıtlamaması) aranır, kör kural YAZILMAZ.")
    print("(3) gs-v0-034 entailment KAPALI olduğu için beklenen hata — FIX-2 kapsamı DEĞİL.")
finally:
    try:
        db.close()
    except Exception:
        pass

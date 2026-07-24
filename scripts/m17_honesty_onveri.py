"""M-17 ÖN-VERİ — honesty tanım adaylarının ANATOMİSİ (kod öncesi, ZORUNLU).

Bu betik ÖLÇÜTÜ DEĞİŞTİRMEZ. `_honesty()`'yi olduğu gibi bırakır; yalnız aynı satırlar
üzerinde tanım adaylarını YAN YANA oynatır ve nerede ayrıştıklarını basar.

NEDEN: M-16, honesty ekseninde kırık olanın davranış değil ÖLÇÜT olduğunu saptadı
(`fabricated = len(sources) > 0` → "reddetti ama kaynaklı bağlam verdi" ile "uydurdu ve
kaynaklandırdı" ayrımı yok). Brief M-17 §1 tanımı değiştirmeden önce 4 sorunun ön-veriyle
yanıtlanmasını şart koşuyor. Bu betik 2/3/4'ü ÜRETİM kodu üzerinde yanıtlar.

ADAYLAR (hepsi `declined` ön-şartını korur — reddetmeyen cevap hiçbir tanımda honest değil):
  D0  = MEVCUT   : fabricated = len(sources) > 0            (herhangi bir kaynak = fail)
  D2c = ADAY     : fabricated = ATIFSIZ iddia cümlesi var   (compose mobilyası hariç)
  D2c+= ADAY(gen): D2c + genişletilmiş red-fragmanı listesi ("bulunamamıştır" vb.)
  D3  = GEVŞEK   : yalnız `fabricated_confident` fail        (M-16'nın uyardığı biçim)

honesty JUDGE KULLANMAZ (kural tabanlı) → bu koşum judge maliyeti taşımaz; yalnız
5 unanswerable soru × REPEATS agent çağrısıdır.

KULLANIM: docker exec -i ragintel-api env GOLDEN=v0.1 REPEATS=3 python - < scripts/m17_honesty_onveri.py
"""
from __future__ import annotations

import os
import re

from ragintel.agents.graph import run_agent
from ragintel.eval import harness
from ragintel.eval.harness import _NOTFOUND_MARKERS
from ragintel.guardrails.grounding import _is_claim_sentence, _sentences
from ragintel.text import normalize_for_quote

GOLDEN = os.environ.get("GOLDEN", "v0.1")
MODEL = os.environ.get("POC_MODEL") or os.environ.get("RAGINTEL_LLM_MODEL") or "qwen3.5:35b"
REPEATS = int(os.environ.get("REPEATS", "3"))

CIT = re.compile(r"\[\d{1,3}\]")
# compose/fallback MOBİLYASI: modelin iddiası değil, sistemin sabit cümlesi (fallback.py:12).
# Bu cümle iddia sayılırsa TEMİZ RED bile "uydurma" olur — iddia tabanlı her tanımın kör noktası.
BOILER = re.compile(r"^(incelenen|bulunan)\s+kaynaklar\s+aşağıdadır\.?$", re.IGNORECASE)
# `_DECLINE_FRAGMENTS` (grounding.py, M-16 mührü) bu red biçimlerini KAÇIRIYOR → iddia sayıyor.
# Ölçüt katmanı kendi listesini taşımalı ki grounding'e (davranış) dokunmadan sınanabilsin.
EXTRA_FRAG = ("bulunamam", "yer verilmem", "rastlanmam", "değinilmem", "bahsedilmem",
              "belirtilmem", "geçmemekte")


def _short(s, n=400):
    s = " ".join((str(s) or "").split())
    return s if len(s) <= n else s[:n] + "…"


def _claims(answer: str, extended: bool) -> list[str]:
    """İddia taşıyan cümleler — compose mobilyası ve red fragmanları hariç."""
    out = []
    for s in _sentences(answer):
        if not _is_claim_sentence(s, normalize_for_quote(s)):
            continue
        if BOILER.match(s.strip()):
            continue
        if extended and any(f in s.lower() for f in EXTRA_FRAG):
            continue
        out.append(s)
    return out


def classify(answer: str, confidence: str, n_src: int, coverage) -> dict:
    declined = confidence == "low" or any(m in (answer or "").lower() for m in _NOTFOUND_MARKERS)
    unc = [s for s in _claims(answer, False) if not CIT.search(s)]
    unc_x = [s for s in _claims(answer, True) if not CIT.search(s)]
    try:
        cov = float(coverage)
    except (TypeError, ValueError):
        cov = -1.0
    return {
        "declined": declined, "uncited": unc, "uncited_x": unc_x, "cov": cov,
        "D0": bool(declined and n_src == 0),
        "D2c": bool(declined and not unc),
        "D2c+": bool(declined and not unc_x),
        "D3": bool(declined),
        # D4: reddetti VE bağlanmamış iddia bırakmadı. Saf ret (kaynak=0) zaten iddiasızdır;
        # kaynaklı cevapta ise validate'in KENDİ oranı ölçüt olur — satır-içi [n] biçimine
        # bağlı DEĞİL (compose.py:64: model işaret koymayabilir, atıf yine geçerlidir).
        "D4": bool(declined and (n_src == 0 or cov >= 1.0)),
    }


KEYS = ("D0", "D2c", "D2c+", "D3", "D4")

db, cfg, model, app = harness.build_eval_app(MODEL)
try:
    with db.connection() as conn:
        records = [r for r in harness.repo.list_golden_records(conn, GOLDEN)
                   if not r["answerable"]]
    agent_cfg = cfg.group("agent")
    print(f"# M-17 ön-veri — honesty tanım adayları — golden={GOLDEN} agent={model}")
    print(f"# soru={len(records)} × repeat={REPEATS}  coverage_eşiği="
          f"{agent_cfg.validation_coverage_threshold} "
          f"entailment={getattr(agent_cfg, 'validate_entailment', '?')}\n")

    tot = {k: 0 for k in KEYS}
    rows = 0
    disagree: list[str] = []       # TEYİT-1: D0→D4 flip eden satırlar
    nearmiss: list[str] = []       # TEYİT-1b: declined+kaynak>0+0.7≤cov<1.0 → D4 fail (haksız mı?)
    shortcut: list[str] = []       # TEYİT-3: declined+kaynak=0 → D4 kısa-devre honest; metin det. mi?

    for rec in records:
        print(f"### {rec['id']}\n    SORU: {_short(rec['question'], 160)}")
        for rep in range(1, REPEATS + 1):
            initial = {"query": rec["question"],
                       "user_ctx": {"user_id": "eval", "tenant_id": "eval", "roles": ["eval"],
                                    "allowed_doc_scopes": [rec["doc_scope"]]},
                       "session_id": f"m17-{rec['id']}-{rep}", "retrieved": []}
            out = run_agent(app, initial)
            final = out.get("final_response") or {}
            answer = str(final.get("answer") or "")
            val = out.get("validation") or {}
            srcs = final.get("sources") or []
            c = classify(answer, str(final.get("confidence") or ""), len(srcs), val.get("coverage"))
            rows += 1
            for k in KEYS:
                tot[k] += int(c[k])
            tag = f"{rec['id']}#{rep}"
            cov = c["cov"]
            if c["D0"] != c["D4"]:
                disagree.append(f"{tag}: D0={c['D0']} → D4={c['D4']}  "
                                f"kaynak={len(srcs)} coverage={val.get('coverage')}")
            # TEYİT-1b: yüksek ama <1.0 coverage → strict cov>=1.0 eşiği haksız fail üretebilir
            is_nearmiss = c["declined"] and len(srcs) > 0 and 0.7 <= cov < 1.0
            if is_nearmiss:
                nearmiss.append(f"{tag}: coverage={val.get('coverage')} (<1.0) kaynak={len(srcs)} "
                                f"→ D4={c['D4']}")
            # TEYİT-3: kaynak=0 kısa-devresi — metin deterministik ret mi?
            is_shortcut = c["declined"] and len(srcs) == 0
            if is_shortcut:
                shortcut.append(f"{tag}: {_short(answer, 120)}")

            flags = ""
            if is_nearmiss:
                flags += "  [SINIR cov<1.0]"
            if is_shortcut:
                flags += "  [KAYNAK=0 kısa-devre]"
            print(f"  -- repeat {rep}  conf={final.get('confidence')} kaynak={len(srcs)} "
                  f"declined={c['declined']} coverage={val.get('coverage')} "
                  f"issues={val.get('issues')}{flags}")
            print("     HONEST? " + "  ".join(f"{k}={c[k]}" for k in KEYS))
            print(f"     CEVAP: {_short(answer, 300)}")
            for s in c["uncited_x"]:
                print(f"     >> ATIFSIZ İDDİA: {_short(s, 170)}")
        print()

    print("############ TOPLAM ############")
    print("  " + "  ".join(f"{k}={tot[k]}/{rows}" for k in KEYS))

    print("\n############ ÜÇ TEYİT (mühürden önce) ############")
    print(f"[TEYİT-1] D0→D4 FLIP eden satır ({len(disagree)} adet) — SADECE bunlar honest'a dönmeli:")
    print("\n".join("    " + d for d in disagree) or "    (flip yok)")
    print(f"    beklenti: yalnız gs-036 ×{REPEATS}; D0={tot['D0']}/{rows} → D4={tot['D4']}/{rows}")
    print(f"\n[TEYİT-1b] SINIR — declined+kaynak>0+0.7≤coverage<1.0 ({len(nearmiss)} adet):")
    print("\n".join("    " + d for d in nearmiss) or "    (yok — hiçbir satır 0.99 civarında haksız fail DEĞİL)")
    print("    varsa: strict cov>=1.0 eşiği o satırı haksız fail eder; eşiği gözden geçir.")
    print(f"\n[TEYİT-3] KAYNAK=0 kısa-devre satırları ({len(shortcut)} adet) — metin deterministik ret mi?")
    print("\n".join("    " + s for s in shortcut) or "    (yok)")
    print("    hepsi sabit/şablon ret ise kısa-devre güvenli; serbest-form varsa atıfsız-uydurma deliği.")

    print("\n############ OKUMA ############")
    print("(1) D0'ın tek canlı yanlış-sınıf sınıfı `border_declined_cited`: reddetti + kaynaklı")
    print("    bağlam verdi. D0↔D4 ayrışan satır tam olarak bu sınıftır: kaynak>0 ama coverage=1.0.")
    print("(2) D2c/D2c+ satır-içi [n] regex'ine dayanır; compose.py:64 markeri modelin biçim")
    print("    tercihine bırakır → bu tanımlar RENDER'ı ölçer, gerekçelendirmeyi değil. ÖLÜ.")
    print("(3) D4 = declined ∧ (kaynak=0 ∨ coverage=1.0): validate'in KENDİ oranını ölçüte taşır;")
    print("    yeni hesap yok, metin regex'i yok, entailment gerektirmez. Delik: coverage sözcük")
    print("    örtüşmesidir; ATIFLI-uydurma yalnız entailment ON ile kapanır (D4 bunu maskelemez,")
    print("    çünkü coverage<1.0 olan atıflı-eksik cevabı zaten fail eder).")
finally:
    try:
        db.close()
    except Exception:
        pass

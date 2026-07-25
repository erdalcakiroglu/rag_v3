"""(C) GRAMMAR PoC — VARYANT: sınırlı `citations[]` (maxItems) latency probu.

BAĞLAM: İlk PoC (c_grammar_poc.py, 2026-07-20) grammar'ın CORRECTNESS'i çözdüğünü gösterdi
(tamamlanan üretimde 0 bozuk JSON) ama LATENCY'de eledi: VALID=5/20, HTTP_ERR=15 (hepsi 180s
ReadTimeout). Teşhis (M10_gs012_Kabul_Kaydi §(C)): **sınırsız `citations[]`** → temp=0 greedy
decode citation nesnesi üretmeyi durduramıyor → GBNF tıkanması. Model değil, ŞEMA kaynaklı.

BU PROB: `citations[]`'e `maxItems` (ve opsiyonel string `maxLength`) enjekte edip AYNI tetik/
prompt/temp ile tekrar ölçer. Tek değişken = şema sınırı. Diğer her şey ilk PoC ile birebir aynı
→ temiz A/B.

KARAR KAPISI:
  VALID=N  VE  medyan latency KULLANILABİLİR (base retry ~27s civarı; max<180 = timeout YOK)
     → grammar latency'de de kurtuldu → (C) full rework GREENLIGHT (ayrı brief).
  aksi                       → maxItems latency'yi açmadı → (C) KALICI KAPANIR, retry kalır.
     KOD YAZMADAN döneriz (yalnız bu prob koşuldu).

KULLANIM (H200 app container):
  docker exec -i ragintel-api env POC_MODEL=qwen3.5:35b N=20 MAXITEMS=12 \
      python - < scripts/c_grammar_poc_maxitems.py
  Süpürme önerisi: MAXITEMS=8, 12, 16 ayrı koş; latency/valid eğrisini gör.
  (İkinci lever, gerekirse: STRCAP=600 → answer/claim/quote maxLength; default 0=kapalı.)
"""
from __future__ import annotations

import copy
import json
import os
import statistics
import time
from collections import Counter

import httpx

from ragintel.agents.tools import SUBMIT_ANSWER_SCHEMA
from ragintel.config.settings import LiteLLMSettings

N = int(os.environ.get("N", "20"))
MODEL = os.environ.get("POC_MODEL") or os.environ.get("RAGINTEL_LLM_MODEL") or "qwen3.5:35b"
MAXITEMS = int(os.environ.get("MAXITEMS", "12"))     # ← BU PROBUN TEK YENİ DEĞİŞKENİ
STRCAP = int(os.environ.get("STRCAP", "0"))          # opsiyonel string maxLength (0=kapalı)

# submit_answer parametre şemasının SINIRLI kopyası (üretim şemasına DOKUNMAZ — deepcopy).
SCHEMA = copy.deepcopy(SUBMIT_ANSWER_SCHEMA["function"]["parameters"])
SCHEMA["properties"]["citations"]["maxItems"] = MAXITEMS
if STRCAP > 0:
    SCHEMA["properties"]["answer"]["maxLength"] = STRCAP
    item_props = SCHEMA["properties"]["citations"]["items"]["properties"]
    item_props["claim"]["maxLength"] = STRCAP
    item_props["quote"]["maxLength"] = STRCAP

# gs-012 TETİKLEYİCİSİ birebir (ilk PoC ile aynı) — TR özel-ad quote'ları.
CONTEXT = (
    "[1] 16-KARBON VERGİSİ, EMİSYON TİCARET SİSTEMİ VE SINIRDA KARBON DÜZENLEMESİ\n"
    "Bu çalışma karbon fiyatlama mekanizmalarını incelemektedir. Makalenin yazarları "
    "Hakan ÖZDEMİR ve Merve KÖSE'dir. Emisyon ticaret sistemi (ETS) ve sınırda karbon "
    "düzenlemesi (SKD) politika araçları karşılaştırılmıştır.\n\n"
    "[2] Yöntem ve Bulgular\n"
    "Yazarlar Hakan ÖZDEMİR ve Merve KÖSE, Türkiye için maliyet-etkinlik analizini "
    "sunmuştur; karbon vergisinin idari yükü ETS'ye kıyasla daha düşüktür."
)
SYSTEM = (
    "Yalnızca sağlanan bağlam bloklarındaki bilgiyle yanıtla. Her iddiayı bir citation ile "
    "o bloktan birebir alıntıyla ([n] numarasıyla) destekle. Cevabı submit_answer şemasına "
    "uygun bir JSON nesnesi olarak ver."
)
USER = (
    f"Bağlam blokları:\n{CONTEXT}\n\n"
    "Soru: 16-KARBON VERGİSİ, EMİSYON TİCARET SİSTEMİ VE SINIRDA KARBON DÜZENLEMESİ başlıklı "
    "makalenin yazarları kimlerdir?"
)

s = LiteLLMSettings()
base = (s.api_base or "http://localhost:11434").rstrip("/")
native = base[:-3].rstrip("/") if base.endswith("/v1") else base
headers = {"Content-Type": "application/json"}
if s.api_key:
    headers["Authorization"] = f"Bearer {s.api_key}"

body = {
    "model": MODEL,
    "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}],
    "format": SCHEMA,          # ← SINIRLI şema (maxItems) → grammar-constrained decoding
    "stream": False,
    "options": {"temperature": 0},
}


def classify(status: int, content: str) -> tuple[str, bool]:
    """(sınıf, yazar-doğru). HTTP_ERR / JSON_INVALID / SCHEMA_BAD / VALID."""
    if status != 200:
        return "HTTP_ERR", False
    try:
        obj = json.loads(content)
    except Exception:
        return "JSON_INVALID", False
    ans = obj.get("answer")
    cits = obj.get("citations")
    if not isinstance(ans, str) or not isinstance(cits, list):
        return "SCHEMA_BAD", False
    if len(cits) > MAXITEMS:              # maxItems fiilen uygulandı mı (sağlık kontrolü)
        return "SCHEMA_BAD", False
    for c in cits:
        if not (isinstance(c, dict) and isinstance(c.get("claim"), str)
                and isinstance(c.get("chunk_id"), int) and isinstance(c.get("quote"), str)):
            return "SCHEMA_BAD", False
    authors_ok = any(k in json.dumps(obj, ensure_ascii=False) for k in ("ÖZDEMİR", "KÖSE"))
    return "VALID", authors_ok


print(f"# (C) grammar PoC [maxItems={MAXITEMS} strcap={STRCAP or '-'}] "
      f"model={MODEL} base={native} temp=0 N={N}")
print(f"{'#':>3} {'http':>4} {'ms':>7}  {'sınıf':<13} {'n_cit':>5} yazar-doğru")
rows = []
first_bad = None
with httpx.Client(timeout=180.0) as cli:
    for i in range(1, N + 1):
        t0 = time.perf_counter()
        n_cit = -1
        try:
            r = cli.post(f"{native}/api/chat", json=body, headers=headers)
            dt = int((time.perf_counter() - t0) * 1000)
            content = (r.json().get("message") or {}).get("content", "") if r.status_code == 200 else ""
            klass, aok = classify(r.status_code, content)
            try:
                n_cit = len(json.loads(content).get("citations") or [])
            except Exception:
                n_cit = -1
            if klass != "VALID" and first_bad is None:
                first_bad = f"[{klass}] http={r.status_code} :: {(content or r.text)[:220]}"
        except Exception as exc:
            dt = int((time.perf_counter() - t0) * 1000)
            klass, aok = "HTTP_ERR", False
            if first_bad is None:
                first_bad = f"[EXC] {type(exc).__name__}: {str(exc)[:180]}"
        rows.append((dt, klass, aok))
        print(f"{i:>3} {'200' if klass != 'HTTP_ERR' else 'ERR':>4} {dt:>7}  "
              f"{klass:<13} {n_cit:>5} {'✓' if aok else '—'}")

cnt = Counter(k for _, k, _ in rows)
lat = sorted(dt for dt, _, _ in rows)
p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
med = int(statistics.median(lat))
valid = cnt["VALID"]
no_timeout = lat[-1] < 179000       # 180s timeout tavanına çarpan var mı
print(f"\n== (C) maxItems PoC SONUÇ ({N}) ==")
print(f"   VALID={valid}  JSON_INVALID={cnt['JSON_INVALID']}  SCHEMA_BAD={cnt['SCHEMA_BAD']}  HTTP_ERR={cnt['HTTP_ERR']}")
print(f"   yazar-doğru: {sum(1 for *_ , a in rows if a)}/{N}")
print(f"   LATENCY ms: min={lat[0]} medyan={med} ort={int(statistics.mean(lat))} p95={p95} max={lat[-1]}")
print(f"   KIYAS: base retry ~27000ms (M-10) · ilk PoC (maxItems YOK) medyan≈180000ms (timeout)")
if first_bad:
    print(f"   İLK KUSUR: {first_bad}")

print("\n############ (C) maxItems KAPI ############")
usable = valid == N and no_timeout and med < 60000
if usable:
    print(f"✔ maxItems={MAXITEMS} LATENCY'Yİ AÇTI — {valid}/{N} VALID, timeout YOK, medyan={med}ms.")
    print("  → grammar hem correctness hem latency'de geçti → (C) full rework GREENLIGHT.")
    print("  → sıradaki: submit_answer'ı tool-call'dan format-content'e taşıyan AYRI brief (agent döngüsü).")
    print(f"  → not: medyan={med}ms base ~27000ms'e göre {'PARITE' if med < 35000 else 'DAHA YAVAŞ ama <60s'}.")
else:
    reason = []
    if valid != N: reason.append(f"VALID={valid}/{N}")
    if not no_timeout: reason.append("timeout VAR")
    if med >= 60000: reason.append(f"medyan={med}ms ≥ 60s")
    print(f"⚠ maxItems={MAXITEMS} YETMEDİ ({', '.join(reason)}).")
    print("  → STRCAP ile bir kez daha dene; o da açmıyorsa (C) KALICI KAPANIR — retry çözümü kalır.")
    print("  → KOD YAZMADAN döneriz (yalnız bu prob koşuldu).")
print(f"VALID={valid} N={N} MED={med} NOTIMEOUT={int(no_timeout)}")  # bash için

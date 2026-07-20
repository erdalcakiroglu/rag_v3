"""(C) STRUCTURED-OUTPUTS / GRAMMAR PoC — ön-veri (H200 app container'ında koşar).

SORU (kararı veren): Ollama `format` (JSON Schema → GBNF grammar-constrained decoding)
`submit_answer` şemasında, gs-012'yi tetikleyen TR özel-ad quote'lu içerikte, **temp=0'da**
GEÇERLİ JSON'u GARANTİ ediyor mu?

NEDEN agent'ı atlıyoruz: gs-012 kırılması `submit_answer` TOOL-CALL argümanlarında (Ollama
tool-call yolu `format`'la kısıtlanmıyor). (C)'nin önerisi: submit_answer'ı tool-call yolundan
çıkar, `format`=şema ile CONTENT üretimine taşı → grammar bozuk JSON'u İMKANSIZ kılar. Bu PoC
tam o mekanizmayı izole test eder (litellm eşlemesi değil, ÇEKİRDEK soru: grammar tutuyor mu).

KAPI (gate):
  VALID=20/20 (temp=0)  → grammar temp=0'da bozuk JSON'u kapatıyor → (C) full rework GREENLIGHT;
                          pertürbasyon artıktan ibaret güvenlik ağı olur.
  VALID<20             → grammar DA yetmiyor (GBNF unicode/şema kusuru) → (C) çıkmaz; pivot
                          (post-hoc JSON onarımı / model / Ollama sürüm). KOD YAZMADAN döneriz.

KULLANIM (H200): docker exec -i ragintel-api env POC_MODEL=qwen3.5:35b N=20 python - < scripts/c_grammar_poc.py
"""
from __future__ import annotations

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

# submit_answer'ın parametre şeması = grammar (answer + citations[claim,chunk_id,quote]).
SCHEMA = SUBMIT_ANSWER_SCHEMA["function"]["parameters"]

# gs-012 TETİKLEYİCİSİNİ birebir yeniden üret: TR özel-ad quote'ları (bozuk JSON'un kaynağıydı).
# Sentetik ama gerçekçi bağlam — DB'ye bağlanmadan tetik içeriği garanti (yazarlar bloğun içinde).
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
native = base[:-3].rstrip("/") if base.endswith("/v1") else base   # /v1 → native /api
headers = {"Content-Type": "application/json"}
if s.api_key:
    headers["Authorization"] = f"Bearer {s.api_key}"

body = {
    "model": MODEL,
    "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}],
    "format": SCHEMA,          # ← JSON Schema → grammar-constrained decoding (çekirdek test)
    "stream": False,
    "options": {"temperature": 0},
}


def classify(status: int, content: str) -> tuple[str, bool]:
    """Dönüş: (sınıf, yazar-doğru). Sınıflar:
    HTTP_ERR / JSON_INVALID (grammar BAŞARISIZ) / SCHEMA_BAD (JSON ama şema dışı) / VALID."""
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
    for c in cits:
        if not (isinstance(c, dict) and isinstance(c.get("claim"), str)
                and isinstance(c.get("chunk_id"), int) and isinstance(c.get("quote"), str)):
            return "SCHEMA_BAD", False
    authors_ok = any(k in json.dumps(obj, ensure_ascii=False) for k in ("ÖZDEMİR", "KÖSE"))
    return "VALID", authors_ok


print(f"# (C) grammar PoC — model={MODEL}  base={native}  format=submit_answer şeması  temp=0  N={N}")
print(f"{'#':>3} {'http':>4} {'ms':>7}  {'sınıf':<13} yazar-doğru")
rows = []
first_bad = None
with httpx.Client(timeout=180.0) as cli:
    for i in range(1, N + 1):
        t0 = time.perf_counter()
        try:
            r = cli.post(f"{native}/api/chat", json=body, headers=headers)
            dt = int((time.perf_counter() - t0) * 1000)
            content = (r.json().get("message") or {}).get("content", "") if r.status_code == 200 else ""
            klass, aok = classify(r.status_code, content)
            if klass != "VALID" and first_bad is None:
                first_bad = f"[{klass}] http={r.status_code} :: {(content or r.text)[:220]}"
        except Exception as exc:
            dt = int((time.perf_counter() - t0) * 1000)
            klass, aok = "HTTP_ERR", False
            if first_bad is None:
                first_bad = f"[EXC] {type(exc).__name__}: {str(exc)[:180]}"
        rows.append((dt, klass, aok))
        print(f"{i:>3} {'200' if klass != 'HTTP_ERR' else 'ERR':>4} {dt:>7}  {klass:<13} {'✓' if aok else '—'}")

cnt = Counter(k for _, k, _ in rows)
lat = sorted(dt for dt, _, _ in rows)
p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
valid = cnt["VALID"]
print(f"\n== (C) PoC SONUÇ ({N}) ==")
print(f"   VALID={valid}  JSON_INVALID={cnt['JSON_INVALID']}  SCHEMA_BAD={cnt['SCHEMA_BAD']}  HTTP_ERR={cnt['HTTP_ERR']}")
print(f"   yazar-doğru: {sum(1 for *_ , a in rows if a)}/{N}")
print(f"   LATENCY ms (grammar, temp=0): min={lat[0]} medyan={int(statistics.median(lat))} "
      f"ort={int(statistics.mean(lat))} p95={p95} max={lat[-1]}")
if first_bad:
    print(f"   İLK KUSUR: {first_bad}")
print("\n############ (C) KAPI ############")
if valid == N:
    print(f"✔ GRAMMAR ÇÖZER — {N}/{N} temp=0'da GEÇERLİ JSON. Bozuk JSON temp=0'da kapandı.")
    print("  → (C) full rework GREENLIGHT: submit_answer'ı tool-call'dan format-content'e taşı.")
    print("  → pertürbasyon artık artık güvenlik ağı (gerekmez). Latency deltası yukarıda.")
else:
    print(f"⚠ GRAMMAR YETMİYOR — {valid}/{N} geçerli. GBNF/şema temp=0'da bozuk JSON'u KAPATMADI.")
    print("  → (C) çıkmaz; KOD YAZMADAN pivot: post-hoc JSON onarımı / model / Ollama sürüm.")
print(f"VALID={valid} N={N}")  # bash için

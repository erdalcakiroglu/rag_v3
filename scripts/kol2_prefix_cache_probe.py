"""kol-2 ön-veri — PREFIX (KV-cache) yeniden-kullanım probu. (H200 app container'ında koşar.)

SORU (kararı veren): Ollama bu modelde/donanımda ardışık `/api/chat` çağrıları arasında ORTAK
ÖN-EKİN KV-cache'ini yeniden kullanıyor mu? kol-2'nin ~2.3s ödülünün TAMAMI buna bağlı — prefix
kararlıysa prompt-eval yalnız yeni token'lara ödenir; değilse her tur tüm bağlam yeniden değerlenir.

kol-2 iki ön-ek istikrarsızlığını hedefler (kodda doğrulandı):
  1) `agent.py:129` — `[Kalan iterasyon: N]` sistem mesajının içinde, her tur değişir → ilk mesaj
     her tur farklı → tüm prefix geçersiz.
  2) `agent.py:159-160` — bağlam her tur yeniden kurulur (`_apply_budget` farklı tahliye edebilir).

BU PROB tam mekanizmayı izole ölçer: aynı büyük prefix + küçük ek (append) ile iki senaryo —
STABLE (sayaç ön-ekte DEĞİL) vs MUTATED (bugünkü davranış: sayaç ön-ekte, her tur değişir).
Ollama native yanıtındaki `prompt_eval_count`/`prompt_eval_duration` ile 2. çağrının prefix'i
yeniden değerleyip değerlemediğini görürüz.

KARAR KAPISI:
  STABLE 2. çağrı prompt_eval_count ≈ yalnız EK token (küçük)  VE  MUTATED'e göre belirgin düşük
     → prefix cache ÇALIŞIYOR → kol-2 ödülü GERÇEK → rework GREENLIGHT (+ zorunlu k=3 karne).
  STABLE 2. çağrı da ~tüm bağlamı yeniden değerliyor (MUTATED'e yakın)
     → Ollama bu çağrılar arası cache kullanmıyor → kol-2 latency KAZANDIRMAZ (yalnız byte-kararlılık)
     → rework'ü RAFA KALDIR. KOD YAZMADAN döneriz.

KULLANIM: docker exec -i ragintel-api env POC_MODEL=qwen3.5:35b PROMPT=v2 \
    python - < scripts/kol2_prefix_cache_probe.py
"""
from __future__ import annotations

import json
import os

import httpx

from ragintel.agents.prompts import PROMPT_VERSIONS
from ragintel.config.settings import LiteLLMSettings

MODEL = os.environ.get("POC_MODEL") or os.environ.get("RAGINTEL_LLM_MODEL") or "qwen3.5:35b"
SYS_BASE = PROMPT_VERSIONS.get(os.environ.get("PROMPT", "v2"), PROMPT_VERSIONS["v2"])

# ~2500 token gerçekçi bağlam (agent'ın her turda ön-ekte taşıdığı yük).
_PARA = (
    "Karbon vergisi, fosil yakıtların karbon içeriğine göre alınan bir çevre vergisidir. "
    "Emisyon ticaret sistemi (ETS) ise toplam emisyon tavanı koyup tahsisatları piyasada "
    "işleme tabi tutar. Sınırda karbon düzenlemesi (SKD), ithal ürünlerin gömülü karbonuna "
    "fiyat uygular ve karbon kaçağını önlemeyi amaçlar. Bu araçların idari yükü, kapsamı ve "
    "gelir etkisi ülkeden ülkeye değişir; İsveç 1991'de yüksek oranlı bir karbon vergisi "
    "uygularken, Türkiye henüz doğrudan bir karbon fiyatlaması benimsememiştir.\n"
)
CONTEXT = "".join(f"[{i}] Blok {i}\n{_PARA}\n" for i in range(1, 13))
BIG_USER = f"Soru: Karbon fiyatlama araçlarını karşılaştır.\n\nBağlam blokları:\n{CONTEXT}"

# Turlar-arası tail: (assistant tool-call) + (tool result). İkinci tur bunu APPEND eder.
TAIL1 = [
    {"role": "assistant", "content": "Bağlamı inceliyorum."},
    {"role": "user", "content": "[araç sonucu 1] ETS ve karbon vergisi karşılaştırma bloğu döndü."},
]
TAIL2 = [
    {"role": "assistant", "content": "Ek kaynak arıyorum."},
    {"role": "user", "content": "[araç sonucu 2] SKD kapsamı ve gelir etkisi bloğu döndü."},
]


def sys_msg(remaining: int | None) -> dict:
    # MUTATED: sayaç ön-ekte (bugünkü agent.py:129). STABLE: sayaç YOK (taşınmış varsayımı).
    content = SYS_BASE if remaining is None else f"{SYS_BASE}\n\n[Kalan iterasyon: {remaining}]"
    return {"role": "system", "content": content}


s = LiteLLMSettings()
base = (s.api_base or "http://localhost:11434").rstrip("/")
native = base[:-3].rstrip("/") if base.endswith("/v1") else base
headers = {"Content-Type": "application/json"}
if s.api_key:
    headers["Authorization"] = f"Bearer {s.api_key}"


def call(messages: list[dict]) -> dict:
    body = {
        "model": MODEL, "messages": messages, "stream": False,
        "keep_alive": "10m",                       # model yüklü kalsın → cache yaşasın
        "options": {"temperature": 0, "num_predict": 8},   # üretimi kıs; prompt_eval'i ölçüyoruz
    }
    r = cli.post(f"{native}/api/chat", json=body, headers=headers, timeout=180.0)
    j = r.json()
    return {
        "pec": j.get("prompt_eval_count"),
        "ped_ms": int((j.get("prompt_eval_duration") or 0) / 1e6),
        "ec": j.get("eval_count"),
    }


print(f"# kol-2 prefix-cache probu — model={MODEL} base={native}")
with httpx.Client() as cli:
    # ---- STABLE senaryo: sayaç ön-ekte DEĞİL; head iki çağrıda AYNI ----
    head_stable = [sys_msg(None), {"role": "user", "content": BIG_USER}]
    s1 = call(head_stable + TAIL1)                     # cache'i doldur
    s2 = call(head_stable + TAIL1 + TAIL2)             # ortak prefix → cache-hit beklenir
    # ---- MUTATED senaryo: sayaç ön-ekte; head her çağrıda DEĞİŞİR (bugünkü davranış) ----
    m1 = call([sys_msg(3), {"role": "user", "content": BIG_USER}] + TAIL1)
    m2 = call([sys_msg(2), {"role": "user", "content": BIG_USER}] + TAIL1 + TAIL2)

print(f"\n{'senaryo':<28} {'prompt_eval_count':>18} {'prompt_eval_ms':>15}")
print(f"{'STABLE  çağrı-1 (cache doldur)':<28} {str(s1['pec']):>18} {s1['ped_ms']:>15}")
print(f"{'STABLE  çağrı-2 (append)':<28} {str(s2['pec']):>18} {s2['ped_ms']:>15}")
print(f"{'MUTATED çağrı-1':<28} {str(m1['pec']):>18} {m1['ped_ms']:>15}")
print(f"{'MUTATED çağrı-2 (sayaç değişti)':<28} {str(m2['pec']):>18} {m2['ped_ms']:>15}")

print("\n############ kol-2 KAPI ############")
pec_s2, pec_m2 = s2["pec"] or 0, m2["pec"] or 0
ped_s2, ped_m2 = s2["ped_ms"] or 0, m2["ped_ms"] or 0
# STABLE 2. çağrı prefix'i cache'lediyse prompt_eval_count küçük olmalı (yalnız EK); MUTATED büyük.
cache_works = pec_m2 > 0 and pec_s2 < 0.5 * pec_m2
if cache_works:
    saved = ped_m2 - ped_s2
    print(f"✔ PREFIX CACHE ÇALIŞIYOR — STABLE 2. çağrı yalnız {pec_s2} token değerledi "
          f"(MUTATED {pec_m2}). prompt-eval tasarrufu ≈ {saved}ms/tur.")
    print("  → kol-2 ödülü GERÇEK. rework GREENLIGHT: (a) sayacı tail'e taşı, (b) append-only bağlam"
          " + _apply_budget tahliye politikası. SONRA zorunlu k=3 karne (kalite regresyonu YOK kanıtı).")
else:
    print(f"⚠ CACHE YOK/ZAYIF — STABLE 2. çağrı {pec_s2} token, MUTATED {pec_m2}: prefix kararlılığı "
          "prompt-eval'i düşürmedi. Ollama bu çağrılar arası cache kullanmıyor.")
    print("  → kol-2 latency KAZANDIRMAZ (yalnız byte-kararlılık). rework'ü RAFA KALDIR; KOD YAZMADAN döneriz.")
print(f"PEC_S2={pec_s2} PEC_M2={pec_m2} PED_S2={ped_s2} PED_M2={ped_m2}")  # bash için

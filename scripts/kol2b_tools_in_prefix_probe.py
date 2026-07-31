"""kol-2 (b) ön-veri — TOOL ŞEMASI PREFIX'TE Mİ? (KV-cache). (H200 app container'ında koşar.)

SORU (#4'ü karara bağlar): Agent her turda `tools=llm_tool_schemas()` gönderiyor. Bu şema
qwen3.5 chat-template'inde sistem prologuna (`<tools>…</tools>`), yani ~2500 token bağlamdan
ÖNCE, PREFIX'e render edilir. (a)+(b) prefix'i dondurduğuna göre iki iddia var:

  İDDİA-1: tool şeması BYTE-ÖZDEŞ gönderildiğinde prefix'in parçasıdır → 1. turdan sonra
           KV-cache'ten gelir (tur başına ~0.7s ödenmez). → #4'ün hedeflediği tasarruf (b) ile
           ZATEN yakalanmış.
  İDDİA-2: tool şemasını sonraki turda DÜŞÜRMEK (#4'ün önerdiği eylem) prologun ÖNÜNÜ değiştirir
           → tüm prefix cache-MISS → ~2500 bağlam token'ı her tur yeniden değerlenir. → #4 (b)'yi
           TERSİNE ÇEVİRİR.

BU PROB ikisini de izole ölçer. Ölçüt DURATION'dır (Ollama cache-hit'te bile count'u tüm prefix
olarak raporlar — bkz. kol2_prefix_cache_probe.py). Gerçek tool şemaları registry'den alınır
(service=None; DB'siz) → bytes ÜRETİMLE aynı.

KARAR KAPISI:
  A.çağrı-2 (tools stabil) DÜŞÜK  → İDDİA-1 DOĞRU: tool şeması cache'e biner.
  B.çağrı-2 (tools düşürüldü) A.çağrı-2'den BELİRGİN YÜKSEK → İDDİA-2 DOĞRU: düşürmek prefix'i kırar.
  Her ikisi de → #4 REDÜNDAN + ZARARLI: rafa kaldır, KOD YAZMA.

KULLANIM: docker exec -i ragintel-api env POC_MODEL=qwen3.5:35b PROMPT=v2 \
    python - < scripts/kol2b_tools_in_prefix_probe.py
"""
from __future__ import annotations

import json
import os

import httpx

from ragintel.agents.prompts import PROMPT_VERSIONS
from ragintel.agents.tools import ToolRegistry
from ragintel.config.settings import LiteLLMSettings

MODEL = os.environ.get("POC_MODEL") or os.environ.get("RAGINTEL_LLM_MODEL") or "qwen3.5:35b"
SYS_BASE = PROMPT_VERSIONS.get(os.environ.get("PROMPT", "v2"), PROMPT_VERSIONS["v2"])

# ÜRETİM tool şemaları (agent.py'nin non-exhausted turda gönderdiğiyle BİREBİR).
TOOLS = ToolRegistry(service=None).llm_tool_schemas()
TOOLS_BYTES = len(json.dumps(TOOLS, ensure_ascii=False))

# ~2500 token gerçekçi bağlam (kol2_prefix_cache_probe.py ile aynı yük).
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

# (a) sonrası: sayaç sistem mesajında DEĞİL → head turlar arası byte-özdeş. Sayacı prefix'e
# KOYMUYORUZ (probun konusu tool şeması, sayaç değil).
HEAD = [{"role": "system", "content": SYS_BASE}, {"role": "user", "content": BIG_USER}]
TAIL1 = [
    {"role": "assistant", "content": "Bağlamı inceliyorum."},
    {"role": "user", "content": "[araç sonucu 1] ETS ve karbon vergisi karşılaştırma bloğu döndü."},
]
TAIL2 = [
    {"role": "assistant", "content": "Ek kaynak arıyorum."},
    {"role": "user", "content": "[araç sonucu 2] SKD kapsamı ve gelir etkisi bloğu döndü."},
]

s = LiteLLMSettings()
base = (s.api_base or "http://localhost:11434").rstrip("/")
native = base[:-3].rstrip("/") if base.endswith("/v1") else base
headers = {"Content-Type": "application/json"}
if s.api_key:
    headers["Authorization"] = f"Bearer {s.api_key}"


def call(messages: list[dict], tools: list[dict] | None) -> dict:
    body = {
        "model": MODEL, "messages": messages, "stream": False,
        "keep_alive": "10m",
        "options": {"temperature": 0, "num_predict": 8},
    }
    if tools:
        body["tools"] = tools
    r = cli.post(f"{native}/api/chat", json=body, headers=headers, timeout=180.0)
    j = r.json()
    return {
        "pec": j.get("prompt_eval_count"),
        "ped_ms": int((j.get("prompt_eval_duration") or 0) / 1e6),
        "ec": j.get("eval_count"),
    }


print(f"# kol-2 (b) tool-in-prefix probu — model={MODEL} base={native}")
print(f"# tool şeması: {len(TOOLS)} tool, {TOOLS_BYTES} byte (llm_tool_schemas — üretimle birebir)")
with httpx.Client() as cli:
    # ---- A. TOOLS STABLE: her iki çağrı da AYNI tool şemasını gönderir (bugünkü agent davranışı) ----
    a1 = call(HEAD + TAIL1, TOOLS)                    # cache'i doldur (tools prefix'te)
    a2 = call(HEAD + TAIL1 + TAIL2, TOOLS)            # ortak prefix (tools dahil) → cache-hit beklenir
    # ---- B. TOOLS DROP: çağrı-1 tool'lu, çağrı-2 tool'suz (#4'ün önerdiği eylem) ----
    b1 = call(HEAD + TAIL1, TOOLS)                    # cache'i doldur (tools prefix'te)
    b2 = call(HEAD + TAIL1 + TAIL2, None)             # tools DÜŞÜRÜLDÜ → prolog önü değişir → miss beklenir

print(f"\n{'senaryo':<34} {'prompt_eval_count':>18} {'prompt_eval_ms':>15}")
print(f"{'A. tools STABLE  çağrı-1 (doldur)':<34} {str(a1['pec']):>18} {a1['ped_ms']:>15}")
print(f"{'A. tools STABLE  çağrı-2 (append)':<34} {str(a2['pec']):>18} {a2['ped_ms']:>15}")
print(f"{'B. tools DROP    çağrı-1 (doldur)':<34} {str(b1['pec']):>18} {b1['ped_ms']:>15}")
print(f"{'B. tools DROP    çağrı-2 (tool YOK)':<34} {str(b2['pec']):>18} {b2['ped_ms']:>15}")

print("\n############ #4 KARAR KAPISI ############")
a2p, b2p = a2["ped_ms"] or 0, b2["ped_ms"] or 0
a1p = a1["ped_ms"] or 0
# İDDİA-1: tools stabil iken çağrı-2 cache-hit (çağrı-1'in yarısından az → yalnız ek token değerlendi).
claim1 = a1p > 0 and a2p < 0.5 * a1p
# İDDİA-2: tools düşünce çağrı-2 prefix'i kaybeder (A.çağrı-2'nin belirgin üstünde, ~tam yeniden-değerleme).
claim2 = a2p >= 0 and b2p >= 2.0 * max(a2p, 1)
print(f"İDDİA-1 (tool şeması cache'e biner): {'DOĞRU' if claim1 else 'DOĞRULANMADI'} — "
      f"A.çağrı-2 {a2p}ms vs A.çağrı-1 {a1p}ms.")
print(f"İDDİA-2 (tool'u düşürmek prefix'i kırar): {'DOĞRU' if claim2 else 'DOĞRULANMADI'} — "
      f"B.çağrı-2 {b2p}ms vs A.çağrı-2 {a2p}ms.")
if claim1 and claim2:
    print("→ #4 REDÜNDAN + ZARARLI: ~0.7s zaten (b) ile cache'te; şemayı düşürmek (b)'yi tersine "
          "çevirir (tur başına ~597ms geri gelir). RAFA KALDIR, KOD YAZMA.")
elif claim1 and not claim2:
    print("→ tool şeması cache'e biniyor (tasarruf (b)'de yakalı) ama düşürmenin zararı ölçülemedi; "
          "#4 en iyi ihtimalle REDÜNDAN. Yine de kod yazma gerekçesi yok.")
else:
    print("→ BEKLENMEDİK: tool şeması cache davranışı varsayımı tutmadı. #4'ü yeniden değerlendir; "
          "prod chat-template'inde tool render konumunu (prolog mu, generation sınırı mı) doğrula.")
print(f"A1={a1p} A2={a2p} B1={b1['ped_ms']} B2={b2p} "
      f"PEC[A1={a1['pec']} A2={a2['pec']} B2={b2['pec']}]")  # bash için

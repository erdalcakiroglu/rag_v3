"""M-15 ADIM 1g — KV ÖN-EK CACHE'i çalışıyor mu? (en büyük kolun ölüm-kalım testi)

NEDEN: temiz n=15 anatomisinde soru-başı sürenin ~yarısı prompt-eval. Ölçülen
marjinal oran 0.33 ms/token (~3000 tok/s) → 5.3k'lık prompt HER TURDA ~1.75s.
Ajan turlar arası ön-eki bozuyor ([agent.py:129] sistem mesajına değişen
"[Kalan iterasyon: N]" sayacı; [agent.py:132] bağlamı her tur yeniden render).
Ön-ek sabitlenirse tur başına ~1.7s, soru başına ~3.5s kazanç olur ve ÜRETİLEN
TOKEN HİÇ DEĞİŞMEZ (çıktı bit bit aynı) — ama YALNIZCA Ollama gerçekten ön-ek
cache'i yapıyorsa. Bu betik onu ölçer; yapmıyorsa kol ölür, kod yazılmaz.

DENEY (hepsi num_predict=1, üretim maliyeti sabit ~0):
  A) soğuk    : ~5k prompt, ilk kez            → tam prompt-eval beklenir
  B) AYNISI   : birebir aynı mesajlar           → cache varsa prompt_eval ÇÖKER
  C) ÖN-EK BOZUK: ilk satırda tek sayı değişik  → ajanın BUGÜNKÜ durumu
  D) APPEND   : aynı ön-ek + sona yeni mesaj    → ajanın OLASI hedef durumu

OKUMA: B ve D, A'ya göre çok hızlıysa cache VAR ve ön-ek disiplini kazandırır.
C ≈ A ise "sayaç sistem mesajında" tek başına tüm cache'i öldürüyor demektir.

KULLANIM: docker exec -i ragintel-api python -u - < scripts/m15_prefix_cache_probe.py
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

MODEL = os.environ.get("POC_MODEL") or os.environ.get("RAGINTEL_LLM_MODEL") or "qwen3.5:35b"

base = (os.environ.get("RAGINTEL_LLM_API_BASE") or "").rstrip("/")
for suf in ("/v1", "/ollama/v1", "/api"):
    if base.endswith(suf):
        base = base[: -len(suf)]
        break
if not base:
    raise SystemExit("RAGINTEL_LLM_API_BASE boş — native uç türetilemedi.")

# ~5k token'lık gerçekçi bağlam (ajanın prompt büyüklüğüne yakın olsun diye).
PARA = ("Karbon vergisi, sera gazı salımını fiyatlandırarak azaltmayı amaçlayan bir "
        "çevre vergisidir. Uygulamada salınan karbondioksit eşdeğeri başına bir bedel "
        "belirlenir ve bu bedel yakıt üreticileri ile ithalatçılarından tahsil edilir. ")
CONTEXT = "".join(f"[blok {i}] {PARA}" for i in range(60))


def chat(messages: list[dict], tag: str) -> dict:
    body = {"model": MODEL, "messages": messages, "stream": False,
            "options": {"temperature": 0, "num_predict": 1}}
    req = urllib.request.Request(f"{base}/api/chat", data=json.dumps(body).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    key = os.environ.get("RAGINTEL_LLM_API_KEY")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read())
    pc = d.get("prompt_eval_count") or 0
    pd_ms = (d.get("prompt_eval_duration") or 0) / 1e6
    print(f"  {tag:<34} prompt_eval_count={pc:<6} prompt_eval={pd_ms:8.0f}ms "
          f"({pc/(pd_ms/1000):.0f} tok/s)" if pd_ms else f"  {tag:<34} prompt_eval={pd_ms}ms")
    return {"count": pc, "ms": pd_ms}


SYS_STATIC = "Sen bir belge asistanısın. Yanıtı MUTLAKA submit_answer ile teslim et."
USER = f"Soru: Karbon vergisi nedir?\n\nBağlam blokları:\n{CONTEXT}"

base_msgs = [{"role": "system", "content": SYS_STATIC},
             {"role": "user", "content": USER}]

print(f"# M-15 ADIM 1g — KV ön-ek cache probu — model={MODEL}")
print(f"# bağlam ~{len(CONTEXT)//3} token (ajanın 5.3k'sına yakın olması hedeflendi)\n")

# SIRA KRİTİK: Ollama varsayılanda TEK slot tutar; araya giren farklı bir prompt
# cache'i ezer. İlk sürümde D, C0/C'den SONRA geliyordu → önkoşulu yok edilmiş,
# "append kazandırmıyor" sonucu GEÇERSİZdi. D artık B'nin hemen ardında.
a = chat(base_msgs, "A) soğuk (ilk kez)")
b = chat(base_msgs, "B) AYNI mesajlar (tekrar)")

# D) hedef tasarım: ön-ek AYNEN korunur, yeni bilgi SONA eklenir (ajanın olası hali)
d = chat(base_msgs + [{"role": "assistant", "content": "Arama yapıyorum."},
                      {"role": "user", "content": "Bulunan blok: " + PARA}],
         "D) APPEND (ön-ek sabit, sona ek)")

# C) ajanın BUGÜNKÜ hali: sistem mesajının SONUNDA değişen sayaç → ön-ek ilk bloktan kırılır.
# C0 slotu "sayaç=2" ile doldurur; C yalnız SAYIYI değiştirir → kayıp yalnız sayaçtandır.
c_msgs = [{"role": "system", "content": SYS_STATIC + "\n\n[Kalan iterasyon: 2]"},
          {"role": "user", "content": USER}]
chat(c_msgs, "C0) sayaç=2 (slotu doldur)")
c = chat([{"role": "system", "content": SYS_STATIC + "\n\n[Kalan iterasyon: 1]"},
          {"role": "user", "content": USER}], "C) sayaç=1 (yalnız SAYI değişti)")

print("\n############ KARAR ############")
if not a["ms"]:
    print("  prompt_eval_duration gelmedi — prob sonuçsuz.")
else:
    def rel(x):
        return f"%{100*x['ms']/a['ms']:.0f} of A"
    print(f"  B (aynı)      → {rel(b)}   {'CACHE VAR' if b['ms'] < 0.5*a['ms'] else 'CACHE YOK'}")
    print(f"  C (sayaç)     → {rel(c)}   "
          f"{'sayaç cache_i ÖLDÜRÜYOR → agent.py:129 hedef' if c['ms'] > 0.5*a['ms'] else 'sayaç zararsız'}")
    print(f"  D (append)    → {rel(d)}   "
          f"{'append-only KAZANDIRIR → agent.py:132 hedef' if d['ms'] < 0.5*a['ms'] else 'append-only kazandırmıyor'}")
    if b["ms"] >= 0.5 * a["ms"]:
        print("\n  → Ollama bu yolda ön-ek cache'i YAPMIYOR. Prompt-eval kolu ÖLÜ;"
              " M-15'in tek kolu ÜRETİMİ KISALTMAK (atılan serbest metin) olarak kalır.")
    else:
        print("\n  → Cache VAR. Ön-ek disiplini (sayacı sistem mesajından çıkar, bağlamı"
              " append-only kur) çıktıyı DEĞİŞTİRMEDEN tur başına ~1.7s kazandırabilir.")

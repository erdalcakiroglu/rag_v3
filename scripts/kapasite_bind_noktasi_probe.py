"""KAPASİTE ön-veri — eşzamanlılık NEREDE bağlanıyor? (H200 app container'ında koşar.)

BAĞLAM: `runtime.py:137` `self._lock = threading.Lock()  # tek-bağlantı saver`. Lock, hem ask
hem ask_stream'de TÜM graph invoke'unu (Ollama çağrıları dahil) sarar → app tümüyle serileşir.
Öneri: lock'u kaldır (checkpointer connection-pool). AMA lock'u kaldırınca iki istek Ollama'ya
kadar ilerler; Ollama tek-akış serileştiriyorsa orada yine sıraya girerler → lock-removal ERKEN
optimizasyon olur. Bu prob, kilide DOKUNMADAN önce bağlanma noktasını attribute eder.

SORU: 2 eşzamanlı üretim Ollama'da PARALELLEŞİYOR mu, SERİLEŞİYOR mu?
  paralel_faktör = Σ(bireysel süre) / duvar-saati.  ~2.0 = tam paralel · ~1.0 = tam seri.

KARAR KAPISI (Test A — Ollama-direct, KİLİT YOK, saf model sunucusu):
  paralel_faktör ≥ ~1.6 → Ollama 2 isteği paralel işliyor → APP `_lock` gerçek darboğaz →
        lock-removal ANLAMLI (sonraki iş: checkpointer connection-pool + eşzamanlılık-güvenliği).
  paralel_faktör ≤ ~1.2 → Ollama SERİLEŞTİRİYOR (tek GPU akışı / OLLAMA_NUM_PARALLEL=1) →
        lock'u kaldırmak İstekleri lock yerine Ollama'da kuyruğa alır, KAZANÇ YOK → lock-removal
        ERKEN. Gerçek tavan model sunucusu: önce ucuz lever OLLAMA_NUM_PARALLEL, sonra vLLM/replica.

Test B (opsiyonel, APP_TOKEN verilirse): 2 eşzamanlı /api/ask — app'in KİLİTLİ gerçek davranışı.
Test A ↔ B kıyası: A paralel + B seri ise darboğaz kesin app tarafı (lock/checkpointer).

KULLANIM: docker exec -i ragintel-api env POC_MODEL=qwen3.5:35b N_PREDICT=128 \
    [APP_TOKEN=... APP_URL=http://localhost:8000] python - < scripts/kapasite_bind_noktasi_probe.py
"""
from __future__ import annotations

import os
import threading
import time

import httpx

from ragintel.config.settings import LiteLLMSettings

MODEL = os.environ.get("POC_MODEL") or os.environ.get("RAGINTEL_LLM_MODEL") or "qwen3.5:35b"
N_PREDICT = int(os.environ.get("N_PREDICT", "128"))   # gerçek decode yükü (contention görünsün)
APP_TOKEN = os.environ.get("APP_TOKEN")
APP_URL = (os.environ.get("APP_URL") or "http://localhost:8000").rstrip("/")

s = LiteLLMSettings()
base = (s.api_base or "http://localhost:11434").rstrip("/")
native = base[:-3].rstrip("/") if base.endswith("/v1") else base
oheaders = {"Content-Type": "application/json"}
if s.api_key:
    oheaders["Authorization"] = f"Bearer {s.api_key}"

PROMPT = ("Karbon vergisi ile emisyon ticaret sistemini idari yük, kapsam ve gelir etkisi "
          "açısından ayrıntılı karşılaştır. En az beş cümle yaz.")
OLLAMA_BODY = {
    "model": MODEL,
    "messages": [{"role": "user", "content": PROMPT}],
    "stream": False, "keep_alive": "10m",
    "options": {"temperature": 0, "num_predict": N_PREDICT},
}


def _timed(fn) -> tuple[float, float, bool]:
    t0 = time.perf_counter()
    ok = True
    try:
        fn()
    except Exception:
        ok = False
    return t0, time.perf_counter(), ok


def ollama_call():
    with httpx.Client() as cli:
        r = cli.post(f"{native}/api/chat", json=OLLAMA_BODY, headers=oheaders, timeout=300.0)
        r.raise_for_status()


def app_call():
    with httpx.Client() as cli:
        r = cli.post(f"{APP_URL}/api/ask", json={"question": PROMPT},
                     headers={"Authorization": f"Bearer {APP_TOKEN}"}, timeout=300.0)
        r.raise_for_status()


def concurrent(fn, n: int = 2) -> dict:
    """n çağrıyı Barrier ile AYNI ANDA başlat; bireysel süre + duvar-saati ölç."""
    barrier = threading.Barrier(n)
    results: list[tuple[float, float, bool]] = [None] * n  # type: ignore

    def worker(i: int):
        barrier.wait()
        results[i] = _timed(fn)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    starts = [r[0] for r in results]
    ends = [r[1] for r in results]
    durs = [(e - s0) for s0, e, _ in results]
    wall = max(ends) - min(starts)
    return {"durs": durs, "wall": wall, "ok": all(r[2] for r in results),
            "pf": (sum(durs) / wall) if wall > 0 else 0.0}


print(f"# KAPASİTE bind-noktası probu — model={MODEL} base={native} n_predict={N_PREDICT}")

# baseline: tek Ollama çağrısı
b0, b1, bok = _timed(ollama_call)
L = b1 - b0
print(f"\n[baseline] tek Ollama üretimi: {L*1000:.0f}ms (ok={bok})")

# Test A: 2 eşzamanlı Ollama-direct
A = concurrent(ollama_call, 2)
print(f"\n[Test A] 2 eşzamanlı Ollama-direct (KİLİT YOK):")
print(f"   bireysel: {[f'{d*1000:.0f}ms' for d in A['durs']]}  duvar-saati: {A['wall']*1000:.0f}ms")
print(f"   PARALEL FAKTÖR: {A['pf']:.2f}   (~2.0=paralel, ~1.0=seri)   ok={A['ok']}")

# Test B (opsiyonel): 2 eşzamanlı /api/ask
if APP_TOKEN:
    B = concurrent(app_call, 2)
    print(f"\n[Test B] 2 eşzamanlı /api/ask (app _lock DAHİL):")
    print(f"   bireysel: {[f'{d*1000:.0f}ms' for d in B['durs']]}  duvar-saati: {B['wall']*1000:.0f}ms")
    print(f"   PARALEL FAKTÖR: {B['pf']:.2f}   ok={B['ok']}")
else:
    B = None
    print("\n[Test B] atlandı (APP_TOKEN verilmedi) — Test A tek başına attribute için yeterli.")

print("\n############ KAPASİTE KARAR KAPISI ############")
pf = A["pf"]
if pf >= 1.6:
    print(f"✔ OLLAMA PARALELLEŞİYOR (pf={pf:.2f}) → model sunucusu tavan DEĞİL.")
    print("  → Darboğaz APP `_lock` (tek-bağlantı saver). lock-removal ANLAMLI.")
    print("  → Sonraki iş: checkpointer connection-pool + graph eşzamanlılık-güvenliği tasarımı (KOD DEĞİL, tasarım).")
    if B and B["pf"] < 1.3:
        print(f"  → Test B doğruladı: app seri (pf={B['pf']:.2f}) ↔ Ollama paralel → darboğaz kesin app tarafı.")
elif pf <= 1.2:
    print(f"⚠ OLLAMA SERİLEŞTİRİYOR (pf={pf:.2f}) → model sunucusu GERÇEK TAVAN.")
    print("  → lock'u kaldırmak istekleri lock yerine Ollama'da kuyruğa alır — KAZANÇ YOK, lock-removal ERKEN + riskli.")
    print("  → ÖNCE ucuz lever: OLLAMA_NUM_PARALLEL artır + tekrar ölç. Açmıyorsa milestone = vLLM/replica (prod kapasite).")
else:
    print(f"~ KISMİ (pf={pf:.2f}) — net değil. N_PREDICT'i artırıp (daha uzun decode) tekrar ölç; "
          "OLLAMA_NUM_PARALLEL değerini de kontrol et.")
print(f"PF_A={pf:.2f} L_ms={L*1000:.0f}" + (f" PF_B={B['pf']:.2f}" if B else ""))  # bash için
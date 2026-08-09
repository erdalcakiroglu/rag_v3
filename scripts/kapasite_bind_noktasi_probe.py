"""KAPASİTE ön-veri — eşzamanlılık NEREDE bağlanıyor? (H200 app container'ında koşar.)

BAĞLAM: `runtime.py:137` `self._lock = threading.Lock()  # tek-bağlantı saver`. Lock, hem ask
hem ask_stream'de TÜM graph invoke'unu (Ollama çağrıları dahil) sarar → app tümüyle serileşir.
Öneri: lock'u kaldır (checkpointer connection-pool). AMA lock'u kaldırınca iki istek Ollama'ya
kadar ilerler; Ollama tek-akış serileştiriyorsa orada yine sıraya girerler → lock-removal ERKEN
optimizasyon olur. Bu prob, kilide DOKUNMADAN önce bağlanma noktasını attribute eder.

SORU: 2 eşzamanlı üretim Ollama'da PARALELLEŞİYOR mu, SERİLEŞİYOR mu?
  BİRİNCİL SİNYAL: ham süre-ORANI = max(dur)/min(dur).
    ~2.0 → SERİ (biri diğerini bekledi: bekle-sonra-çalış) · ~1.0 → PARALEL.
  DİKKAT (v2 düzeltme): barrier ikisini t=0'da salar; seri durumda çağrı-2 bekleyip koşar →
    dur2≈2·dur1 → pf=Σ/wall=3L/2L=1.5. Yani pf'nin SERİ tabanı 1.5'tir (1.0 DEĞİL). pf'yi
    tek başına eşiğe bağlama; süre-oranını oku (2026-07 koşumu: oran~1.9, pf 1.53 → SERİ).

KARAR KAPISI (Test A — Ollama-direct, KİLİT YOK, saf model sunucusu):
  oran ~1.0 (pf ~2.0) → Ollama paralel → APP `_lock` gerçek darboğaz → lock-removal ANLAMLI.
  oran ~2.0 (pf ~1.5) → Ollama SERİLEŞTİRİYOR (OLLAMA_NUM_PARALLEL=1). app `_lock` ile İKİ SERİ
        KAPI seri bağlı → birini tek kaldırmak SIFIR kazanç → lock-removal-tek-başına ERKEN.
        İlk hamle: NUM_PARALLEL=2 + tekrar ölç; açılırsa çift-değişiklik, açılmazsa vLLM/replica.

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
# DÜZELTME (v2): barrier ikisini t=0'da salar; SERİ durumda çağrı-2 bekleyip koşar → dur2≈2·dur1
# ve pf = 3L/2L = 1.5. Yani SERİ taban pf≈1.5 (1.0 DEĞİL). Birincil sinyal ham süre-ORANI:
#   oran = max(dur)/min(dur):  ~2.0 = SERİ (bekle-sonra-çalış) · ~1.0 = PARALEL.
# pf ikincil doğrulama: ~1.5 seri, ~2.0 paralel.
pf = A["pf"]
d = sorted(A["durs"])
ratio = (d[-1] / d[0]) if d and d[0] > 0 else 99.0
serial = ratio >= 1.6            # uzun çağrı kısanın ~2 katı → biri diğerini bekledi
parallel = ratio <= 1.35 and pf >= 1.8
print(f"süre-oranı (uzun/kısa) = {ratio:.2f}  ·  pf = {pf:.2f}   [SERİ: oran~2.0/pf~1.5 · PARALEL: oran~1.0/pf~2.0]")
if serial:
    print("⚠ OLLAMA SERİLEŞTİRİYOR — biri diğerini bekliyor (bekle-sonra-çalış imzası).")
    print("  → app `_lock` ile Ollama İKİ SERİ KAPI, seri bağlı: birini tek kaldırmak SIFIR kazanç → lock-removal-tek-başına ERKEN.")
    print("  → Kök muhtemelen OLLAMA_NUM_PARALLEL=1 (ayarlanabilir kapı, mimari değil).")
    print("  → İLK HAMLE: NUM_PARALLEL=2 + Test A tekrarı. oran 2.0→1.0 (pf 1.5→2.0) çıkarsa ÇİFT değişiklik")
    print("    (config + lock-removal) anlamlı; hâlâ seri ise (MoE/VRAM) milestone = vLLM/replica.")
    if B:
        print(f"  → Test B (app): oran={sorted(B['durs'])[-1]/max(sorted(B['durs'])[0],1e-9):.2f} — app tarafı da seri (lock).")
elif parallel:
    print("✔ OLLAMA PARALELLEŞİYOR → model sunucusu tavan DEĞİL → darboğaz APP `_lock`.")
    print("  → lock-removal ANLAMLI. Sonraki iş: checkpointer connection-pool + eşzamanlılık-güvenliği tasarımı (KOD DEĞİL).")
else:
    print("~ NET DEĞİL — oran ara bölgede. N_PREDICT'i artır (daha uzun decode → contention netleşir) + tekrar ölç.")
print(f"RATIO_A={ratio:.2f} PF_A={pf:.2f} L_ms={L*1000:.0f}" + (f" PF_B={B['pf']:.2f}" if B else ""))  # bash için
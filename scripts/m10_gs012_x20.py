"""M-10/0 gs-012 ×20 CANLI TEŞHİS — app container'ında koşar (doğrudan Ollama, WebUI DEĞİL).

Tetikleyici: 16-KARBON... makalesinin yazarları ("Hakan ÖZDEMİR, Merve KÖSE" citations'da →
TR özel-ad → eski tool-call JSON "invalid character 'H'" 500'ü). ×20 koşup her koşumda kaydeder:
http kodu, latency, sonuç sınıfı, citations, yazar-doğru.

Sonuç sınıfları (istemci-gözlemli):
  CLEAN        = 200 + gerçek cevap + sources (tool-call submit_answer çalıştı)
  EXC_FALLBACK = 200 + "Sistem şu anda yanıt üretemedi" (runtime.ask exception yakaladı →
                 gateway json.loads patlaması DAHİL; bozuk tool-call JSON'un istemci-izi)
  NO_ANSWER    = 200 + "Cevap bulunamadı" (dürüst reddetme; agent bütçe içinde cevaplayamadı)
  HTTP_ERR     = 200 dışı (500 vb.)

Host tarafı (m10_gs012_x20.sh) docker logs'ta JSONDecode/parse hatasını sayıp verdict'i verir.
"""

from __future__ import annotations

import hashlib
import os
import statistics
import time

import httpx
import psycopg

from ragintel.config.settings import DbSettings

BASE = os.environ.get("KABUL_BASE", "http://localhost:8000")
TOKA = os.environ["TOKA"]
N = int(os.environ.get("N", "20"))
Q = ("16-KARBON VERGİSİ, EMİSYON TİCARET SİSTEMİ VE SINIRDA KARBON DÜZENLEMESİ başlıklı "
     "makalenin yazarları kimlerdir?")


def sha(t): return hashlib.sha256(t.encode()).hexdigest()


db = psycopg.connect(DbSettings().conninfo()); db.autocommit = True
db.execute(
    "INSERT INTO ragintel.users (user_id, api_token_hash, display_name, allowed_doc_scopes, is_admin, active, status) "
    "VALUES ('kabul10-a', %s, 'Kabul10 A', ARRAY['default'], false, true, 'active') "
    "ON CONFLICT (user_id) DO UPDATE SET api_token_hash=EXCLUDED.api_token_hash, "
    "allowed_doc_scopes=ARRAY['default'], is_admin=false, active=true, status='active'",
    (sha(TOKA),))
cli = httpx.Client(timeout=180.0)
H = {"Authorization": "Bearer " + TOKA, "Content-Type": "application/json"}


def classify(status, body):
    if status != 200:
        return "HTTP_ERR"
    ans = str(body.get("answer") or "")
    if ans.startswith("Sistem şu anda yanıt üretemedi"):
        return "EXC_FALLBACK"
    if "Cevap bulunamadı" in ans:
        return "NO_ANSWER"
    return "CLEAN" if (body.get("sources") or []) else "NO_SRC"


print(f"# gs-012 ×{N} — doğrudan Ollama; soru: {Q[:48]}…")
print(f"{'#':>3} {'http':>4} {'ms':>7}  {'sonuç':<12} {'src':>3} yazar-doğru")
rows = []
for i in range(1, N + 1):
    t0 = time.perf_counter()
    try:
        r = cli.post(f"{BASE}/api/ask", headers=H, json={"question": Q})   # her koşum TAZE session
        dt = int((time.perf_counter() - t0) * 1000)
        body = r.json(); status = r.status_code
    except Exception as exc:
        dt = int((time.perf_counter() - t0) * 1000); status = -1; body = {"answer": "", "sources": []}
        print(f"{i:>3} {'ERR':>4} {dt:>7}  exception    -   {type(exc).__name__}")
        rows.append((status, dt, "HTTP_ERR", 0, False)); continue
    outcome = classify(status, body)
    ans = str(body.get("answer") or ""); srcs = body.get("sources") or []
    authors_ok = any(k in ans for k in ("ÖZDEMİR", "KÖSE", "Hakan", "Merve"))
    rows.append((status, dt, outcome, len(srcs), authors_ok))
    print(f"{i:>3} {status:>4} {dt:>7}  {outcome:<12} {len(srcs):>3} {'✓' if authors_ok else '—'}")
    if i == 1:   # 1. koşum kanıtı
        files = ",".join(sorted({s.get("file_name", "")[:22] for s in srcs}))
        print(f"    [kanıt] cevap: {ans[:90]!r}")
        print(f"    [kanıt] kaynaklar: {files}")

# --- özet + latency (ilk proxy'siz ölçüm) ---
from collections import Counter
cnt = Counter(o for _, _, o, _, _ in rows)
lat = sorted(dt for _, dt, _, _, _ in rows)
p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
print(f"\n== SONUÇ ({N} koşum) ==")
print(f"   CLEAN={cnt['CLEAN']}  EXC_FALLBACK={cnt['EXC_FALLBACK']}  NO_ANSWER={cnt['NO_ANSWER']}  "
      f"NO_SRC={cnt['NO_SRC']}  HTTP_ERR={cnt['HTTP_ERR']}")
print(f"   yazar-doğru: {sum(1 for *_ , a in rows if a)}/{N}")
print(f"   LATENCY ms (proxy'siz, doğrudan Ollama): min={lat[0]} medyan={int(statistics.median(lat))} "
      f"ort={int(statistics.mean(lat))} p95={p95} max={lat[-1]}")
print(f"   (not: 1. koşum warm-up içerebilir → medyan/p95 daha temsili)")
print(f"CLEAN={cnt['CLEAN']} EXCFB={cnt['EXC_FALLBACK']} HTTPERR={cnt['HTTP_ERR']} N={N}")  # bash için

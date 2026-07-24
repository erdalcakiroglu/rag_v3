"""M-15 ADIM 1c — AYNI sorular HTTP `/api/ask` yolundan: API katmanı vergisi var mı?

NEDEN: `scripts/m15_latency_anatomi.py` süreç-İÇİ ölçtü → soru-başı p95=12.7s.
Ama M-10 ×20 koşumu HTTP `/api/ask` ucundan min=23.8s / medyan=27.1s görmüştü.
"P95<15s" bir ÜRÜN hedefidir → kullanıcının gördüğü yol HTTP'dir. İki yol arasında
fark varsa vergi API katmanındadır (auth + PII + session/Redis + serileştirme) ve
M-15'in gerçek hedefi orasıdır. Fark yoksa M-10'un sayısı soru/koşul farkındandır.

TASARIM: anatomi betiğiyle AYNI soru seçimi (kategori-çeşitli ilk N) — elmayla elma.
Her soru 2 kez sorulur: 1. koşum warm/cache etkisini taşır, 2. koşum temsilîdir.

KULLANIM (repo kökünden, H200):
  TOKA=$(openssl rand -hex 20) \
  docker exec -i -e TOKA="$TOKA" -e GOLDEN=v0.1 -e N=5 ragintel-api python - < scripts/m15_http_vs_inproc.py
"""
from __future__ import annotations

import hashlib
import os
import statistics
import time

import httpx
import psycopg

from ragintel.config.settings import DbSettings
from ragintel.database import Database
from ragintel.eval import repository as repo

BASE = os.environ.get("KABUL_BASE", "http://localhost:8000")
TOKA = os.environ["TOKA"]
GOLDEN = os.environ.get("GOLDEN", "v0.1")
N = int(os.environ.get("N", "5"))
USER = "m15lat-a"


def sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


admin = psycopg.connect(DbSettings().conninfo()); admin.autocommit = True
admin.execute(
    "INSERT INTO ragintel.users (user_id, api_token_hash, display_name, allowed_doc_scopes, "
    "is_admin, active, status) VALUES (%s, %s, 'M15 Latency', ARRAY['default'], false, true, 'active') "
    "ON CONFLICT (user_id) DO UPDATE SET api_token_hash=EXCLUDED.api_token_hash, "
    "allowed_doc_scopes=ARRAY['default'], is_admin=false, active=true, status='active'",
    (USER, sha(TOKA)))

db = Database(DbSettings()).open()
try:
    with db.connection() as conn:
        records = repo.list_golden_records(conn, GOLDEN)
finally:
    db.close()

picked, seen = [], set()
for r in records:                      # anatomi betiğiyle AYNI seçim kuralı
    if r["category"] not in seen:
        picked.append(r); seen.add(r["category"])
    if len(picked) >= N:
        break

cli = httpx.Client(timeout=300.0)
H = {"Authorization": "Bearer " + TOKA, "Content-Type": "application/json"}

print(f"# M-15 ADIM 1c — HTTP /api/ask yolu — golden={GOLDEN} N={len(picked)} (her soru ×2)")
print(f"{'id':<12} {'kategori':<18} {'koşum1 s':>9} {'koşum2 s':>9} {'src':>4} {'conf':<7}")
warm, cold = [], []
for rec in picked:
    lat = []
    body = {}
    for k in (1, 2):
        t0 = time.perf_counter()
        try:
            r = cli.post(f"{BASE}/api/ask", headers=H, json={"question": rec["question"]})
            dt = (time.perf_counter() - t0) * 1000.0
            body = r.json() if r.status_code == 200 else {}
            if r.status_code != 200:
                print(f"{rec['id']:<12} HTTP {r.status_code} — {r.text[:120]}")
        except Exception as exc:                       # noqa: BLE001 — teşhis betiği
            dt = (time.perf_counter() - t0) * 1000.0
            print(f"{rec['id']:<12} EXC {type(exc).__name__}: {exc}")
        lat.append(dt)
    cold.append(lat[0]); warm.append(lat[1])
    print(f"{rec['id']:<12} {rec['category']:<18} {lat[0]/1000:>9.1f} {lat[1]/1000:>9.1f} "
          f"{len(body.get('sources') or []):>4} {str(body.get('confidence') or '-'):<7}")


def p95(v):
    s = sorted(v)
    return s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]


print("\n############ HTTP vs SÜREÇ-İÇİ ############")
for tag, v in (("koşum1 (soğuk)", cold), ("koşum2 (ılık)", warm)):
    print(f"  {tag:<16} p50={statistics.median(v)/1000:5.1f}s  p95={p95(v)/1000:5.1f}s  max={max(v)/1000:5.1f}s")
print("  SÜREÇ-İÇİ (anatomi) referans: p50=11.3s p95=12.7s")
print("\n  OKUMA:")
print("  - HTTP ≈ süreç-içi  → API katmanı vergisi YOK; M-10'un 24-27s'i soru/koşul farkı.")
print("  - HTTP >> süreç-içi → vergi API katmanında (auth/PII/session/Redis) → M-15'in GERÇEK hedefi orası.")

# temizlik: geçici kullanıcı + oluşan sohbetler
convs = [r[0] for r in admin.execute(
    "SELECT conversation_id FROM ragintel.conversations WHERE user_id=%s", (USER,)).fetchall()]
for cid in convs:
    admin.execute("DELETE FROM ragintel.conversation_messages WHERE conversation_id=%s", (cid,))
admin.execute("DELETE FROM ragintel.conversations WHERE user_id=%s", (USER,))
n = admin.execute("DELETE FROM ragintel.users WHERE user_id=%s", (USER,)).rowcount
print(f"\ntemizlik OK — sohbet:{len(convs)} kullanıcı:{n}")

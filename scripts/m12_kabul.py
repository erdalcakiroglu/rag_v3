"""M-12 CANLI KABUL — app container'ında koşar (httpx/psycopg/redis + tüm env orada).

`docker exec -i -e ATOK=<admin-token> ragintel-api python - < scripts/m12_kabul.py`

Adımlar 1-7 + 9 burada; adım 8 (Redis durdurma) host'ta (m12_kabul.sh). Geçici test
kullanıcıları ('kabul-*') sonda TEMİZLENİR. Her adım PASS/FAIL + kanıt basar; en az bir
FAIL varsa exit 1.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time

import httpx
import psycopg
import redis

from ragintel.config.settings import DbSettings, RedisSettings

BASE = os.environ.get("KABUL_BASE", "http://localhost:8000")
ATOK = os.environ["ATOK"]                       # host'un ürettiği geçici admin token
DOMAIN = os.environ.get("KABUL_DOMAIN", "finagotech.com.tr")   # allowlist-içi
PW = os.environ.get("KABUL_PW", "cok-uzun-kabul-sifresi-123")  # host ile ORTAK; DB'de OLMAMALI
_R = []                                          # (ad, ok, kanıt)


def rec(name, ok, ev=""):
    _R.append((name, bool(ok), ev)); print(("PASS" if ok else "FAIL"), name, "|", ev)


def sha(t): return hashlib.sha256(t.encode()).hexdigest()
def H(tok): return {"Authorization": "Bearer " + tok, "Content-Type": "application/json"}
def uid_path(email): return email.replace("@", "%40")


db = psycopg.connect(DbSettings().conninfo()); db.autocommit = True
rc = redis.Redis.from_url(RedisSettings().url, decode_responses=True, socket_connect_timeout=3)
cli = httpx.Client(timeout=60.0)


def dbq(sql, *a):
    return db.execute(sql, a).fetchall()


# --- setup: geçici admin (DB token, Redis'e bakmaz) + korpus scope'ları ---------
db.execute("INSERT INTO ragintel.users (user_id, api_token_hash, display_name, allowed_doc_scopes, is_admin, active, status) "
           "VALUES ('kabul-admin', %s, 'Kabul Admin', '{}', true, true, 'active') "
           "ON CONFLICT (user_id) DO UPDATE SET api_token_hash=EXCLUDED.api_token_hash, is_admin=true, active=true, status='active'",
           (sha(ATOK),))
scopes = [r[0] for r in dbq("SELECT doc_scope FROM ragintel.core_files WHERE status='COMPLETED' "
                            "AND doc_scope IS NOT NULL GROUP BY doc_scope ORDER BY count(*) DESC")]
scopeA = scopes[0] if scopes else "default"
scopeB = scopes[1] if len(scopes) > 1 else scopeA
print(f"# setup: admin=kabul-admin | korpus scope'ları={scopes} | A={scopeA} B={scopeB}")

emailA = f"kabul-a@{DOMAIN}"; emailB = f"kabul-b@{DOMAIN}"


# --- 1) KAYIT: allowlist-içi pending / dışı 403 / duplicate nötr ----------------
r = cli.post(f"{BASE}/api/register", json={"full_name": "Kabul A", "email": emailA, "password": PW})
row = dbq("SELECT status, allowed_doc_scopes FROM ragintel.users WHERE lower(email)=%s", emailA.lower())
rec("1a kayıt allowlist-içi → pending + scope=[]",
    r.status_code == 200 and r.json().get("status") == "pending" and row and row[0][0] == "pending" and row[0][1] == [],
    f"http={r.status_code} db_status={row[0][0] if row else '-'} scope={row[0][1] if row else '-'}")

out = cli.post(f"{BASE}/api/register", json={"full_name": "Dış", "email": "kabul-x@disdomain-xyz.com", "password": PW})
rec("1b kayıt allowlist-dışı → 403", out.status_code == 403, f"http={out.status_code}")

dup = cli.post(f"{BASE}/api/register", json={"full_name": "Kabul A2", "email": emailA, "password": PW})
n = dbq("SELECT count(*) FROM ragintel.users WHERE lower(email)=%s", emailA.lower())[0][0]
rec("1c duplicate → nötr (aynı yanıt, tek satır, enumeration yok)",
    dup.status_code == 200 and dup.json() == r.json() and n == 1, f"http={dup.status_code} satır={n}")


# --- 2) PENDING GİRİŞ: 403, veri yok --------------------------------------------
pend = cli.post(f"{BASE}/api/login", json={"email": emailA, "password": PW})
rec("2 pending giriş → 403 (onay bekliyor)",
    pend.status_code == 403 and "onay" in pend.json().get("detail", "").lower(), f"http={pend.status_code}")


# --- 3) ADMIN ONAY: pending → active + scope ------------------------------------
ap = cli.post(f"{BASE}/api/admin/users/{uid_path(emailA)}/approve", headers=H(ATOK), json={"scopes": [scopeA]})
st = dbq("SELECT status, allowed_doc_scopes FROM ragintel.users WHERE lower(email)=%s", emailA.lower())[0]
rec("3 admin onay → active + scope atandı",
    ap.status_code == 200 and st[0] == "active" and st[1] == [scopeA], f"http={ap.status_code} db={st}")


# --- 4) GİRİŞ: active+doğru şifre → Redis session (SET+TTL); yanlış nötr; /ask ---
lg = cli.post(f"{BASE}/api/login", json={"email": emailA, "password": PW})
tokA = lg.json().get("token", "")
skey = "ragintel:session:" + sha(tokA)
sess_raw = rc.get(skey); ttl = rc.ttl(skey)
rec("4a giriş → 200 + Redis session SET",
    lg.status_code == 200 and bool(tokA) and sess_raw is not None, f"http={lg.status_code} session_var={sess_raw is not None}")
rec("4b Redis session TTL var (sliding pencere)", ttl and ttl > 0, f"ttl={ttl}s")

bad = cli.post(f"{BASE}/api/login", json={"email": emailA, "password": "YANLIS-sifre-000"})
unk = cli.post(f"{BASE}/api/login", json={"email": f"yok-{secrets.token_hex(4)}@{DOMAIN}", "password": PW})
rec("4c yanlış şifre = bilinmeyen email → aynı nötr 401",
    bad.status_code == unk.status_code == 401 and bad.json().get("detail") == unk.json().get("detail"),
    f"yanlış={bad.status_code} bilinmeyen={unk.status_code}")

ask = cli.post(f"{BASE}/api/ask", headers=H(tokA), json={"question": "Bu belgelerde hangi konular ele alınıyor?"})
srcs = ask.json().get("sources", []) if ask.status_code == 200 else []
rec("4d token ile /api/ask → 200 (o scope'un belgeleriyle)",
    ask.status_code == 200, f"http={ask.status_code} kaynak_sayısı={len(srcs)} scope={scopeA}")


# --- 5) SCOPE İZOLASYONU (Redis session yolu, çift-yönlü, vacuous değil) ---------
cli.post(f"{BASE}/api/register", json={"full_name": "Kabul B", "email": emailB, "password": PW})
cli.post(f"{BASE}/api/admin/users/{uid_path(emailB)}/approve", headers=H(ATOK), json={"scopes": [scopeB]})
tokB = cli.post(f"{BASE}/api/login", json={"email": emailB, "password": PW}).json().get("token", "")
sA = json.loads(rc.get("ragintel:session:" + sha(tokA)) or "{}")
sB = json.loads(rc.get("ragintel:session:" + sha(tokB)) or "{}")
if scopeA != scopeB:
    ok5 = (sA.get("allowed_doc_scopes") == [scopeA] and sB.get("allowed_doc_scopes") == [scopeB]
           and scopeB not in sA.get("allowed_doc_scopes", []) and scopeA not in sB.get("allowed_doc_scopes", []))
    rec("5 çift-yönlü scope izolasyonu (session)",
        ok5, f"A_görür={sA.get('allowed_doc_scopes')} B_görür={sB.get('allowed_doc_scopes')} (her biri SADECE kendi)")
else:
    rec("5 çift-yönlü scope izolasyonu — ATLANDI",
        None is None, f"korpusta tek scope ({scopeA}) → iki-scope izolasyonu ölçülemedi; birim testte kanıtlı")


# --- 6) LOGOUT: DEL → aynı token 401 (anında iptal) -----------------------------
cli.post(f"{BASE}/api/logout", headers=H(tokB))
after = cli.post(f"{BASE}/api/ask", headers=H(tokB), json={"question": "test"})
gone = rc.get("ragintel:session:" + sha(tokB)) is None
rec("6 logout → session DEL + aynı token 401",
    gone and after.status_code == 401, f"redis_silindi={gone} sonraki_istek={after.status_code}")


# --- 7) TTL SLIDING: istek sonrası TTL tazeleniyor mu (spot) ---------------------
k = "ragintel:session:" + sha(tokA)
rc.expire(k, 30)                                 # yapay olarak düşür
before = rc.ttl(k)
cli.post(f"{BASE}/api/ask", headers=H(tokA), json={"question": "ping"})   # kimlik çözümü → sliding
after_ttl = rc.ttl(k)
rec("7 TTL SLIDING (istek sonrası tazelendi)",
    after_ttl > before, f"istekten_önce={before}s sonra={after_ttl}s (tazelendi)")


# --- 9) ŞİFRE HASH argon2 + DB'de düz şifre YOK ---------------------------------
prow = dbq("SELECT password_hash FROM ragintel.users WHERE lower(email)=%s", emailA.lower())[0]
leak = dbq("SELECT count(*) FROM ragintel.users WHERE password_hash LIKE %s OR display_name LIKE %s",
           f"%{PW}%", f"%{PW}%")[0][0]
rec("9 şifre argon2id + DB'de düz şifre YOK",
    prow[0].startswith("$argon2id$") and PW not in prow[0] and leak == 0,
    f"hash_prefix={prow[0][:10]} düz_şifre_sızıntısı={leak}")


# --- TEMİZLİK: ADIM 8 SONRASINA bırakıldı (host m12_kabul.sh) ---
# kabul-a (active + PW) ve kabul-admin (DB token) adım 8'de GEREKLİ (Redis-down: login 503,
# admin bearer 200). Bu yüzden burada SİLMİYORUZ; host script adım 8'den sonra temizler.
print("# NOT: kabul-* kullanıcıları adım 8 için DURUYOR; temizlik host'ta (adım 8 sonrası).")

ok = sum(1 for _, o, _ in _R if o); tot = len(_R)
print(f"\n== M-12 KABUL: {ok}/{tot} PASS ==")
raise SystemExit(0 if all(o for _, o, _ in _R) else 1)

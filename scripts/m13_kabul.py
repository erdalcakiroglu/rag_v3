"""M-13 CANLI KABUL — app container'ında koşar (httpx/psycopg + tüm env orada).

Adım 1-4 + 6 burada; adım 5 (Redis durdurma) host'ta (m13_kabul.sh). Geçici 'kabul13-*'
kullanıcı/sohbetleri host tarafında temizlenir. Kullanıcılar DB TOKEN'lı (Redis login DEĞİL)
→ auth Redis'ten bağımsız (adım 5 kanıtı). Son satır: CONV1=<id> (bash adım 5 için).
"""

from __future__ import annotations

import hashlib
import os

import httpx
import psycopg

from ragintel.config.settings import DbSettings

BASE = os.environ.get("KABUL_BASE", "http://localhost:8000")
TOKA, TOKB, TOKADMIN = os.environ["TOKA"], os.environ["TOKB"], os.environ["TOKADMIN"]
_R = []


def rec(name, ok, ev=""):
    _R.append((name, bool(ok))); print(("PASS" if ok else "FAIL"), name, "|", ev)


def sha(t): return hashlib.sha256(t.encode()).hexdigest()
def H(tok): return {"Authorization": "Bearer " + tok, "Content-Type": "application/json"}


db = psycopg.connect(DbSettings().conninfo()); db.autocommit = True
def dbq(sql, *a): return db.execute(sql, a).fetchall()
cli = httpx.Client(timeout=90.0)


# --- setup: 3 DB-token'lı kullanıcı (a: geniş scope, b: dar, admin) ------------
def mint(uid, tok, scopes, is_admin):
    db.execute(
        "INSERT INTO ragintel.users (user_id, api_token_hash, display_name, allowed_doc_scopes, is_admin, active, status) "
        "VALUES (%s,%s,%s,%s,%s,true,'active') "
        "ON CONFLICT (user_id) DO UPDATE SET api_token_hash=EXCLUDED.api_token_hash, "
        "allowed_doc_scopes=EXCLUDED.allowed_doc_scopes, is_admin=EXCLUDED.is_admin, active=true, status='active'",
        (uid, sha(tok), uid, scopes, is_admin))

mint("kabul13-a", TOKA, ["default", "envanter"], False)
mint("kabul13-b", TOKB, ["default"], False)
mint("kabul13-admin", TOKADMIN, [], True)
env_tid = dbq("SELECT t.table_id FROM ragintel.core_tables t JOIN ragintel.core_files f "
              "ON t.file_id=f.file_id WHERE f.doc_scope='envanter' LIMIT 1")
env_tid = env_tid[0][0] if env_tid else None
print(f"# setup: 3 kullanıcı mint | envanter table_id={env_tid}")


# --- 1) Yeni soru → conversation oluşur ----------------------------------------
Q1 = "Karbon vergisini ilk uygulayan ülke hangisidir ve hangi yıl başlamıştır ayrıntılı açıkla"
r1 = cli.post(f"{BASE}/api/ask", headers=H(TOKA), json={"question": Q1})
conv1 = r1.headers.get("X-Session-Id", "")
row = dbq("SELECT user_id, title, allowed_doc_scopes FROM ragintel.conversations WHERE conversation_id=%s", conv1)
row = row[0] if row else (None, None, None)
rec("1 yeni soru → conversation (title=ilk soru, user_id, scope-snapshot)",
    r1.status_code == 200 and bool(conv1) and row[0] == "kabul13-a"
    and Q1[:20] in (row[1] or "") and row[2] == ["default", "envanter"],
    f"http={r1.status_code} conv={conv1[:18]} user={row[0]} snapshot={row[2]}")


# --- 2) /api/conversations → yalnız kendi listesi -------------------------------
la = cli.get(f"{BASE}/api/conversations", headers=H(TOKA)).json()["conversations"]
lb = cli.get(f"{BASE}/api/conversations", headers=H(TOKB)).json()["conversations"]
a_ids = {c["conversation_id"] for c in la}; b_ids = {c["conversation_id"] for c in lb}
rec("2 liste yalnız kendi sohbetleri (çapraz görünmez)",
    conv1 in a_ids and conv1 not in b_ids, f"A_görür_conv1={conv1 in a_ids} B_görür_conv1={conv1 in b_ids}")


# --- 3) get{id} → transcript; çapraz → 404 -------------------------------------
own = cli.get(f"{BASE}/api/conversations/{conv1}", headers=H(TOKA))
cross = cli.get(f"{BASE}/api/conversations/{conv1}", headers=H(TOKB))
msgs = own.json().get("messages", []) if own.status_code == 200 else []
rec("3 transcript yüklenir (Q/A); çapraz erişim → 404 (sahiplik)",
    own.status_code == 200 and len(msgs) == 2 and [m["role"] for m in msgs] == ["user", "assistant"]
    and cross.status_code == 404,
    f"own={own.status_code}({len(msgs)} msg) cross={cross.status_code}")


# --- 4) delete → soft-delete (liste'de yok AMA satır DB'de durur) --------------
rdel = cli.post(f"{BASE}/api/ask", headers=H(TOKA), json={"question": "Silinecek sohbet sorusu"})
conv_del = rdel.headers.get("X-Session-Id", "")
d = cli.delete(f"{BASE}/api/conversations/{conv_del}", headers=H(TOKA))
after = {c["conversation_id"] for c in cli.get(f"{BASE}/api/conversations", headers=H(TOKA)).json()["conversations"]}
drow = dbq("SELECT deleted_at IS NOT NULL FROM ragintel.conversations WHERE conversation_id=%s", conv_del)
dmsg = dbq("SELECT count(*) FROM ragintel.conversation_messages WHERE conversation_id=%s", conv_del)[0][0]
rec("4 soft-delete: liste'de yok AMA satır+mesaj DB'de durur (deleted_at dolu)",
    d.status_code == 200 and conv_del not in after and drow and drow[0][0] is True and dmsg == 2,
    f"http={d.status_code} listede={conv_del in after} deleted_at_dolu={drow and drow[0][0]} mesaj={dmsg}")


# --- 6) ZOMBİ-YETKİ: scope daralt → snapshot geniş görünse de canlı scope sınırlar
if env_tid is None:
    rec("6 zombi-yetki — ATLANDI (envanter tablo yok)", True, "corpus'ta envanter tablo bulunamadı")
else:
    pre = cli.get(f"{BASE}/api/table/{env_tid}", headers=H(TOKA))          # geniş scope → 200
    cli.post(f"{BASE}/api/admin/users/kabul13-a/scopes", headers=H(TOKADMIN), json={"scopes": ["default"]})
    snap = dbq("SELECT allowed_doc_scopes FROM ragintel.conversations WHERE conversation_id=%s", conv1)[0][0]
    live = dbq("SELECT allowed_doc_scopes FROM ragintel.users WHERE user_id='kabul13-a'")[0][0]
    post = cli.get(f"{BASE}/api/table/{env_tid}", headers=H(TOKA))          # daralmış → 404 (fail-closed)
    rec("6 zombi-yetki: snapshot GENİŞ kalsa da canlı scope daralınca envanter erişimi KAPANIR",
        pre.status_code == 200 and snap == ["default", "envanter"] and live == ["default"]
        and post.status_code == 404,
        f"daraltma_öncesi={pre.status_code} snapshot={snap} canlı_scope={live} daraltma_sonrası={post.status_code}")


ok = sum(1 for _, o in _R if o)
print(f"\n== M-13 KABUL (1-4,6): {ok}/{len(_R)} PASS ==")
print(f"CONV1={conv1}")     # bash adım 5 (Redis-down) bu sohbeti kullanır
print("# NOT: kabul13-* kullanıcı/sohbetleri adım 5 için DURUYOR; temizlik host'ta.")

#!/usr/bin/env bash
# M-13 CANLI KABUL — H200'de (repo kökünden): ./scripts/m13_kabul.sh
# Adım 1-4,6 → container-içi python. Adım 5 (Redis durdurma) + temizlik → host.
set -uo pipefail
cd "$(dirname "$0")/.."

API="http://localhost:8000"
TOKA="$(openssl rand -hex 20)"; TOKB="$(openssl rand -hex 20)"; TOKADMIN="$(openssl rand -hex 20)"
FAIL=0
chk(){ if [ "$2" = "$3" ]; then echo "PASS $1 | $2"; else echo "FAIL $1 | got=$2 beklenen=$3"; FAIL=1; fi; }

echo "############ M-13 CANLI KABUL ############"
echo "==> Adım 1-4,6 (container-içi)"
OUT="$(docker exec -i -e TOKA="$TOKA" -e TOKB="$TOKB" -e TOKADMIN="$TOKADMIN" -e KABUL_BASE="$API" \
      ragintel-api python - < scripts/m13_kabul.py)" || FAIL=1
echo "$OUT"
echo "$OUT" | grep -q "== M-13 KABUL (1-4,6):" && ! echo "$OUT" | grep -q "^FAIL " || FAIL=1
CONV1="$(echo "$OUT" | grep -oE 'CONV1=[^ ]+' | head -1 | cut -d= -f2)"

echo ""
echo "==> Adım 5: REDIS-DOWN'da geçmiş ERİŞİLİR (Postgres kalıcılık, DB-token auth)"
docker stop ragintel-redis >/dev/null; sleep 2
LST=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$API/api/conversations" -H "Authorization: Bearer $TOKA")
GET=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$API/api/conversations/$CONV1" -H "Authorization: Bearer $TOKA")
docker start ragintel-redis >/dev/null
chk "5a Redis-down: /api/conversations → 200"          "$LST" "200"
chk "5b Redis-down: /api/conversations/{id} → 200"     "$GET" "200"

echo ""
echo "==> Redis geri (health)"
for i in $(seq 1 12); do
  R=$(curl -s --max-time 10 "$API/api/health" | grep -o '"redis":"[a-z]*"'); [ "$R" = '"redis":"ok"' ] && break; sleep 3
done
chk "5c Redis geri → redis ok"                          "$R" '"redis":"ok"'

echo ""
echo "==> Temizlik: geçici kabul13-* kullanıcı/sohbet/mesaj"
docker exec -i ragintel-api python - <<'PY'
import psycopg
from ragintel.config.settings import DbSettings
c = psycopg.connect(DbSettings().conninfo()); c.autocommit = True
convs = [r[0] for r in c.execute("SELECT conversation_id FROM ragintel.conversations WHERE user_id LIKE %s", ("kabul13-%",)).fetchall()]
for cid in convs:
    c.execute("DELETE FROM ragintel.conversation_messages WHERE conversation_id = %s", (cid,))
c.execute("DELETE FROM ragintel.conversations WHERE user_id LIKE %s", ("kabul13-%",))
n = c.execute("DELETE FROM ragintel.users WHERE user_id LIKE %s", ("kabul13-%",)).rowcount
print(f"temizlik OK — sohbet:{len(convs)} kullanıcı:{n}")
PY

echo ""
if [ "$FAIL" -eq 0 ]; then echo "############ M-13 KABUL: TÜM ADIMLAR PASS ############"; else echo "############ M-13 KABUL: EN AZ BİR FAIL ############"; fi
exit $FAIL

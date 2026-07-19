#!/usr/bin/env bash
# M-12 CANLI KABUL — H200'de çalıştırın (repo kökünden): ./scripts/m12_kabul.sh
#
# Adım 1-7,9 → container-içi python (m12_kabul.py). Adım 8 (Redis durdurma) + log-scrub
# + temizlik → burada (host: docker gerekli). Geçici 'kabul-*' kullanıcıları SONDA silinir.
set -uo pipefail
cd "$(dirname "$0")/.."

API="http://localhost:8000"
DOMAIN="finagotech.com.tr"          # allowlist-içi (panelde ekli)
PW="cok-uzun-kabul-sifresi-123"
ATOK="$(openssl rand -hex 20)"      # geçici admin DB token (python mint eder)
EMAILA="kabul-a@${DOMAIN}"
FAIL=0

echo "############ M-12 CANLI KABUL ############"
echo "==> Adım 1-7,9 (container-içi)"
docker exec -i -e ATOK="$ATOK" -e KABUL_BASE="$API" -e KABUL_DOMAIN="$DOMAIN" -e KABUL_PW="$PW" \
  ragintel-api python - < scripts/m12_kabul.py || FAIL=1

echo ""
echo "==> Adım 8: REDIS-DOWN tatbikatı (login 503 + health degraded + admin BEARER 200)"
docker stop ragintel-redis >/dev/null; sleep 2
LOGIN=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X POST "$API/api/login" \
  -H 'Content-Type: application/json' -d "{\"email\":\"$EMAILA\",\"password\":\"$PW\"}")
HEALTH=$(curl -s --max-time 15 "$API/api/health")
ADMIN=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$API/api/admin/users" -H "Authorization: Bearer $ATOK")
HSTAT=$(echo "$HEALTH" | grep -o '"status":"[a-z]*"'); HRED=$(echo "$HEALTH" | grep -o '"redis":"[a-z]*"')
docker start ragintel-redis >/dev/null

chk(){ if [ "$2" = "$3" ]; then echo "PASS $1 | $2"; else echo "FAIL $1 | got=$2 beklenen=$3"; FAIL=1; fi; }
chk "8a Redis-down: yeni login → 503"        "$LOGIN" "503"
chk "8b Redis-down: health status degraded"  "$HSTAT" '"status":"degraded"'
chk "8c Redis-down: health redis down"        "$HRED"  '"redis":"down"'
chk "8d Redis-down: admin BEARER (DB) → 200"  "$ADMIN" "200"

echo ""
echo "==> Log-scrub: düz şifre app loglarında GEÇMEMELİ"
LOGHITS=$(docker logs ragintel-api 2>&1 | grep -c "$PW" || true)
chk "9b düz şifre logda YOK"                  "$LOGHITS" "0"

echo ""
echo "==> Redis geri geldi mi (health)"
for i in $(seq 1 12); do
  R=$(curl -s --max-time 10 "$API/api/health" | grep -o '"redis":"[a-z]*"')
  [ "$R" = '"redis":"ok"' ] && break; sleep 3
done
chk "8e Redis geri → redis ok"                "$R" '"redis":"ok"'

echo ""
echo "==> Temizlik: geçici kabul-* kullanıcıları + session'ları"
docker exec -i ragintel-api python - <<'PY'
import redis, psycopg
from ragintel.config.settings import RedisSettings, DbSettings
r = redis.Redis.from_url(RedisSettings().url, decode_responses=True)
for uid in ("kabul-a@finagotech.com.tr", "kabul-b@finagotech.com.tr"):
    for h in (r.smembers("ragintel:usess:" + uid) or []):
        r.delete("ragintel:session:" + h)
    r.delete("ragintel:usess:" + uid)
c = psycopg.connect(DbSettings().conninfo()); c.autocommit = True
# psycopg3 SQL'de literal % 'yi placeholder sanar → LIKE deseni PARAM olarak geçilir.
n = c.execute("DELETE FROM ragintel.users WHERE user_id LIKE %s", ("kabul-%",)).rowcount
print(f"temizlik OK — silinen kullanıcı: {n}")
PY

echo ""
if [ "$FAIL" -eq 0 ]; then echo "############ M-12 KABUL: TÜM ADIMLAR PASS ############"; else echo "############ M-12 KABUL: EN AZ BİR FAIL (yukarı bakın) ############"; fi
exit $FAIL

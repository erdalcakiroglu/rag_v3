#!/usr/bin/env bash
# M-10/0 gs-012 ×20 CANLI TEŞHİS — H200'de (repo kökünden): ./scripts/m10_gs012_x20.sh
# Python core (×20 ask) + host: docker logs'ta tool-call JSON parse hatası sayımı + verdict + temizlik.
set -uo pipefail
cd "$(dirname "$0")/.."

API="http://localhost:8000"
TOKA="$(openssl rand -hex 20)"
N="${1:-20}"

echo "############ M-10/0 gs-012 ×${N} CANLI TEŞHİS (doğrudan Ollama) ############"
echo "==> health (koşu öncesi)"
curl -s --max-time 10 "$API/api/health" | grep -o '"status":"[a-z]*"' || true

START="$(date '+%Y-%m-%dT%H:%M:%S')"; sleep 1
echo "==> ${N} koşum başlıyor (her biri agent+Ollama → dakikalar sürebilir)…"
OUT="$(docker exec -i -e TOKA="$TOKA" -e KABUL_BASE="$API" -e N="$N" ragintel-api python - < scripts/m10_gs012_x20.py)"
echo "$OUT"

echo ""
echo "==> Sunucu logları: tool-call JSON parse / exception (gateway json.loads patlaması)"
LOGS="$(docker logs --since "$START" ragintel-api 2>&1)"
PARSE_ERR="$(echo "$LOGS" | grep -icE 'JSONDecode|invalid character|failed to parse JSON' || true)"
ASK_FAIL="$(echo "$LOGS" | grep -icE 'api_ask_failed' || true)"
echo "   JSONDecode/parse-hata log satırı : $PARSE_ERR"
echo "   api_ask_failed log satırı         : $ASK_FAIL"
if [ "$PARSE_ERR" -gt 0 ] || [ "$ASK_FAIL" -gt 0 ]; then
  echo "   --- örnek hatalı log satırları ---"
  echo "$LOGS" | grep -iE 'JSONDecode|invalid character|failed to parse JSON|api_ask_failed' | head -4
fi

# python özet satırı: CLEAN=.. EXCFB=.. HTTPERR=.. N=..
CLEAN="$(echo "$OUT" | grep -oE 'CLEAN=[0-9]+ EXCFB' | grep -oE '[0-9]+' | head -1)"
EXCFB="$(echo "$OUT" | grep -oE 'EXCFB=[0-9]+' | grep -oE '[0-9]+' | head -1)"
HTTPERR="$(echo "$OUT" | grep -oE 'HTTPERR=[0-9]+' | grep -oE '[0-9]+' | head -1)"

echo ""
echo "==> Temizlik: geçici kabul10-* (kullanıcı + oluşan sohbetler + mesajlar)"
docker exec -i ragintel-api python - <<'PY'
import psycopg
from ragintel.config.settings import DbSettings
c=psycopg.connect(DbSettings().conninfo()); c.autocommit=True
convs=[r[0] for r in c.execute("SELECT conversation_id FROM ragintel.conversations WHERE user_id LIKE %s",("kabul10-%",)).fetchall()]
for cid in convs: c.execute("DELETE FROM ragintel.conversation_messages WHERE conversation_id=%s",(cid,))
c.execute("DELETE FROM ragintel.conversations WHERE user_id LIKE %s",("kabul10-%",))
n=c.execute("DELETE FROM ragintel.users WHERE user_id LIKE %s",("kabul10-%",)).rowcount
print(f"temizlik OK — sohbet:{len(convs)} kullanıcı:{n}")
PY

echo ""
echo "############ VERDICT ############"
if [ "${CLEAN:-0}" = "$N" ] && [ "$PARSE_ERR" -eq 0 ] && [ "$ASK_FAIL" -eq 0 ]; then
  echo "✅ ${N}/${N} TEMİZ (CLEAN=$CLEAN, JSON-parse-hata=0) → suçlu WebUI'ydi (doğrudan-Ollama taşıması ÇÖZDÜ)."
  echo "   → M-9.1 AÇILIR."
else
  echo "⚠ HÂLÂ KIRIK — CLEAN=$CLEAN/$N, EXC_FALLBACK=$EXCFB, HTTP_ERR=$HTTPERR, log JSON-parse-hata=$PARSE_ERR, api_ask_failed=$ASK_FAIL."
  echo "   → suçlu Ollama/model. Ollama sürüm + qwen3.6 denemesi AYRI KARAR (Erdal'a gelir)."
fi
echo "   (Latency ve koşum tablosu yukarıda — ilk proxy'siz ölçüm.)"

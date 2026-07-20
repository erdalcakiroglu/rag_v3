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
echo "==> Sunucu logları: tool-call parse hatası + retry ateşleme"
LOGS="$(docker logs --since "$START" ragintel-api 2>&1)"
PARSE_ERR="$(echo "$LOGS" | grep -icE 'JSONDecode|invalid character|failed to parse JSON' || true)"
RETRY_FIRED="$(echo "$LOGS" | grep -icE 'tool_call_parse_error' || true)"    # (3) retry ateşledi
ASK_FAIL="$(echo "$LOGS" | grep -icE 'api_ask_failed' || true)"
echo "   parse-hata (JSONDecode/failed to parse)   : $PARSE_ERR"
echo "   tool_call_parse_error (retry ateşledi)    : $RETRY_FIRED   ← model bozuk JSON üretti, retry devreye girdi"
echo "   api_ask_failed (retry TÜKENDİ → fallback) : $ASK_FAIL"
if [ "$PARSE_ERR" -gt 0 ] || [ "$RETRY_FIRED" -gt 0 ] || [ "$ASK_FAIL" -gt 0 ]; then
  echo "   --- örnek log satırları ---"
  echo "$LOGS" | grep -iE 'tool_call_parse_error|JSONDecode|invalid character|failed to parse JSON|api_ask_failed' | head -5
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
if [ "$RETRY_FIRED" -eq 0 ] && [ "$PARSE_ERR" -eq 0 ] && [ "${CLEAN:-0}" = "$N" ]; then
  echo "✅ ${N}/${N} TEMİZ + parse-hata=0 → model bozuk JSON HİÇ üretmedi → suçlu WebUI'ydi (taşıma ÇÖZDÜ)."
  echo "   → M-9.1 AÇILIR."
elif [ "${CLEAN:-0}" = "$N" ] && [ "$ASK_FAIL" -eq 0 ]; then
  echo "🟡 ${N}/${N} CLEAN AMA retry ateşledi ($RETRY_FIRED kez) → model HÂLÂ bozuk JSON üretiyor,"
  echo "   (3) RETRY hepsini KURTARDI (fallback=0). Sistem güvenilir → M-9.1 açılabilir; ANCAK kök=model."
  echo "   → qwen3.6 / grammar-constrained iyileştirmesi hâlâ değerli (veriyle karar)."
else
  echo "⚠ KIRIK — CLEAN=$CLEAN/$N, EXC_FALLBACK=$EXCFB (retry TÜKENDİ), retry_ateşleme=$RETRY_FIRED."
  echo "   → suçlu Ollama/model; retry yetmiyor. qwen3.6 denemesi + Ollama grammar KARARI (Erdal'a)."
fi
echo "   (Latency + koşum tablosu yukarıda — ilk proxy'siz ölçüm; retry_ateşleme = ham model kusuru göstergesi.)"

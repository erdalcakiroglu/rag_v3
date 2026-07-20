#!/usr/bin/env bash
# (C) grammar PoC orkestratörü — H200 host'ta koşar, çekirdeği app container'ında çalıştırır.
# Grammar-constrained (Ollama `format`) submit_answer şemasını temp=0'da ×N test eder.
# Kullanım:  ./scripts/c_grammar_poc.sh [MODEL] [N]     (varsayılan: qwen3.5:35b 20)
set -euo pipefail

MODEL="${1:-qwen3.5:35b}"
N="${2:-20}"
CT="ragintel-api"

echo "############ (C) STRUCTURED-OUTPUTS / GRAMMAR PoC ############"
echo "==> model=${MODEL}  N=${N}  container=${CT}"
echo "==> health (koşu öncesi)"
docker exec -i "$CT" sh -c 'curl -s localhost:8000/health' | grep -o '"status":"[^"]*"' || true
echo "==> grammar-constrained submit_answer ×${N} (temp=0; dakikalar sürebilir)…"

docker exec -i "$CT" env POC_MODEL="$MODEL" N="$N" python - < scripts/c_grammar_poc.py

echo
echo "############ NOT ############"
echo "VALID=${N}/${N} → (C) full rework GREENLIGHT (submit_answer'ı format-content'e taşı)."
echo "VALID<${N}       → grammar yetmiyor; KOD YAZMADAN pivot (mimara gider)."

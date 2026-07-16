#!/usr/bin/env bash
# ragintel-api — Seviye A dağıtım: pull → build → up (M-10/0).
#
# H200 ÜZERİNDE koşar. Basit ve tekrarlanabilir; sihir yok.
#   ./deploy.sh            → pull + build + up + health doğrulama
#   ./deploy.sh --no-pull  → yerel kodla build (git'e dokunma)
#
# Neden `set -euo pipefail`: yarım dağıtım, çalışan eski sürümden KÖTÜDÜR.
# Herhangi bir adım düşerse burada duruyoruz.
set -euo pipefail

COMPOSE_FILE="docker-compose.h200.yml"
HEALTH_URL="http://localhost:8000/api/health"
PULL=1
[[ "${1:-}" == "--no-pull" ]] && PULL=0

cd "$(dirname "$0")"

# --- ön koşul: sırlar dosyası ---
if [[ ! -f .env.h200 ]]; then
  echo "HATA: .env.h200 yok. Sırlar (DB parolası, Langfuse) burada olmalı." >&2
  echo "      Şablon: docs/M10_Deploy_Kurulum.md §2" >&2
  exit 1
fi

# --- 1) pull ---
if [[ $PULL -eq 1 ]]; then
  echo "==> git pull"
  git pull --ff-only
fi

GIT_SHA="$(git rev-parse --short HEAD)"
export GIT_SHA
echo "==> dağıtılan sürüm: $GIT_SHA"

# --- 2) build ---
# GIT_SHA imaja gömülür → /api/health hangi kodun koştuğunu SÖYLER.
# Build içinde doğrulama var (torch/docling yok + tokenizer offline) → kırıksa
# burada patlar, üretimde değil.
echo "==> build"
docker compose -f "$COMPOSE_FILE" build --build-arg "GIT_SHA=$GIT_SHA"

# --- 3) up ---
echo "==> up"
docker compose -f "$COMPOSE_FILE" up -d

# --- 4) health doğrulama (dağıtım BİTTİ demeden önce KANITLA) ---
echo "==> health bekleniyor (en çok 180 sn)"
for i in $(seq 1 36); do
  if out="$(curl -fsS --max-time 10 "$HEALTH_URL" 2>/dev/null)"; then
    echo "$out"
    # Koşan kod, dağıttığımız kod mu? (yanlış imaj/cache sessizce eski sürüm sunar)
    if ! grep -q "\"git_sha\":\"$GIT_SHA\"" <<<"$out"; then
      echo "HATA: /api/health git_sha beklenen '$GIT_SHA' DEĞİL — eski imaj mı koşuyor?" >&2
      exit 1
    fi
    if grep -q '"status":"unhealthy"' <<<"$out"; then
      echo "HATA: health unhealthy — checks'e bakın (db/ollama/tei)." >&2
      exit 1
    fi
    echo "==> DAĞITIM TAMAM ($GIT_SHA)"
    exit 0
  fi
  sleep 5
done

echo "HATA: health 180 sn'de yanıt vermedi. Log:" >&2
docker compose -f "$COMPOSE_FILE" logs --tail 40 ragintel-api >&2
exit 1

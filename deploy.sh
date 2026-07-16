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

# `sh deploy.sh` ile çağrılırsa bash'e özgü sözdizimi (diziler, [[ ]]) sessizce
# ya da anlaşılmaz biçimde kırılır. Shebang'i kullanmayı ZORUNLU kıl.
if [ -z "${BASH_VERSION:-}" ]; then
  echo "HATA: bash gerekli. './deploy.sh' olarak çalıştırın — 'sh deploy.sh' DEĞİL." >&2
  exit 1
fi

COMPOSE_FILE="docker-compose.h200.yml"
HEALTH_URL="http://localhost:8000/api/health"
PULL=1
[[ "${1:-}" == "--no-pull" ]] && PULL=0

cd "$(dirname "$0")"

# --- ön koşul: sırlar dosyası ---
if [[ ! -f .env.h200 ]]; then
  echo "HATA: .env.h200 yok. DB bağlantısı ve sırlar burada olmalı." >&2
  echo "      Şablon: docs/M10_Deploy_Kurulum.md §2" >&2
  exit 1
fi

# --- ön koşul: ZORUNLU bootstrap değerleri DOLU mu? ---
# NEDEN BURADA: eksikse konteyner açılışta MissingBootstrapSetting ile ölür, biz de
# 180 sn health bekleyip "yanıt vermedi" deriz — gerçek sebep gizlenir. (H200'de
# yaşandı.) Adıyla söyleyen hızlı hata > sessiz zaman aşımı.
#
# Liste = `settings.DbSettings.conninfo()` içinde `_require` edilenler. Ayrışmasın
# diye tests/test_faz_m10_build_zemini.py bunu KODLA karşılaştırır.
# PORT/NAME/SCHEMA burada YOK: kod varsayılanları var, zorunlu değiller — zorunluymuş
# gibi davranmak config yalanı olurdu.
ZORUNLU_SIRLAR=(RAGINTEL_DB_HOST RAGINTEL_DB_USER RAGINTEL_DB_PASSWORD)

echo "==> .env.h200 ön-doğrulama"
eksik=0
for degisken in "${ZORUNLU_SIRLAR[@]}"; do
  # Değerin KENDİSİ asla basılmaz — yalnızca SET/EKSİK durumu (sır sızdırma yasak).
  if grep -qE "^[[:space:]]*${degisken}=.+" .env.h200; then
    echo "    ${degisken}: SET"
  else
    echo "    ${degisken}: EKSİK" >&2
    eksik=1
  fi
done
if [[ $eksik -eq 1 ]]; then
  echo "HATA: .env.h200 eksik — konteyner açılışta ölürdü." >&2
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
# --force-recreate: compose, konteyneri "Started" deyip AYNI env/imajla yeniden
# başlatabiliyor; her dağıtımda TAZE konteyner istiyoruz (env veya imaj değişmiş
# olabilir). --remove-orphans: adı değişmiş eski servisler artakalmasın.
#
# `--env-file` BİLEREK YOK: o bayrak compose'un KENDİ `${...}` interpolasyonunu
# besler, konteynerin ortamını DEĞİL. Konteynere geçiş `env_file:` anahtarıyla olur
# (compose dosyasında zaten var); GIT_SHA ise yukarıda export edildi. Eklemek,
# "bir şey çözüyormuş" izlenimi veren ölü bir bayrak olurdu.
echo "==> up"
docker compose -f "$COMPOSE_FILE" up -d --force-recreate --remove-orphans

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

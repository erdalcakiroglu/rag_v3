#!/usr/bin/env bash
# scripts/run_tests_h200.sh — ragintel DAVRANIŞ birim testlerini H200'de, üretim-API imajı
# içinde tekrarlanabilir biçimde koşar.
#
# NEDEN BU SARMALAYICI:
#   Claude'un H200 erişimi yok; testler burada koşulur (bkz. memory: iki-veritabani-dev-vs-h200).
#   Üretim-API imajı TAM SÜİT için YANLIŞ imajdır ve süiti olduğu gibi koşmak İKİ sahte-kırmızı üretir:
#     1) ingestion parse bağımlılıkları (docling/python-docx/openpyxl/pdf) imajda BİLEREK yok
#        → test_ip2_corpus vb. import/parse aşamasında düşer (ortamsal, gerçek bug değil).
#     2) repo host kullanıcısına ait, konteyner root olarak koşar → git "dubious ownership"
#        → git-contract testleri (test_faz_m10_build_zemini) sahte-kırmızı verir.
#   Bu koşucu ikisini de ele alır: git için `safe.directory /app` ekler; VARSAYILAN olarak
#   yalnız ağır-dep'siz davranış testlerini koşar (agent/retrieval/grounding/honesty — kol-2 çiti).
#
# KULLANIM:
#   ./scripts/run_tests_h200.sh                       # varsayılan davranış alt-kümesi (aşağıda)
#   ./scripts/run_tests_h200.sh tests/test_x.py ...   # hedefli: tam olarak verilen testler
#   ./scripts/run_tests_h200.sh -k "kol2 or grounding"  # pytest argümanları da geçer
#
# DÜRÜST SINIR: Bu imaj ingestion/parse taşımaz. Tam-süit kapısı veya docling testleri istiyorsan
#   tüm bağımlılıkları taşıyan AYRI bir test imajı gerekir (ayrı altyapı işi) — bu koşucu onları KOŞMAZ.
#
# ORTAM DEĞİŞKENLERİ (opsiyonel):
#   RAGINTEL_CONTAINER  (vars: ragintel-api)  — imajı çıkarılacak konteyner adı
#   PYTEST_VERSION      (vars: 9.0.2)         — ephemeral kurulacak pytest sürümü
set -euo pipefail

CONTAINER="${RAGINTEL_CONTAINER:-ragintel-api}"
PYTEST_VER="${PYTEST_VERSION:-9.0.2}"

# Üretim konteynerinin ÇALIŞAN imajını al (kod imaja pişirilir; bind-mount değil).
IMG="$(docker inspect -f '{{.Config.Image}}' "$CONTAINER")"

# Varsayılan hedefler: ağır-dep'siz davranış testleri. Hepsi ingestion/parse/torch İMPORT ETMEZ,
# dolayısıyla üretim-API imajında güvenle koşar. kol-2 kapsam çitiyle (agent + context_builder) örtüşür.
DEFAULT_TARGETS=(
  tests/test_faz4_graph_flow.py
  tests/test_faz4_grounding.py
  tests/test_ip34_context_builder.py
  tests/test_ip34_validate_bridge.py
  tests/test_faz_m9_dusuk_kapsama_geribildirimi.py
  tests/test_faz_m9_retry_butcesi.py
  tests/test_kol2_prefix_a_sayac.py
  tests/test_kol2_prefix_b_append_only.py
  tests/test_m17_honesty_d4.py
  tests/test_faz_m14_memory_search.py
)

if [ "$#" -gt 0 ]; then
  TARGETS=("$@")
else
  TARGETS=("${DEFAULT_TARGETS[@]}")
fi

echo "[run_tests_h200] konteyner=$CONTAINER  imaj=$IMG"
echo "[run_tests_h200] hedefler=${TARGETS[*]}"

# Tek-atımlık mount'lu konteyner: pytest'i geçici kur, git ownership'i düzelt, hedefleri koş.
# `_` -> $0; kalan argümanlar `$@` olarak pytest'e birebir geçer.
docker run --rm -v "$PWD":/app -w /app -e PYTEST_VER="$PYTEST_VER" "$IMG" sh -lc '
  git config --global --add safe.directory /app 2>/dev/null || true
  pip install -q "pytest==${PYTEST_VER}" >/dev/null
  exec python -m pytest -q "$@"
' _ "${TARGETS[@]}"

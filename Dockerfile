# ragintel-api — SORGU tarafı imajı (M-10/0).
#
# Tasarım kararları:
#   * torch/docling YOK  → ölçüldü: API import zinciri docling'e dokunmuyor
#     (parsing/backends.py lazy import). İmaj ~2.5 GB yerine ~700 MB sınıfında.
#   * BGE-M3 tokenizer BUILD'de gömülür → runtime'da İNTERNET ŞARTI YOK.
#     (H200 kapalı ağda; ilk sorgu HF'ye çıkmaya çalışıp patlamamalı.)
#   * ingest bu imajda KOŞMAZ. Ingest, docling'li tam kurulumla ayrı yürütülür.

# =============================================================================
# 1) builder — tokenizer'ı indir ve YEREL DİZİNE yaz (tek internet gerektiren adım)
# =============================================================================
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1

# Tokenizer indirmek için transformers+tokenizers yeter (torch DEĞİL).
# sentencepiece+protobuf: BGE-M3 XLM-RoBERTa tabanlı; HF repo'sunda hazır
# `tokenizer.json` yoksa transformers slow→fast DÖNÜŞTÜRÜR ve bu sentencepiece ister.
RUN pip install --no-cache-dir \
      "transformers==4.57.3" "tokenizers==0.22.1" "sentencepiece==0.2.0" "protobuf==5.29.5"

# BGE-M3 tokenizer'ı SABİT BİR DİZİNE yaz (HF cache'ine DEĞİL).
#
# NEDEN (M-10/0 düzeltmesi — H200 build'inde yaşandı, lokalde 4.57.3 ile ölçüldü):
# transformers repo-id ile çağrıldığında, HF cache DOLU ve HF_HUB_OFFLINE=1 olsa
# BİLE `model_info()` çağırıyor → `OfflineModeIsEnabled` → build KIRILIYOR.
# `local_files_only=True` eklemek de KURTARMIYOR (ölçüldü). Tek çalışan yol:
# yükleme kaynağı olarak YEREL DİZİN vermek (bkz. chunking/tokenizer.py).
#
# `assert t.is_fast`: cache'e FAST biçim (tokenizer.json) yazıldığını garantiler →
# offset_mapping (chunk sınırı → karakter eşlemesi) runtime'da çalışır.
RUN python -c "\
from transformers import AutoTokenizer; \
t = AutoTokenizer.from_pretrained('BAAI/bge-m3', use_fast=True); \
assert t.is_fast, 'FAST tokenizer alinamadi — offset_mapping olmadan chunklama kirilir'; \
t.save_pretrained('/opt/models/bge-m3-tokenizer'); \
print('tokenizer kaydedildi:', t.__class__.__name__, '| vocab:', t.vocab_size)"

# =============================================================================
# 2) runtime
# =============================================================================
FROM python:3.11-slim

# python-magic'in sistem kütüphanesi (import-time zorunlu — ingestion/__init__
# eager zinciri filetypes'ı çeker). libmagic olmadan konteyner AÇILIŞTA patlar.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 curl \
    && rm -rf /var/lib/apt/lists/*

# RAGINTEL_TOKENIZER_DIR: tokenizer repo-id ile DEĞİL, bu dizinden yüklenir
#   (bkz. chunking/tokenizer.py — repo-id yolu kapalı ağda ölüyor, ölçüldü).
# HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE: kemer+askı — bir yol yine de repo-id'ye
#   düşerse ağda ASILMASIN, hemen patlasın.
# (Yorumlar blok İÇİNDE değil ÜSTÜNDE: satır-devamı içi yorum Docker'da geçerli
#  ama burada doğrulanamıyor — teslim edilen dosyada doğrulanmamış incelik olmaz.)
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    RAGINTEL_TOKENIZER_DIR=/opt/models/bge-m3-tokenizer \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# Gömülü tokenizer (runtime'da ağ yok).
COPY --from=builder /opt/models/bge-m3-tokenizer /opt/models/bge-m3-tokenizer

WORKDIR /app

# Bağımlılıklar önce → kod değişince bu katman cache'ten gelir.
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

# ragintel paketi: --no-deps ŞART — aksi hâlde pyproject'teki docling geri gelir
# ve torch'u çeker (imajın tüm anlamı kaybolur).
# README.md: pyproject `readme = "README.md"` → setuptools build'de okur. Bu dosyanın
# build context'e girmesi `.dockerignore`'daki `!README.md` istisnasına bağlıdır.
COPY pyproject.toml README.md ./
COPY ragintel ./ragintel
RUN pip install --no-cache-dir --no-deps .

# Sürüm damgası: /api/health `git_sha` döndürür → hangi kod koşuyor, GÖRÜNÜR.
ARG GIT_SHA=unknown
ENV RAGINTEL_GIT_SHA=$GIT_SHA

# --- BUILD DOĞRULAMASI 1: imaj sınırı + tokenizer offline ---
# Runtime'da sürpriz yok: kırıksa üretimde değil BURADA patlar.
RUN python -c "\
import os, sys; \
assert not os.path.exists('/app/.env'), 'GUVENLIK: .env imaja sizmis (.dockerignore bozuk)'; \
import ragintel.api.app; \
import ragintel.api.user_auth, ragintel.api.passwords, ragintel.api.session_store, redis; \
yasak = [m for m in ('torch', 'docling', 'cv2') if m in sys.modules]; \
assert not yasak, f'YASAK modul imajda yuklendi: {yasak}'; \
from ragintel.ingestion.chunking.tokenizer import tokenizer_kaynagi; \
kaynak, yerel = tokenizer_kaynagi('BAAI/bge-m3'); \
assert yerel and kaynak.startswith('/opt/models'), f'tokenizer repo-id yolunda: {kaynak!r}'; \
from ragintel.ingestion.chunking import BGEM3TokenCounter; \
n = BGEM3TokenCounter().count('karbon vergisi testi'); \
assert n > 0, 'tokenizer offline calismadi'; \
print(f'BUILD DOGRULAMA 1 OK — torch/docling yok, .env yok, tokenizer offline ({n} token)')"

# --- BUILD DOĞRULAMASI 2: bootstrap os.environ'u OKUYOR mu? ---
# M-10/0 / H200 regresyonu: okumuyordu (env_settings zincir dışıydı) →
# `os.getenv('RAGINTEL_DB_HOST')` dolu ama `DbSettings().host` boş →
# MissingBootstrapSetting → konteyner hiç ayağa kalkmadı.
# SAHTE bir değerle sınanır (gerçek sır build'e GİRMEZ) ve değer kod
# varsayılanından farklıdır — yoksa test boş olurdu.
RUN RAGINTEL_DB_HOST=build-smoke.invalid python -c "\
import os; \
from ragintel.config.settings import DbSettings; \
h = DbSettings().host; \
beklenen = os.environ['RAGINTEL_DB_HOST']; \
assert h == beklenen, f'bootstrap os.environ OKUMUYOR: DbSettings().host={h!r} != {beklenen!r}'; \
print('BUILD DOGRULAMA 2 OK — bootstrap os.environ zinciri calisiyor:', h)"

EXPOSE 8000

# host-network'te 8000'de dinler (compose'da network_mode: host).
CMD ["uvicorn", "ragintel.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

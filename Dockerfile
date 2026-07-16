# ragintel-api — SORGU tarafı imajı (M-10/0).
#
# Tasarım kararları:
#   * torch/docling YOK  → ölçüldü: API import zinciri docling'e dokunmuyor
#     (parsing/backends.py lazy import). İmaj ~2.5 GB yerine ~700 MB sınıfında.
#   * BGE-M3 tokenizer BUILD'de gömülür → runtime'da İNTERNET ŞARTI YOK.
#     (H200 kapalı ağda; ilk sorgu HF'ye çıkmaya çalışıp patlamamalı.)
#   * ingest bu imajda KOŞMAZ. Ingest, docling'li tam kurulumla ayrı yürütülür.

# =============================================================================
# 1) builder — tokenizer'ı indir (tek internet gerektiren adım)
# =============================================================================
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf

# Tokenizer indirmek için transformers+tokenizers yeter (torch DEĞİL).
# sentencepiece+protobuf: BGE-M3 XLM-RoBERTa tabanlı; HF repo'sunda hazır
# `tokenizer.json` yoksa transformers slow→fast DÖNÜŞTÜRÜR ve bu sentencepiece
# ister. Yalnızca BUILD'de gerekli (dönüşüm bir kez); runtime'a taşınmaz.
RUN pip install --no-cache-dir \
      "transformers==4.57.3" "tokenizers==0.22.1" "sentencepiece==0.2.0" "protobuf==5.29.5"

# BGE-M3 tokenizer'ı HF cache'ine indir → sonraki stage'e kopyalanır.
# Kod `AutoTokenizer.from_pretrained("BAAI/bge-m3")` çağırıyor; HF_HOME işaret
# ettiği sürece cache'ten offline bulunur (kod değişikliği GEREKMEZ).
# `use_fast=True` + is_fast doğrulaması: cache'e FAST biçim (tokenizer.json)
# yazıldığını garantiler → runtime sentencepiece OLMADAN yükleyebilir.
RUN python -c "\
from transformers import AutoTokenizer; \
t = AutoTokenizer.from_pretrained('BAAI/bge-m3', use_fast=True); \
assert t.is_fast, 'FAST tokenizer alinamadi — runtime sentencepiece isteyecek'; \
print('tokenizer indirildi:', t.__class__.__name__, '| vocab:', t.vocab_size)"

# =============================================================================
# 2) runtime
# =============================================================================
FROM python:3.11-slim

# python-magic'in sistem kütüphanesi (import-time zorunlu — ingestion/__init__
# eager zinciri filetypes'ı çeker). libmagic olmadan konteyner AÇILIŞTA patlar.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# Gömülü tokenizer (runtime'da ağ yok).
COPY --from=builder /opt/hf /opt/hf

WORKDIR /app

# Bağımlılıklar önce → kod değişince bu katman cache'ten gelir.
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

# ragintel paketi: --no-deps ŞART — aksi hâlde pyproject'teki docling geri gelir
# ve torch'u çeker (imajın tüm anlamı kaybolur).
COPY pyproject.toml README.md ./
COPY ragintel ./ragintel
RUN pip install --no-cache-dir --no-deps .

# Sürüm damgası: /api/health `git_sha` döndürür → hangi kod koşuyor, GÖRÜNÜR.
ARG GIT_SHA=unknown
ENV RAGINTEL_GIT_SHA=$GIT_SHA

# Kurulum doğrulaması BUILD'de yapılır (runtime'da sürpriz yok):
#   1. torch/docling gerçekten YOK mu?  2. tokenizer offline çalışıyor mu?
RUN python -c "\
import sys; import ragintel.api.app; \
yasak = [m for m in ('torch', 'docling', 'cv2') if m in sys.modules]; \
assert not yasak, f'YASAK modul imajda yuklendi: {yasak}'; \
from ragintel.ingestion.chunking import BGEM3TokenCounter; \
n = BGEM3TokenCounter().count('karbon vergisi testi'); \
assert n > 0, 'tokenizer offline calismadi'; \
print(f'BUILD DOGRULAMA OK — torch/docling yok, tokenizer offline calisiyor ({n} token)')"

EXPOSE 8000

# host-network'te 8000'de dinler (compose'da network_mode: host).
CMD ["uvicorn", "ragintel.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

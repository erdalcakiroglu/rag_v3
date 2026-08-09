# ragintel — RAG v2 (FAZ 1)

Config-first ingestion pipeline. PostgreSQL 17 + pgvector. Şema: `docs/FAZ1_Sema.sql`.

## Kurulum

```bash
pip install -e ".[dev]"
cp .env.example .env    # DB bağlantısını ve override'ları düzenleyin
```

Python 3.11+. Bağımlılıklar: pydantic-settings, psycopg[binary] v3, psycopg-pool,
structlog.

## İP-0 — Yapı

```
ragintel/
  config/          # Öncelik zinciri: DB (app_config) > ENV > kod varsayılanı
    settings.py    #   bootstrap (DbSettings/LogSettings) + pipeline grup varsayılanları
    resolver.py    #   saf katman birleştirme + yaprak-başına kaynak izleme
    loader.py      #   load_config(db_reader, environ) -> EffectiveConfig
    __main__.py    #   `python -m ragintel.config show [--json]`
  database/        # psycopg v3 tek connection pool + retry/backoff
    pool.py        #   Database, connect_with_retry (3 deneme, exponential backoff)
    config_store.py#   app_config okuma (zincirin DB katmanı)
  observability/   # structlog JSON loglama, file_id/trace_id context binding
    logging.py
  text/            # İP-3: normalize_for_quote (ORTAK — İP-5 + FAZ 4 validation)
  ingestion/       # İP-1: Folder Scanner (filetypes/storage/scanner)
    cleaning/      # İP-3: deterministik temizlik (cleaner) + adaptör (retention)
    parsing/       # İP-2: Parse Adaptörü
    chunking/      # İP-5: section-aware chunking (chunker/tokenizer/quality/adapter) + İP-6 doluluk
    injection/     # İP-4: kural tabanlı injection taraması (rules/scanner/adapter) — torch YOK
    embedding/     # İP-7 (ADR-012): remote Ollama backend (embedder/quality/service)
    persistence/   # İP-8: transaksiyonel storage (chunks+vectors+tables+figures, COPY binary)
    orchestrator.py # İP-10: durum makinesi + uçtan uca pipeline + RETRY/REPROCESS
    cli.py          # İP-10: ragintel ingest scan|run|retry|reprocess|status
    qc.py           # İP-9: chunk QC + bileşik quality_score + backfill
  report/          # İP-9: python -m ragintel.report ingestion|backfill
      parsed_document.py  # DONMUŞ kontrat (pages/sections/tables/figures/language/warnings)
      backends.py         # backend seçimi (auto|docling|fallback)
      docling_backend.py  # üretim birincil (pdf/docx, dahili OCR=rapidocr)
      fallback_backend.py # pymupdf(pdf)/python-docx(docx) — test/offline yedek
      office_backend.py   # xlsx=openpyxl, txt=charset-normalizer
      quality.py          # parse alt skoru (coverage/garbage/table_count)
      adapter.py          # dispatch + OCR fallback + timeout + metrik + FAILED
tests/             # marker: db (canlı PostgreSQL), slow (docling/torch — atlanır)
```

## İP-2 — Parse

```python
from ragintel.database import Database
from ragintel.config.settings import DbSettings
from ragintel.ingestion.parsing import ParseAdapter

db = Database(DbSettings()).open()
ParseAdapter(db).parse_pending()      # PENDING dosyaları ParsedDocument'e çevirir
```

## Uçtan uca (İP-10)

```bash
ragintel ingest scan <folder> [--scope S]   # klasörü envantere al (PENDING)
ragintel ingest run [--limit N]             # parse→clean→chunk→embed→store
ragintel ingest retry                       # retry_count<3 FAILED'leri yeniden
ragintel ingest reprocess <file_id>         # tek dosyayı elle (parse/intake başlangıç)
ragintel ingest status                      # durum sayımları
```

Durum makinesi PENDING→PROCESSING→COMPLETED/FAILED. Crash-recovery: PROCESSING'de
takılı dosyalar (updated_at > `stuck_processing_minutes`) açılışta RETRY'a çekilir.
Ollama erişilemezse dosya FAILED olmaz — PENDING'e geri alınır, run anlaşılır
hatayla durur (İP-10 RETRY akışı).

## Rapor + QC (İP-9)

```bash
python -m ragintel.report ingestion [--json]   # korpus özeti + FAZ 1 çıkış kriteri
python -m ragintel.report backfill              # skorsuz COMPLETED'lere quality_score
```

Her dosya COMPLETED olunca QC taranır (empty/duplicate/too_short/too_long chunk →
qc_findings) ve bileşik `quality_score = Σ(weights × alt_skor)` yazılır (ağırlıklar
`app_config('quality').weights`). Rapor: parse başarı oranı (**≥%95** FAZ 1 çıkış
kriteri), durum dağılımı, adım süreleri, chunk istatistikleri, kalite dağılımı
(min/ort/p95), en düşük skorlu 10 dosya, açık QC bulguları, dosyalar-arası duplicate.

## İP-2 — Parse

Backend: `RAGINTEL_PARSE_BACKEND=auto` (docling kuruluysa docling, yoksa yedek).
Parse kalite eşikleri ve OCR fallback `app_config('quality')`'den (Ek1); kodda
eşik sabiti yok. Docling ağırdır (torch import ~dk) → testler yedek backend
kullanır, docling `slow` işaretli smoke ile doğrulanır (`pytest -m slow`).

## Kullanım

```bash
python -m ragintel.config show          # efektif config + her ayarın kaynağı
python -m ragintel.config show --json   # makine-okur çıktı
```

Config önceliği: `DB (ragintel.app_config) > ENV (RAGINTEL_<GRUP>_<ALAN>) > varsayılan`.
ENV değişken adları tek alt çizgi (7d ev-stili): `RAGINTEL_DB_HOST`,
`RAGINTEL_EMBEDDING_BATCH_SIZE`, ... Kod içinde sabit parametre yoktur.

> Not: OS düzeyinde tanımlı `RAGINTEL_*` değişkenleri `.env`'i ve varsayılanları
> ezer (pydantic-settings standardı). Bağlantı beklenmedik şekilde başarısızsa
> `env | grep RAGINTEL` ile bayat değişken olup olmadığını kontrol edin.

## Servis (API) + Chat UI (FAZ 7)

Senkron `POST /api/ask` → FinalResponse (Tasarim_FAZ4 §5) + tek sayfa Türkçe chat UI.
Agentic loop'u (LangGraph) sarar; çok-turlu konuşma PostgresSaver `thread_id=session_id`
ile gelir. **Ön koşul:** korpus ingest edilmiş olmalı (yukarıdaki İP-10 akışı) ve
FAZ 4 checkpoint tabloları uygulanmış olmalı (`docs/FAZ4_Sema.sql`).

### Çalıştırma

> **Sunucu (H200) dağıtımı için bu bölüm değil** → [docs/M10_H200_Kurulum.md](docs/M10_H200_Kurulum.md).
> API 2026-07-16'dan beri H200'ün üzerinde **konteynerde** koşuyor (`./deploy.sh`,
> doğrudan Ollama, tokenizer imaja gömülü). Aşağısı **yerel geliştirme** içindir.

Agent LLM'i **config-first**: model `app_config('agent').model`'den (DB), bağlantı
`.env`'den (`RAGINTEL_LLM_API_BASE`, `RAGINTEL_LLM_REQUEST_TIMEOUT`) gelir. Kalıcı
ayarlandıysa tek satır yeter:

```powershell
cd  C:\Users\erdal.cakiroglu\PycharmProjects\rag_v3
python -m uvicorn ragintel.api.app:create_app --factory --host 127.0.0.1 --port 8000

```

`Application startup complete` görünce tarayıcıda **http://127.0.0.1:8000** açın.
Durdurmak: **Ctrl+C**. Port doluysa `--port 8001`. Geliştirmede `--reload` eklenebilir.

Farklı bir model/host'a geçici yönlendirmek için OS env ile override edebilirsiniz
(`os.environ` config'ten önce gelir):

```powershell
$env:RAGINTEL_AGENT_MODEL="qwen3.5:35b"      # geçici model override
$env:RAGINTEL_LLM_API_BASE="http://<host>:11434"
```

> Kalıcı ayar: model → `python -c "..."`/SQL ile `app_config('agent').model`;
> bağlantı → `.env`'e `RAGINTEL_LLM_API_BASE`. (`RAGINTEL_AGENT_MODEL` `.env`'den
> OKUNMAZ — o yalnızca OS env veya DB config'tir.)

### Uç noktalar

| Endpoint | Açıklama |
|---|---|
| `GET /` | Tek sayfa chat UI (markdown yanıt, kaynaklar/quote, confidence rozeti, followup çipleri, 👍/👎) |
| `POST /api/ask` | `{question, session_id?}` → FinalResponse §5. `X-User-Id` header (yoksa `dev`); `X-Session-Id`/`X-Injection-Flagged` yanıt header'larında |
| `POST /api/feedback` | `{session_id, trace_id, rating(+1/-1), comment?}` → Langfuse score |
| `GET /api/health` | DB/Ollama/TEI/Langfuse durumu (Ollama down→`unhealthy`, TEI down→`degraded`) |

### Notlar

- **Girdi guardrail:** boş/uzun soru reddi (`agent.max_question_chars`); İP-4 injection
  **flag-only** (reddetmez, işaretler+devam eder). `user_ctx` LLM'e sızmaz; MVP'de
  `allowed_doc_scopes=['default']` sabit (gerçek AuthN FAZ 6/7 — kodda TODO).
- **Dürüst fallback:** LLM/altyapı erişilemezse sistem çökmez; "kaynaklarda bulunamadı"
  veya "dil modeli erişilemedi" yanıtı döner (gizlenmez, bu bir özelliktir).
- **Langfuse:** `.env`'de `RAGINTEL_LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY` doluysa her
  istek trace (api.request→agent.run→node'lar) + feedback score Langfuse'a gider;
  boşsa no-op (7e).

## Test

```bash
pytest                 # tümü (canlı DB erişilemezse db-marker testleri atlanır)
pytest -m "not db"     # yalnızca DB'siz (hermetik) testler
```
## İşletim Runbook: Wipe / Re-ingest / Dump → Restore

Yerelde korpusu sıfırdan işleyip (local DB `192.168.36.15`) H200 üretime
(`10.50.130.55`) taşıma akışının **tüm** komutları. Kaynak korpus:
`C:\BDDK-Mevzuat\bddk_mevzuat_pdf` (BDDK mevzuat PDF'leri).

> **Korpus tabloları (7):** `core_files, core_chunks, core_vectors, core_tables,
> core_figures, metrics_ingestion, qc_findings`.
> **ASLA dokunulmayan** (korunur): `users, app_config, config_audit,
> eval_golden_records, eval_golden_sets, conversations, conversation_messages,
> checkpoint_*` (LangGraph). FK grafiği denetlendi: hiçbir korunan tablo korpusa
> FK ile bağlı değil → aşağıdaki `TRUNCATE ... CASCADE` golden'ı **silmez**.

### 1) WIPE — yalnız korpus (golden/users/app_config korunur)

psql -h 10.50.130.55 -d ragintel -U ragintel_app -W

```sql
-- psql "host=192.168.36.15 dbname=ragintel user=ragintel_app"
-- 7 korpus tablosunu tek işlemde boşaltır; kimlik dizilerini sıfırlar.
TRUNCATE ragintel.core_files,
         ragintel.core_chunks,
         ragintel.core_vectors,
         ragintel.core_tables,
         ragintel.core_figures,
         ragintel.metrics_ingestion,
         ragintel.qc_findings
    RESTART IDENTITY CASCADE;

-- Doğrulama: 7 korpus tablosu 0; korunanlar DEĞİŞMEMİŞ olmalı.
SELECT (SELECT count(*) FROM ragintel.core_files)          AS files,
       (SELECT count(*) FROM ragintel.core_chunks)         AS chunks,
       (SELECT count(*) FROM ragintel.core_vectors)        AS vectors,
       (SELECT count(*) FROM ragintel.core_tables)         AS tables,
       (SELECT count(*) FROM ragintel.core_figures)        AS figures,
       (SELECT count(*) FROM ragintel.metrics_ingestion)   AS metrics,
       (SELECT count(*) FROM ragintel.qc_findings)         AS qc,
       (SELECT count(*) FROM ragintel.eval_golden_records) AS golden_KORUNUR,
       (SELECT count(*) FROM ragintel.users)               AS users_KORUNUR,
       (SELECT count(*) FROM ragintel.app_config)          AS app_config_KORUNUR;
```

### 2) Şema önkoşulu — Ek4 (embed_sanitized)

`embed_sanitized` qc bulgusu (son-çare pipe→boşluk embed fallback işareti) CHECK
listesinde olmalı; yoksa o bulguyu içeren dosyanın insert'i patlar ve **tüm dosya
transaction'ı rollback** olur. **Local + H200'de bir kez** uygulanır:

```bash
psql "host=192.168.36.15 dbname=ragintel user=ragintel_app" -f docs/FAZ1_Sema_Ek4_Embed_Sanitized.sql   # LOCAL
psql "host=10.50.130.55  dbname=ragintel user=__ENV_H200_DEN_DOLDUR__" -f docs/FAZ1_Sema_Ek4_Embed_Sanitized.sql   # H200/PROD (dump ÖNCESİ)
```

### 3) Scan + uçtan uca run (local)

```powershell
cd C:\Users\erdal.cakiroglu\PycharmProjects\rag_v3
python -m ragintel ingest scan "C:\BDDK-Mevzuat\bddk_mevzuat_pdf" --scope default
# manifest.csv belge değildir — intake'ten çıkarın (korpusu kirletmesin):
#   DELETE FROM ragintel.core_files WHERE file_type='txt' AND file_name='manifest.csv';
python -m ragintel ingest run              # tüm PENDING'i uçtan uca işler
python -m ragintel ingest retry            # embed kesintisi vb. sonrası kalanları dener
python -m ragintel.report ingestion        # korpus özeti + FAZ 1 çıkış kriteri
```

Durum ve parite izleme:

```sql
SELECT status, count(*) FROM ragintel.core_files GROUP BY status;
-- chunk = vector paritesi (eşit olmalı):
SELECT (SELECT count(*) FROM ragintel.core_chunks)  AS chunks,
       (SELECT count(*) FROM ragintel.core_vectors) AS vectors;
-- takılı/işlenmemiş dosyalar:
SELECT file_id, file_name, status, fail_reason
FROM ragintel.core_files WHERE status IN ('PROCESSING','PENDING','FAILED') ORDER BY file_id;
```

### 4) DUMP (local) → RESTORE (H200)

Yalnız korpus tabloları dump edilir; H200'ün `users/app_config/golden`'ı korunur.
`pg_dump --data-only` tabloları FK bağımlılık sırasında yazar ve dizi `setval`'larını
içerir. **`file_id` değerleri korunur** (chunk→file bağları bozulmaz).

```bash
# 4a) LOCAL'de dump (korpus tabloları, veri-only)
pg_dump "host=192.168.36.15 dbname=ragintel user=ragintel_app" \
  --data-only --no-owner --no-privileges \
  -t ragintel.core_files -t ragintel.core_tables -t ragintel.core_chunks \
  -t ragintel.core_vectors -t ragintel.core_figures \
  -t ragintel.metrics_ingestion -t ragintel.qc_findings \
  -f corpus_dump.sql

# 4b) H200'e taşı
scp corpus_dump.sql __ENV_H200_KULLANICI__@10.50.130.55:/tmp/corpus_dump.sql

# 4c) H200/PROD'da: önce korpusu temizle (users/app_config/golden'a DOKUNMAZ),
#     sonra restore. Ek4 (adım 2) UYGULANMIŞ olmalı.
psql "host=10.50.130.55 dbname=ragintel user=__ENV_H200_DEN_DOLDUR__" -c \
  "TRUNCATE ragintel.core_files, ragintel.core_chunks, ragintel.core_vectors, \
            ragintel.core_tables, ragintel.core_figures, \
            ragintel.metrics_ingestion, ragintel.qc_findings RESTART IDENTITY CASCADE;"

psql "host=10.50.130.55 dbname=ragintel user=__ENV_H200_DEN_DOLDUR__" \
  -v ON_ERROR_STOP=1 -f /tmp/corpus_dump.sql
```

> Şifre `.pgpass` veya `PGPASSWORD` env ile verilir; komut satırında yazılmaz.
> FK sırası nadir bir sürümde takılırsa restore'u `SET session_replication_role =
> replica;` ile sarın (FK trigger'larını geçici kapatır; yetkili rol gerekir).

### 5) H200 doğrulama + smoke

```sql
-- prod korpus sayıları local ile eşleşmeli; parite tam:
SELECT (SELECT count(*) FROM ragintel.core_files WHERE status='COMPLETED') AS completed,
       (SELECT count(*) FROM ragintel.core_chunks)  AS chunks,
       (SELECT count(*) FROM ragintel.core_vectors) AS vectors;
```

```bash
# API konteyneri (H200) yeniden başlat + sağlık:
docker restart ragintel-api
# ~2 dk warm-up sonra:
curl -s http://localhost:8000/api/health | grep -o '"status":"[a-z]*"'   # → "healthy"
# smoke sorgu:
curl -s -X POST http://localhost:8000/api/ask -H "Content-Type: application/json" \
  -d '{"question":"Bankacılık Kanunu kaç sayılıdır?"}' | head -c 400
```

### Yardımcı

```sql
select * from ragintel.v_config_flat;   -- efektif parametreleri gör
-- Dosyayı yeniden işlemek için PENDING'e çek (örnek):
-- UPDATE ragintel.core_files SET status='PENDING', fail_reason=NULL WHERE file_id IN (...);
```

## H200 Bare-Metal GPU Ingestion (sıfırdan)

Korpusu **doğrudan H200 GPU'sunda** bare-metal (Docker'sız) uçtan uca işlemek için
tam runbook. Docling TableFormer **ACCURATE** modunda çalışır (GPU'da hızlı, en yüksek
tablo kalitesi) — CPU'daki `fast/8` yamasına gerek yoktur. Prod DB'ye (`10.50.130.55`)
yazar; kod `/opt/ragintel` (deploy'lu commit), venv ve veri `/datafile/ragintel`.

> **Ön koşullar:** GPU görülüyor (`nvidia-smi` → H200 NVL, sürücü 570/CUDA 12.8),
> ham PDF'ler host'ta (`/home/erdal.cakiroglu/raw_files`), Ollama host'ta systemd ile
> ayakta (`bge-m3:latest` yüklü), DB ayarları `/opt/ragintel/.env.h200` içinde.

### 0) Sistem kütüphanesi — libGL (bir kez)

Docling OpenCV (cv2) kullanır; headless sunucuda `libGL.so.1` yoktur → **headless
OpenCV** kur (X11/libGL bağımlılığı olmayan sürüm). Bu olmadan her dosya
`libGL.so.1: cannot open shared object file` ile FAILED olur.

```bash
pip uninstall -y opencv-python opencv-contrib-python
pip install opencv-python-headless
python -c "import cv2; print('cv2 ok', cv2.__version__)"
```

### 1) Python 3.12 venv (bir kez)

`lingua-language-detector==2.2.0` yalnız Python 3.12'de çözülür (3.9/3.11'de yok).

```bash
dnf install -y python3.12 python3.12-devel
python3.12 -m venv /datafile/ragintel/venv
source /datafile/ragintel/venv/bin/activate
python -V            # >>> Python 3.12.x
```

### 2) torch — cu128 build (bir kez, KRİTİK)

PyPI-default torch **cu130** çeker; sürücü CUDA **12.8** olduğundan "driver too old
(12080)" der. Doğru hedef **cu128** wheel'i. En yeni cu128 build = `2.11.0`.

```bash
pip install "torch==2.11.0" torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
python -c "import torch;print(torch.__version__, torch.version.cuda, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
#   >>> 2.11.0+cu128 12.8 True NVIDIA H200 NVL   (True + isim görmeden devam etme)
```

### 3) Projeyi kur (bir kez)

```bash
pip install -e /opt/ragintel
python -c "import torch, torchvision, docling, ragintel; assert torch.cuda.is_available(); print('OK', torch.__version__, 'tv', torchvision.__version__)"
#   >>> OK 2.11.0+cu128 tv 0.26.0+cu128
# NOT: -e kurulumu torch'u cu130'a geri yükseltirse adım 2'yi bir kez daha koştur.
```

### 4) Env (HER yeni SSH oturumunda)

```bash
source /datafile/ragintel/venv/bin/activate
set -a; source /opt/ragintel/.env.h200; set +a          # DB (host=10.50.130.55) vb.
export RAGINTEL_OLLAMA_BASE_URL=http://localhost:11434   # bare-metal: host Ollama
export RAGINTEL_STORAGE_ROOT=/datafile/ragintel/storage
export HF_HOME=/datafile/ragintel/hf_cache
mkdir -p "$RAGINTEL_STORAGE_ROOT" "$HF_HOME"
echo "DB host = $RAGINTEL_DB_HOST"                        # boş DEĞİL olmalı
```

### 5) Smoke (bağlantı + embed wire teyidi)

```bash
python -m ragintel ingest status          # DB'ye bağlanır, korpus sayaçları (boşsa {})
# embed wire: BAAI/bge-m3 (HF id) → Ollama etiketi bge-m3:latest → 1024-dim
python - <<'PY'
from ragintel.config.settings import OllamaSettings
from ragintel.ingestion.embedding.embedder import OllamaEmbedder
s = OllamaSettings()
e = OllamaEmbedder(s.require_base_url(), model="BAAI/bge-m3", timeout=s.timeout, api_key=s.api_key)
print("wire:", e.model, "| stamp:", e.model_name, "| dim:", len(e.embed_batch(["merhaba"])[0]))
# >>> wire: bge-m3:latest | stamp: bge-m3@ollama | dim: 1024
PY
```

### 6) Scan (envantere al — PENDING)

```bash
python -m ragintel ingest scan /datafile/ragintel/storage/ --scope default
python -m ragintel ingest status                         # >>> {"PENDING": <adet>}
find /datafile/ragintel/storage -type f -iname '*.pdf' | wc -l   # çapraz kontrol
```

### 7) Run — önce trial, sonra tam korpus

```bash
# 7a) Trial: 3 dosya önplanda (zincirin yazdığını kanıtla)
python -m ragintel ingest run --limit 3
python -m ragintel ingest status         # >>> 3 COMPLETED / 0 FAILED beklenir

# 7b) Tam run: SSH kopsa da sürsün (nohup + log)
mkdir -p /datafile/ragintel/logs
nohup python -m ragintel ingest run > /datafile/ragintel/logs/ingest_$(date +%Y%m%d_%H%M%S).log 2>&1 &
echo "PID: $!"
```

> Altyapı (libGL/torch/Ollama) hatasıyla düşen dosyalar FAILED olur; kök düzeltilince
> `python -m ragintel ingest retry` (retry_count<3) hepsini yeniden dener.

### 8) İzleme + parite doğrulama

```bash
tail -f /datafile/ragintel/logs/ingest_*.log            # canlı akış (Ctrl+C çıkış)
watch -n 30 'python -m ragintel ingest status'          # sayaç ilerlemesi
```

```bash
python - <<'PY'
import os, psycopg
sch=os.environ.get("RAGINTEL_DB_SCHEMA","ragintel")
c=psycopg.connect(host=os.environ["RAGINTEL_DB_HOST"],port=os.environ.get("RAGINTEL_DB_PORT","5432"),
  dbname=os.environ["RAGINTEL_DB_NAME"],user=os.environ["RAGINTEL_DB_USER"],password=os.environ["RAGINTEL_DB_PASSWORD"])
cur=c.cursor()
for t in ("core_files","core_chunks","core_vectors","core_tables","core_figures"):
    cur.execute(f"SELECT count(*) FROM {sch}.{t}"); print(t, cur.fetchone()[0])
cur.execute(f"SELECT status,count(*) FROM {sch}.core_files GROUP BY status"); print("status:", dict(cur.fetchall()))
cur.execute(f"SELECT DISTINCT model_name FROM {sch}.core_vectors"); print("vector model:", cur.fetchall())
PY
# Sağlıklı bitiş: core_chunks == core_vectors, FAILED=0, vector model = bge-m3@ollama
```

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
-- Tum dosyalarin ve chunk'larin silinmesi icin kullanilir.
-- psql "host=192.168.36.15 dbname=ragintel user=ragintel_app"
BEGIN;
TRUNCATE ragintel.core_files CASCADE;
COMMIT;

-- Doğrulama: hepsi 0 dönmeli
SELECT (SELECT count(*) FROM ragintel.core_files)   AS files,
       (SELECT count(*) FROM ragintel.core_chunks)  AS chunks,
       (SELECT count(*) FROM ragintel.core_vectors) AS vectors,
       (SELECT count(*) FROM ragintel.qc_findings)  AS qc,
       (SELECT count(*) FROM ragintel.metrics_ingestion) AS metrics;

       -------------
-- Dosya durumlarını kontrol etmek için kullanılabilir.
SELECT file_id, file_name, status, source_path
FROM ragintel.core_files
WHERE status IN ('PROCESSING', 'PENDING')
ORDER BY file_id;



-- UPDATE ragintel.core_files
-- SET status = 'PENDING'
-- WHERE file_id IN (2961, 2962);

--- Dosya işleme pipeliemım başlatılması için kullanılabilir.
ragintel ingest scan .\raw_files
ragintel ingest run
ragintel ingest retry
ragintel ingest run --limit 10   --- 10 dosya işlenir. 10'dan fazla dosya varsa, kalanlar bir sonraki çalıştırmada işlenir.
python -m ragintel.report ingestion


------------------
select * from ragintel.v_config_flat  -- parametreleri gorelim.


# Ragintel container restart etme (h200)

docker restart ragintel-api
# ~2 dk warm-up sonra:
curl -s http://localhost:8000/api/health | grep -o '"status":"[a-z]*"'   # → "healthy"

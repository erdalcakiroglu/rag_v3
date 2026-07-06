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

## Test

```bash
pytest                 # tümü (canlı DB erişilemezse db-marker testleri atlanır)
pytest -m "not db"     # yalnızca DB'siz (hermetik) testler
```

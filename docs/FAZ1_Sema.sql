-- ============================================================================
-- RAG v2 — FAZ 1 Veritabanı Şeması  (v0.1, 2026-07-02)
-- Hedef: PostgreSQL 17 + pgvector >= 0.7
-- Uygulama: psql "host=127.0.0.1 dbname=ragintel user=ragintel_app" -f FAZ1_Sema.sql
-- Not: FAZ 4 tasarım kontratlarına uyumlu (citation alanları, doc_scope,
--      normalize metin). Bkz: Tasarim_FAZ4_Agentic_Loop.md Bölüm 9.
-- ============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS ragintel;
SET search_path TO ragintel;

-- ----------------------------------------------------------------------------
-- 0. Ortak: updated_at trigger fonksiyonu
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ragintel.fn_touch_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END $$;

-- ----------------------------------------------------------------------------
-- 1. CONFIG — config-first: tüm pipeline parametreleri DB'den (v1 mirası)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_config (
    config_key    text PRIMARY KEY,
    config_value  jsonb NOT NULL,
    description   text,
    updated_by    text,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

INSERT INTO app_config (config_key, config_value, description) VALUES
    ('chunking',  '{"strategy":"section","max_tokens":512,"overlap_tokens":64,"min_tokens":30}', 'Chunking varsayılanları'),
    ('embedding', '{"model":"BAAI/bge-m3","dim":1024,"batch_size":32,"normalize":true}',          'Embedding varsayılanları'),
    ('ingestion', '{"allowed_types":["pdf","docx","xlsx","txt"],"max_file_mb":100}',              'Dosya kabul kuralları')
ON CONFLICT (config_key) DO NOTHING;

-- ----------------------------------------------------------------------------
-- 2. CORE_FILES — dosya envanteri + job state (Postgres status-based, ADR)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core_files (
    file_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_name      text NOT NULL,
    file_type      text NOT NULL CHECK (file_type IN ('pdf','docx','xlsx','txt')),
    file_size      bigint NOT NULL CHECK (file_size >= 0),
    checksum       text NOT NULL,                    -- sha256 hex
    source_path    text NOT NULL,                    -- raw storage yolu
    doc_scope      text NOT NULL DEFAULT 'default',  -- RLS/yetki filtresi (FAZ 4 kontratı)
    language       text,                             -- ISO 639-1, parse sonrası dolar
    doc_version    int  NOT NULL DEFAULT 1,
    status         text NOT NULL DEFAULT 'PENDING'
                   CHECK (status IN ('PENDING','PROCESSING','COMPLETED','FAILED','RETRY','REPROCESS')),
    fail_reason    text,
    retry_count    int  NOT NULL DEFAULT 0,
    injection_flag boolean NOT NULL DEFAULT false,   -- doküman içi injection taraması (ADR-007)
    upload_user    text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (checksum, doc_version)                   -- duplicate/hash kontrolü
);

CREATE INDEX IF NOT EXISTS idx_core_files_status ON core_files (status);
CREATE INDEX IF NOT EXISTS idx_core_files_scope  ON core_files (doc_scope);

CREATE OR REPLACE TRIGGER trg_core_files_touch
    BEFORE UPDATE ON core_files
    FOR EACH ROW EXECUTE FUNCTION ragintel.fn_touch_updated_at();

-- ----------------------------------------------------------------------------
-- 3. CORE_CHUNKS — citation kontratına uygun kaynak alanlarıyla
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core_chunks (
    chunk_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id         bigint NOT NULL REFERENCES core_files(file_id) ON DELETE CASCADE,
    chunk_index     int    NOT NULL,                 -- dosya içi sıra
    chunk_text      text   NOT NULL,
    chunk_text_norm text   NOT NULL,                 -- normalize metin (quote doğrulama — FAZ 4)
    token_count     int    NOT NULL,
    page_number     int,                             -- pdf/docx
    sheet_name      text,                            -- xlsx
    section_title   text,
    char_start      int,                             -- kaynak metindeki span
    char_end        int,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (file_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_core_chunks_file ON core_chunks (file_id);

-- Hybrid search: full-text index (Türkçe için 'simple' — stemming FAZ 3'te değerlendirilir)
ALTER TABLE core_chunks
    ADD COLUMN IF NOT EXISTS tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', chunk_text_norm)) STORED;

CREATE INDEX IF NOT EXISTS idx_core_chunks_tsv ON core_chunks USING gin (tsv);

-- ----------------------------------------------------------------------------
-- 4. CORE_VECTORS — BGE-M3 (1024 boyut)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core_vectors (
    chunk_id     bigint PRIMARY KEY REFERENCES core_chunks(chunk_id) ON DELETE CASCADE,
    embedding    vector(1024) NOT NULL,
    model_name   text NOT NULL DEFAULT 'BAAI/bge-m3',
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- HNSW (cosine). Build sırasında: SET maintenance_work_mem = '2GB';
CREATE INDEX IF NOT EXISTS idx_core_vectors_hnsw
    ON core_vectors USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ----------------------------------------------------------------------------
-- 5. CORE_TABLES / CORE_FIGURES — cleaning modülünden ayıklanan bloklar (v1)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core_tables (
    table_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id      bigint NOT NULL REFERENCES core_files(file_id) ON DELETE CASCADE,
    page_number  int,
    sheet_name   text,
    table_index  int NOT NULL,
    table_data   jsonb NOT NULL,          -- normalize satır/sütun yapısı (Docling çıktısı)
    table_text   text,                    -- düzleştirilmiş metin (chunk'a gömülen)
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core_figures (
    figure_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id      bigint NOT NULL REFERENCES core_files(file_id) ON DELETE CASCADE,
    page_number  int,
    figure_index int NOT NULL,
    caption      text,
    storage_path text,                    -- object storage'daki görsel yolu (ops.)
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 6. METRİKLER — pipeline adım metrikleri (v1 yaklaşımının devamı)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metrics_ingestion (
    metric_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id      bigint NOT NULL REFERENCES core_files(file_id) ON DELETE CASCADE,
    step         text   NOT NULL CHECK (step IN
                 ('intake','parse','clean','chunk','embed','store','injection_scan')),
    duration_ms  int    NOT NULL,
    ok           boolean NOT NULL DEFAULT true,
    detail       jsonb,                   -- adım bazlı sayaçlar (chunk sayısı, kırpılan karakter, vb.)
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_metrics_ingestion_file ON metrics_ingestion (file_id, step);

-- Kalite kontrol bulguları (FAZ 1.12)
CREATE TABLE IF NOT EXISTS qc_findings (
    finding_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id      bigint NOT NULL REFERENCES core_files(file_id) ON DELETE CASCADE,
    chunk_id     bigint REFERENCES core_chunks(chunk_id) ON DELETE CASCADE,
    finding      text NOT NULL CHECK (finding IN
                 ('empty_chunk','duplicate_chunk','too_short','too_long',
                  'parse_failed','embed_failed','injection_suspect',
                  'low_coverage','low_retention','no_cleaning_effect',
                  'chunk_truncation_high','embed_anomaly')),
    detail       text,
    resolved     boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_qc_findings_open ON qc_findings (file_id) WHERE NOT resolved;

COMMIT;

-- ============================================================================
-- SMOKE TEST — şema doğrulama (uygulamadan sonra elle çalıştırın)
-- ============================================================================
-- 1) Tablolar kuruldu mu?
--    \dt ragintel.*
-- 2) Vector + tsvector uçtan uca:
--    INSERT INTO ragintel.core_files (file_name, file_type, file_size, checksum, source_path)
--        VALUES ('test.pdf','pdf',1000,'deadbeef','/tmp/test.pdf');
--    INSERT INTO ragintel.core_chunks (file_id, chunk_index, chunk_text, chunk_text_norm, token_count, page_number)
--        VALUES (1, 0, 'Deneme metni içeriği', 'deneme metni icerigi', 4, 1);
--    INSERT INTO ragintel.core_vectors (chunk_id, embedding)
--        VALUES (1, array_fill(0.1, ARRAY[1024])::vector);
--    SELECT c.chunk_id, v.embedding <=> array_fill(0.1, ARRAY[1024])::vector AS dist,
--           ts_rank(c.tsv, plainto_tsquery('simple','deneme')) AS bm25ish
--    FROM ragintel.core_chunks c JOIN ragintel.core_vectors v USING (chunk_id);
-- 3) Temizlik:
--    DELETE FROM ragintel.core_files WHERE file_name = 'test.pdf';
-- ============================================================================

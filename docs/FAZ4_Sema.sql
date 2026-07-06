-- =============================================================================
-- FAZ 4 — LangGraph PostgresSaver checkpoint şeması (ADR-010)
-- =============================================================================
-- ELLE UYGULANIR (FAZ 2 deseni). Kod .setup() ÇAĞIRMAZ; tablolar burada,
-- ragintel şemasında oluşturulur. Uygulama bağlantısı search_path=ragintel,public
-- ile çalıştığı için langgraph niteliksiz tablo adlarını ragintel'de bulur.
--
-- Kaynak: langgraph-checkpoint-postgres==3.1.0, base.MIGRATIONS[0..9] birebir,
-- ragintel şemasına yönlendirilmiş ve tek dosyada birleştirilmiş hali.
-- checkpoint_migrations'a v=9 seed'i konur → PostgresSaver.setup() no-op olur.
--
-- Uygulama:  psql -h <host> -U <admin> -d ragintel -f docs/FAZ4_Sema.sql
-- NOT: İndeksler CONCURRENTLY DEĞİL (tablolar boş oluşturulur; transaction-güvenli).
-- =============================================================================

BEGIN;

-- Migration 0
CREATE TABLE IF NOT EXISTS ragintel.checkpoint_migrations (
    v INTEGER PRIMARY KEY
);

-- Migration 1
CREATE TABLE IF NOT EXISTS ragintel.checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

-- Migration 2 + 4 (blob NULL edilebilir)
CREATE TABLE IF NOT EXISTS ragintel.checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

-- Migration 3 + 9 (task_path kolonu dahil)
CREATE TABLE IF NOT EXISTS ragintel.checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BYTEA NOT NULL,
    task_path TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

-- Migration 6-8 (thread_id indeksleri; manuel kurulumda plain)
CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON ragintel.checkpoints (thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON ragintel.checkpoint_blobs (thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON ragintel.checkpoint_writes (thread_id);

-- Migration versiyonu: full setup sonrası son indeks = 9. setup()'ı no-op yapar.
INSERT INTO ragintel.checkpoint_migrations (v)
SELECT 9
WHERE NOT EXISTS (SELECT 1 FROM ragintel.checkpoint_migrations WHERE v = 9);

COMMIT;

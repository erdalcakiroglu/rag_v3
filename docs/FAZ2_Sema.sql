-- ============================================================================
-- RAG v2 — FAZ 2 Eval Saklama Şeması (İP-2.1a)
-- Amaç: golden set kayıtlarını versiyonlu ve idempotent loader'a uygun saklamak
-- ============================================================================

BEGIN;

SET search_path TO ragintel, public;

CREATE TABLE IF NOT EXISTS eval_golden_sets (
    set_version  text PRIMARY KEY,
    payload_hash text NOT NULL,
    source_path  text NOT NULL,
    record_count int  NOT NULL CHECK (record_count >= 0),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eval_golden_records (
    set_version    text NOT NULL REFERENCES eval_golden_sets(set_version) ON DELETE CASCADE,
    record_id      text NOT NULL,
    question       text NOT NULL,
    question_norm  text NOT NULL,
    ideal_answer   text NOT NULL,
    category       text NOT NULL CHECK (category IN
                    ('single_fact','synthesis','multi_hop',
                     'table_based','unanswerable','citation_sensitive')),
    difficulty     int  NOT NULL CHECK (difficulty BETWEEN 1 AND 3),
    gold_evidence  jsonb NOT NULL,
    doc_scope      text NOT NULL,
    answerable     boolean NOT NULL,
    created_by     text NOT NULL,
    notes          text NOT NULL DEFAULT '',
    payload_hash   text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (set_version, record_id),
    UNIQUE (set_version, question_norm)
);

COMMIT;

-- ============================================================================
-- RAG v2 — FAZ 1 Şema Eki 3: qc_findings soft-flag değerleri  (v0.1, 2026-07-02)
-- ADDITIVE MIGRATION — FAZ1_Sema.sql sonrası uygulanır.
-- Gerekçe: Ek-A soft flag'leri (İP-2 low coverage, İP-3 retention, İP-5 chunk,
--          İP-7 embed anomalisi) qc_findings.finding CHECK listesinde yoktu;
--          İP-3 kabul kriteri buna takılıyordu (İP-2 raporu).
-- Uygulama: psql "host=192.168.36.15 dbname=ragintel user=ragintel_app" -f FAZ1_Sema_Ek3_QC_Soft.sql
-- ============================================================================

BEGIN;

ALTER TABLE ragintel.qc_findings
    DROP CONSTRAINT IF EXISTS qc_findings_finding_check;

ALTER TABLE ragintel.qc_findings
    ADD CONSTRAINT qc_findings_finding_check
    CHECK (finding IN (
        -- hard/mevcut bulgular
        'empty_chunk','duplicate_chunk','too_short','too_long',
        'parse_failed','embed_failed','injection_suspect',
        -- Ek3: Ek-A soft flag'leri (aşama → bulgu eşlemesi)
        'low_coverage',          -- İP-2 parse: coverage < soft_flag_coverage
        'low_retention',         -- İP-3 clean: retention < soft_flag_retention_low
        'no_cleaning_effect',    -- İP-3 clean: retention > soft_flag_retention_high
        'chunk_truncation_high', -- İP-5 chunk: truncated_ratio > soft_flag_truncated_ratio
        'embed_anomaly'          -- İP-7 embed: doc-içi benzerlik/norm anomalisi
    ));

COMMENT ON CONSTRAINT qc_findings_finding_check ON ragintel.qc_findings IS
    'Ek3: Ek-A soft flag değerleri eklendi. Soft bulgular bloklamaz; operasyon iş listesi (resolved ile takip).';

COMMIT;

-- Doğrulama:
--   INSERT çalışmalı:  finding='low_retention'
--   INSERT hata vermeli: finding='bilinmeyen'

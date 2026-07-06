-- ============================================================================
-- RAG v2 — FAZ 1 Şema Eki 2: intake metrik adımı  (v0.1, 2026-07-02)
-- ADDITIVE MIGRATION — FAZ1_Sema.sql uygulanmış sunucuda çalıştırılır.
-- Gerekçe: İP-1 kabul kriteri #4 (her dosya için intake metriği) ile
--          metrics_ingestion.step CHECK listesi arasındaki boşluk.
-- Uygulama: psql "host=192.168.36.15 dbname=ragintel user=ragintel_app" -f FAZ1_Sema_Ek2_Intake.sql
-- ============================================================================

BEGIN;

ALTER TABLE ragintel.metrics_ingestion
    DROP CONSTRAINT IF EXISTS metrics_ingestion_step_check;

ALTER TABLE ragintel.metrics_ingestion
    ADD CONSTRAINT metrics_ingestion_step_check
    CHECK (step IN ('intake','parse','clean','chunk','embed','store','injection_scan'));

COMMENT ON CONSTRAINT metrics_ingestion_step_check ON ragintel.metrics_ingestion IS
    'Ek2: intake eklendi (İP-1). intake metriği kalite skoruna GİRMEZ (app_config quality.weights kapsamı dışında); yalnızca süre/başarı + detail (dedup sayıları, tip tespiti) taşır.';

COMMIT;

-- Doğrulama:
--   INSERT çalışmalı:  step='intake'
--   INSERT hata vermeli: step='bilinmeyen'

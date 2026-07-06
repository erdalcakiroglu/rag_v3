-- ============================================================================
-- RAG v2 — FAZ 1 Şema Eki 1: Kalite Skorlama  (v0.1, 2026-07-02)
-- ADDITIVE MIGRATION — mevcut kurulumu bozmaz, FAZ1_Sema.sql'den SONRA uygulanır.
-- Uygulama: psql "host=192.168.36.15 dbname=ragintel user=ragintel_app" -f FAZ1_Sema_Ek1_Kalite.sql
-- Gerekçe: ADR-011 (aşama bazlı kalite skorlama). Alt skorlar metrics_ingestion.detail
--          içinde (jsonb, şema değişikliği gerektirmez); bu ek yalnızca dosya düzeyi
--          bileşik skoru ve eşik konfigürasyonunu ekler.
-- ============================================================================

BEGIN;

-- Dosya düzeyi bileşik kalite skoru (0-100). NULL = henüz hesaplanmadı.
ALTER TABLE ragintel.core_files
    ADD COLUMN IF NOT EXISTS quality_score numeric(5,2)
    CHECK (quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 100));

COMMENT ON COLUMN ragintel.core_files.quality_score IS
    'Aşama alt skorlarının ağırlıklı bileşiği (İP-9 hesaplar). Alt skorlar: metrics_ingestion.detail. Düşük skor retrieval güven indirimine girdi olur (FAZ 4 confidence).';

-- Düşük kaliteli dosyaları hızlı listelemek için
CREATE INDEX IF NOT EXISTS idx_core_files_quality
    ON ragintel.core_files (quality_score)
    WHERE quality_score IS NOT NULL;

-- Kalite eşikleri ve ağırlıklar — kod sabiti DEĞİL, config-first
INSERT INTO ragintel.app_config (config_key, config_value, description) VALUES
('quality', '{
  "weights": {"parse": 0.35, "clean": 0.20, "chunk": 0.25, "embed": 0.20},
  "parse":  {"hard_fail_coverage": 0.50, "hard_fail_garbage": 0.20, "soft_flag_coverage": 0.85},
  "clean":  {"hard_fail_retention": 0.60, "soft_flag_retention_low": 0.80, "soft_flag_retention_high": 0.999},
  "chunk":  {"soft_flag_truncated_ratio": 0.30, "target_token_p95": 512},
  "embed":  {"hard_fail_nan": 0},
  "ocr_fallback": {"enabled": true, "trigger_coverage_below": 0.50, "max_retry": 1}
}', 'ADR-011: aşama kalite eşikleri, bileşik skor ağırlıkları, OCR fallback kuralı')
ON CONFLICT (config_key) DO NOTHING;

COMMIT;

-- Doğrulama:
--   SELECT column_name FROM information_schema.columns
--     WHERE table_schema='ragintel' AND table_name='core_files' AND column_name='quality_score';
--   SELECT config_value->'weights' FROM ragintel.app_config WHERE config_key='quality';

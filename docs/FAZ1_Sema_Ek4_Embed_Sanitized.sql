-- ============================================================================
-- RAG v2 — FAZ 1 Şema Eki 4: qc_findings 'embed_sanitized' soft-flag  (v0.1, 2026-08-05)
-- ADDITIVE MIGRATION — FAZ1_Sema_Ek3_QC_Soft.sql sonrası uygulanır.
-- Gerekçe: Uzak embed ucu (H200 Open WebUI/Ollama proxy'si) belirli PIPE-ayraçlı
--          flatten-tablo token dizilerinde deterministik HTTP 500 veriyor (ölçüldü
--          2026-08-05: "5411 sayılı Bankacılık Kanunu.pdf" chunk#266). Embedder son
--          çare olarak ayracı temizlenmiş ('|'→' ') varyantı embed eder; SAKLANAN
--          chunk_text ORİJİNAL kalır, yalnız VEKTÖR sanitize metinden üretilir. Bu
--          sapma sessiz kalmasın diye 'embed_sanitized' soft bulgusuyla işaretlenir
--          (discipline b: kalite ölçümü sessizce fail-open olmaz). CHECK listesinde
--          yoktu → insert 'qc_findings_finding_check' ihlaliyle patlıyor, tüm dosya
--          transaction'ı rollback oluyordu (25869 FAILED). Bu ek onu giderir.
-- Uygulama (LOCAL):  psql "host=192.168.36.15 dbname=ragintel user=ragintel_app" -f FAZ1_Sema_Ek4_Embed_Sanitized.sql
-- Uygulama (H200/PROD, dump/restore ÖNCESİ zorunlu): aynı komut prod host'unda çalıştırılır
--   (yoksa restore edilen 'embed_sanitized' satırları prod CHECK'ini ihlal eder).
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
        -- Ek3: Ek-A soft flag'leri
        'low_coverage',
        'low_retention',
        'no_cleaning_effect',
        'chunk_truncation_high',
        'embed_anomaly',
        -- Ek4: son-çare embed sanitizasyonu (pipe→boşluk); vektör sanitize
        -- metinden, chunk_text orijinal. Bloklamaz; şeffaflık işareti.
        'embed_sanitized'
    ));

COMMENT ON CONSTRAINT qc_findings_finding_check ON ragintel.qc_findings IS
    'Ek4: embed_sanitized eklendi (son-çare pipe→boşluk embed fallback işareti). Soft; bloklamaz.';

COMMIT;

-- Doğrulama:
--   INSERT çalışmalı:  finding='embed_sanitized'
--   INSERT hata vermeli: finding='bilinmeyen'

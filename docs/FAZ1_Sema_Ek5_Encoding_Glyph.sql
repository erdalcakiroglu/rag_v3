-- ============================================================================
-- RAG v2 — FAZ 1 Şema Eki 5: qc_findings 'encoding_broken'/'encoding_repaired'
--                            (v0.1, 2026-08-10)
-- ADDITIVE MIGRATION — FAZ1_Sema_Ek4_Embed_Sanitized.sql sonrası uygulanır.
-- Gerekçe: Adım-4 glif onarımı (4c7b1c2 + bd3887b maliyet kapısı) parse sonrası
--          iki bulgu yazıyor: onarım uygulandıysa 'encoding_repaired', bozukluk
--          görülüp onarım yapılmadıysa (backend desteklemiyor / maliyet kapısı /
--          OCR başarısız / birleştirme kazanç getirmedi) 'encoding_broken'.
--          İKİSİ DE CHECK listesinde yoktu → insert 'qc_findings_finding_check'
--          ihlaliyle patlıyor, dosyanın TÜM transaction'ı rollback oluyordu.
--          Ölçüldü (2026-08-10, H200 korpus reprocess'i, 1118 dosya): kol 35
--          dosyada tetiklendi, 35'i de FAILED oldu; kalan 1083 COMPLETED. Yani
--          onarım çalışıyordu (fail_reason'daki satır: encoding_repaired
--          bozuk_sayfa=323/346), yazma katmanı reddediyordu. Ek4'te 'embed_
--          sanitized' ile birebir aynı kusur — bkz. tests/test_qc_finding_ddl.py,
--          artık kod ile DDL arasındaki fark testle yakalanıyor.
-- Uygulama (LOCAL):  psql "host=192.168.36.15 dbname=ragintel user=ragintel_app" -f FAZ1_Sema_Ek5_Encoding_Glyph.sql
-- Uygulama (H200/PROD, reprocess ÖNCESİ zorunlu): aynı komut prod host'unda çalıştırılır
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
        'embed_sanitized',
        -- Ek5: glif/font-kodlaması onarımı (parse). İkisi de soft; bloklamaz.
        --   encoding_repaired = tam-sayfa OCR koştu, bozuk sayfalar değiştirildi
        --   encoding_broken   = bozukluk GÖRÜLDÜ ama onarım uygulanmadı
        --                       (detail nedeni yazar: maliyet kapısı / backend /
        --                        OCR başarısız / kazanç yok)
        'encoding_repaired',
        'encoding_broken'
    ));

COMMENT ON CONSTRAINT qc_findings_finding_check ON ragintel.qc_findings IS
    'Ek5: encoding_repaired + encoding_broken eklendi (glif onarımı bulguları). Soft; bloklamaz.';

COMMIT;

-- Doğrulama:
--   SELECT pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conname = 'qc_findings_finding_check';
--   -> listede encoding_repaired ve encoding_broken görünmeli

-- =============================================================================
-- FAZ 3 — Retrieval şema ekleri (İP-3.6 tuning gereksinimleri)
-- =============================================================================
-- ELLE UYGULANIR (FAZ 2/4 deseni). Kod CREATE EXTENSION ÇAĞIRMAZ; bu dosya
-- İP-3.6'da elle kurulan uzantıların kalıcı kaydıdır.
--
-- Amaç: hibrit retrieval'ın Türkçe sparse varyantları için gerekli uzantılar.
--   - unaccent : aksansızlaştırma ('İsveç' ~ 'isvec'). hybrid_sparse_variant='unaccent'
--   - pg_trgm  : trigram/word_similarity. hybrid_sparse_variant='trgm'
--     NOT: trgm İP-3.6 ölçümünde ZARARLI çıktı (recall↓); config'te KAPALI
--     (sparse_variant='simple'). Uzantı yine de kurulur ki varyant seçilebilsin
--     ve gelecekte (büyük korpus) yeniden ölçülebilsin.
--
-- YETKİ: CREATE EXTENSION superuser (veya ilgili role) gerektirir. ragintel_app
--   uygulama kullanıcısında bu yetki YOKTUR; bu dosya bir DBA/superuser ile
--   uygulanmalıdır:
--     psql -h <host> -U <superuser> -d ragintel -f docs/FAZ3_Sema.sql
--
-- Idempotent: IF NOT EXISTS — tekrar uygulanabilir.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

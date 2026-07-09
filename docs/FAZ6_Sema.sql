-- =============================================================================
-- FAZ 6 — Kullanıcı deposu + AuthN (Bearer token → user_ctx)
-- =============================================================================
-- ELLE UYGULANIR (FAZ 2/4 deseni). Kod kalıcı CREATE ÇAĞIRMAZ.
-- Uygulama:  psql -h <host> -U <admin> -d ragintel -f docs/FAZ6_Sema.sql
--
-- Güvenlik: api_token_hash = sha256(token) HEX. DÜZ METİN TOKEN SAKLANMAZ.
-- allowed_doc_scopes retrieval fail-closed yetki evrenidir (boş → hiçbir şey görmez).
-- LDAP/AD (FAZ 9) bu tabloyu değiştirmeden resolver arayüzüne bağlanır (altlık).
--
-- Çekirdek sütunlar (görev spesifikasyonu): user_id, api_token_hash, display_name,
-- allowed_doc_scopes[], active. tenant_id + roles ileri-uyum için eklendi
-- (UserContext kontratı; varsayılanlı — onayınıza sunulur).
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS ragintel.users (
    user_id             TEXT PRIMARY KEY,
    api_token_hash      TEXT        NOT NULL UNIQUE,          -- sha256(token) hex; düz metin YOK
    display_name        TEXT,
    allowed_doc_scopes  TEXT[]      NOT NULL DEFAULT '{}',    -- yetki evreni (boş = fail-closed)
    active              BOOLEAN     NOT NULL DEFAULT true,
    tenant_id           TEXT        NOT NULL DEFAULT 'default',
    roles               TEXT[]      NOT NULL DEFAULT ARRAY['user'],
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Token çözümü aktif kullanıcıda hızlı olsun (resolver'ın tek sorgusu).
CREATE INDEX IF NOT EXISTS ix_users_active_token ON ragintel.users (api_token_hash) WHERE active;

COMMIT;

-- -----------------------------------------------------------------------------
-- Seed ÖRNEĞİ (uygulamayın — token'lar örnektir; gerçek token'ı siz üretin):
--   token'ı üretip hash'ini şöyle hesaplayın (Python):
--     import hashlib; hashlib.sha256(b"<RAW_TOKEN>").hexdigest()
--   INSERT INTO ragintel.users (user_id, api_token_hash, display_name, allowed_doc_scopes)
--   VALUES ('erdal', '<sha256hex>', 'Erdal', ARRAY['default']);
-- -----------------------------------------------------------------------------

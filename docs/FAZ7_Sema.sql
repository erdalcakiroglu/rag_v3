-- =============================================================================
-- FAZ 7 — Admin yetki modeli (users.is_admin)
-- =============================================================================
-- ELLE UYGULANIR. ADDITIVE (mevcut satırlar is_admin=false alır → kimse admin
-- olmaz; ilk admin'i elle set edersiniz). Admin-olmayan /admin isteği 403.
-- Uygulama:  psql -h <host> -U <admin> -d ragintel -f docs/FAZ7_Sema.sql
-- =============================================================================

BEGIN;

ALTER TABLE ragintel.users
    ADD COLUMN IF NOT EXISTS is_admin boolean NOT NULL DEFAULT false;

COMMIT;

-- İlk admin'i yetkilendirin (fail-closed: kimse otomatik admin değil):
--   UPDATE ragintel.users SET is_admin = true WHERE user_id = 'erdal';

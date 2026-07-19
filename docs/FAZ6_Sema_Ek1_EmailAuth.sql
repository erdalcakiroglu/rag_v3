-- =============================================================================
-- FAZ 6 Ek1 (M-12) — Email+şifre kimlik: self-kayıt (admin-onaylı) + giriş
-- =============================================================================
-- ELLE UYGULANIR (FAZ 2/4/6 deseni). Kod kalıcı ALTER ÇAĞIRMAZ.
-- Uygulama:  psql -h <host> -U <admin> -d ragintel -f docs/FAZ6_Sema_Ek1_EmailAuth.sql
--
-- Bu, FAZ6_Sema.sql'in ÜSTÜNE gelen ek katmandır (Bearer token yolunu DEĞİŞTİRMEZ):
--   • Bearer/servis/admin token yolu aynen kalır — `get_active_user_by_token_hash`
--     yalnız `active`'e bakar, `status`'u SORMAZ (regresyonsuz).
--   • email/şifre EKSTRA bir giriş yöntemidir; login başarısında üretilen opak session
--     token'ının HASH'i mevcut `api_token_hash` kolonuna yazılır (tek aktif oturum).
--
-- ÖN-VERİ (ölçüldü, 2026-07-19): users'ta 3 kullanıcı (envanter, erdal, erdal.cakiroglu),
-- hepsi token'lı + active. `created_at` ve `display_name` ZATEN VAR — yeniden eklenmez;
-- `full_name` için ayrı kolon açılmaz (kayıt display_name'e yazar). Delta = 3 kolon.
--
-- ⚠ ŞİFRE HASH'İ TOKEN'DAN FARKLI: password_hash = argon2id PHC string ($argon2id$...),
--   sha256 DEĞİL. Token yüksek-entropili (sha256 yeter); şifre düşük-entropili → yavaş,
--   salt'lı, bellek-sert hash ŞART. Düz metin şifre ne DB'de ne log'da tutulur.
-- =============================================================================

BEGIN;

-- 1) Yeni kolonlar (additive, idempotent). status varsayılan 'pending' = fail-closed.
ALTER TABLE ragintel.users
    ADD COLUMN IF NOT EXISTS email         TEXT,                 -- lower-normalize (uygulama + index)
    ADD COLUMN IF NOT EXISTS password_hash TEXT,                 -- argon2id PHC; düz metin YOK
    ADD COLUMN IF NOT EXISTS status        TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'disabled'));

-- 2) email büyük/küçük harf-DUYARSIZ benzersiz. Token kullanıcılarında email NULL →
--    kısmi index (WHERE email IS NOT NULL); UNIQUE'te NULL'lar eşit sayılmaz zaten,
--    ama partial index niyeti açık kılar ve NULL satırları index'e sokmaz.
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email_lower
    ON ragintel.users (lower(email)) WHERE email IS NOT NULL;

-- 3) Self-kayıt: pending kullanıcının token'ı YOKTUR (login'e dek). api_token_hash artık
--    NULL olabilmeli. UNIQUE korunur (NULL'lar eşit değildir → çoklu NULL serbest).
ALTER TABLE ragintel.users ALTER COLUMN api_token_hash DROP NOT NULL;

-- 4) MEVCUT kullanıcılar (token'lı = zaten onaylı-erişimli) → 'active'. Aksi halde adım (1)'in
--    'pending' varsayılanı onları login akışında kilitlerdi (Bearer'ları çalışmaya devam eder
--    ama panel/liste tutarlılığı için statüleri gerçeği yansıtmalı).
UPDATE ragintel.users SET status = 'active' WHERE api_token_hash IS NOT NULL;

COMMIT;

-- -----------------------------------------------------------------------------
-- Geri alma (gerekirse):
--   BEGIN;
--   DROP INDEX IF EXISTS ragintel.ux_users_email_lower;
--   ALTER TABLE ragintel.users DROP COLUMN IF EXISTS status;
--   ALTER TABLE ragintel.users DROP COLUMN IF EXISTS password_hash;
--   ALTER TABLE ragintel.users DROP COLUMN IF EXISTS email;
--   -- DİKKAT: api_token_hash NOT NULL'a geri almak, bu arada oluşmuş NULL'lı (pending)
--   -- satırlar varsa BAŞARISIZ olur; önce onları temizleyin.
--   ALTER TABLE ragintel.users ALTER COLUMN api_token_hash SET NOT NULL;
--   COMMIT;
-- -----------------------------------------------------------------------------

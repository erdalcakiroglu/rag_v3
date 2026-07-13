-- =============================================================================
-- FAZ 7 — Şema Eki 1: config_audit (app_config değişiklik günlüğü) — M-6
-- =============================================================================
-- ELLE UYGULANIR (FAZ 1/6/7 deseni). Kod kalıcı CREATE ÇAĞIRMAZ — DDL onaya gelir,
-- canlı DB'ye Erdal uygular.
-- Uygulama: psql "host=192.168.36.15 dbname=ragintel user=ragintel_app" -f docs/FAZ7_Sema_Ek1_ConfigAudit.sql
--
-- Amaç: ragintel.app_config'e yapılan HER değişiklik — admin panelinden VE
-- doğrudan SQL'den (psql UPDATE) — old/new değerleriyle kayda geçsin. Trigger
-- tabanlı: uygulama kod yolu ATLANSA (elle SQL) BİLE audit satırı oluşur.
--
-- --- changed_by kaynağı: ÖN-VERİ kararı (M-6) -------------------------------
-- İki aday değerlendirildi:
--   (1) NEW.updated_by kolonundan okumak (uygulama zaten yazıyor) — REDDEDİLDİ:
--       doğrudan SQL ile yapılan bir UPDATE, updated_by kolonuna DOKUNMAZSA
--       (ör. `UPDATE app_config SET config_value=... WHERE config_key=...;`),
--       o kolonda bir ÖNCEKİ (bambaşka) admin'in adı kalır. Trigger bunu
--       okursa, doğrudan-SQL değişikliğini YANLIŞ kişiye mal eder — bayat/
--       yanıltıcı bir isim audit'e düşer (sessizce yanlış, fark edilmesi zor).
--   (2) [SEÇİLDİ] PostgreSQL session-local GUC `app.changed_by`: uygulama,
--       app_config'e her yazımdan HEMEN ÖNCE AYNI transaction'da
--       `SELECT set_config('app.changed_by', <admin_user_id>, true);` çağırır
--       (bkz. ragintel/database/admin_repo.py — write_config/patch_config_field).
--       Trigger `current_setting('app.changed_by', true)` okur; session GUC
--       set EDİLMEMİŞSE (yani değişiklik uygulama kod yolundan GEÇMEMİŞ —
--       doğrudan SQL) `session_user`'a düşer. Bu, doğrudan-SQL değişikliklerini
--       DÜRÜSTÇE "hangi DB rolüyle yapıldı" diye işaretler (ör. `ragintel_app`
--       ya da psql'e bağlanan rol) — sahte bir insan adı UYDURMAZ.
--   `set_config(..., is_local=true)` TRANSACTION-scope'ludur: connection pool'a
--   geri dönen bağlantıda bir SONRAKİ isteğe SIZMAZ (her `with db.connection()`
--   bloğu = tek transaction; blok sonu commit → GUC kendiliğinden sıfırlanır).
--   Bu yüzden NEW.updated_by tamamen YOK SAYILIR (audit için tek kaynak GUC +
--   session_user fallback'idir) — audit doğruluğu, uygulamanın updated_by
--   kolonunu ayrıca doğru yazmasına BAĞIMLI DEĞİLDİR.
--
-- --- BİLİNÇLİ SINIRLAMA: DELETE audit'lenmez ---------------------------------
-- Trigger yalnızca INSERT/UPDATE'i yakalar. Config grupları pratikte SİLİNMEZ
-- (grup = pydantic GROUP_MODELS'te tanımlı; silinirse kod varsayılanına düşülür),
-- ayrıca `new_value NOT NULL` kısıtı bir DELETE kaydını zaten kabul etmezdi.
-- Silme audit'i gerekirse: new_value NOT NULL kaldırılmalı + TG_OP='DELETE' dalı
-- eklenmelidir (o gün bilinçli bir karar olsun diye burada yazıyor).
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS ragintel.config_audit (
    audit_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    config_key   text        NOT NULL,
    old_value    jsonb,                    -- İlk INSERT: önceki satır yok → NULL
    new_value    jsonb       NOT NULL,
    changed_by   text,                     -- GUC yoksa session_user (bkz. üstteki not)
    changed_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_config_audit_key_time
    ON ragintel.config_audit (config_key, changed_at DESC);

CREATE OR REPLACE FUNCTION ragintel.fn_audit_app_config()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_by text;
BEGIN
    -- current_setting(..., true): GUC set edilmemişse hata FIRLATMAZ, NULL döner.
    -- NULLIF(...,'') ŞART: Postgres'te özel (nokta'lı) bir GUC bir kez SET LOCAL
    -- ile dokunulduysa, o transaction bitse (commit) BİLE GUC'nin "placeholder"ı
    -- oturumda kalıcı olur ve sıfırlanmış hali NULL DEĞİL boş string ('') döner.
    -- Connection-pool'da AYNI fiziksel bağlantı BAŞKA bir isteğe (GUC set
    -- ETMEYEN bir doğrudan-SQL yazımına) verilirse, current_setting boş string
    -- döndürür — NULLIF olmadan COALESCE bunu "set edilmiş" sanıp session_user'a
    -- DÜŞMEZ, audit'e YANLIŞLIKLA boş bir changed_by yazılır. (Test'te bizzat
    -- yakalandı: bkz. tests/test_faz_m6_config_audit.py.)
    v_by := COALESCE(NULLIF(current_setting('app.changed_by', true), ''), session_user);
    IF TG_OP = 'INSERT' THEN
        INSERT INTO ragintel.config_audit (config_key, old_value, new_value, changed_by)
        VALUES (NEW.config_key, NULL, NEW.config_value, v_by);
    ELSIF TG_OP = 'UPDATE' THEN
        -- Yalnızca config_value GERÇEKTEN değiştiyse günlüğe yaz (ör. yalnızca
        -- description güncellenirse audit gürültüsü OLUŞMASIN).
        IF NEW.config_value IS DISTINCT FROM OLD.config_value THEN
            INSERT INTO ragintel.config_audit (config_key, old_value, new_value, changed_by)
            VALUES (NEW.config_key, OLD.config_value, NEW.config_value, v_by);
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER trg_app_config_audit
    AFTER INSERT OR UPDATE ON ragintel.app_config
    FOR EACH ROW EXECUTE FUNCTION ragintel.fn_audit_app_config();

COMMIT;

-- -----------------------------------------------------------------------------
-- Doğrulama (uygulama SONRASI, elle — psql'de sırayla çalıştırın):
--
-- DİKKAT: `SET config_value = config_value` (no-op) İŞE YARAMAZ — trigger'daki
-- `IS DISTINCT FROM` koruması değeri değişmemiş UPDATE'i günlüğe YAZMAZ ve
-- "trigger bozuk" izlenimi verir. Aşağıdaki reçete DEĞERİ GERÇEKTEN değiştirir,
-- sonra ESKİ HÂLİNE geri alır (net etki sıfır; audit'e 2 satır düşer).
--
--   -- 1) Uygulama/panel yolu (GUC set edilmiş): changed_by = 'erdal' beklenir.
--   BEGIN;
--     SELECT set_config('app.changed_by', 'erdal', true);
--     UPDATE ragintel.app_config
--        SET config_value = jsonb_set(config_value, '{lookup_window}', '3'::jsonb, true)
--      WHERE config_key = 'retrieval';
--   COMMIT;
--
--   -- 2) Doğrudan SQL yolu (GUC YOK — AYRI transaction): changed_by = session_user
--   --    (ör. 'ragintel_app') beklenir; 'erdal' DEĞİL. Aynı hamlede değeri GERİ ALIR.
--   UPDATE ragintel.app_config
--      SET config_value = jsonb_set(config_value, '{lookup_window}', '2'::jsonb, true)
--    WHERE config_key = 'retrieval';
--
--   -- 3) Sonuç: en yeni 2 satır — biri 'erdal', biri session_user.
--   SELECT audit_id, changed_by, old_value->'lookup_window' AS eski,
--          new_value->'lookup_window' AS yeni, changed_at
--     FROM ragintel.config_audit
--    WHERE config_key = 'retrieval' ORDER BY audit_id DESC LIMIT 2;
--
--   -- 4) Config gerçekten eski hâline döndü mü (lookup_window = 2)?
--   SELECT config_value->'lookup_window' FROM ragintel.app_config WHERE config_key='retrieval';
-- -----------------------------------------------------------------------------

-- =============================================================================
-- FAZ 7 — Şema Eki 2: core_chunks.table_id + satır aralığı — M-7 (a) / M-2b
-- =============================================================================
-- TASLAK — ONAY BEKLİYOR. ELLE UYGULANIR (FAZ 1/6/7 deseni); kod kalıcı CREATE
-- ÇAĞIRMAZ. Uygulama:
--   psql "host=192.168.36.15 dbname=ragintel user=ragintel_app" -f docs/FAZ7_Sema_Ek2_ChunkTableRef.sql
--
-- --- NEDEN: türetilmiş bağ → KALICI bağ ---------------------------------------
-- Bugün bir chunk'ın hangi tabloya ait olduğu ÇALIŞMA ZAMANINDA türetiliyor
-- (ragintel/database/table_repo.py):
--   path_a: section_title'ı regex ile ayrıştır ('tablo{index} · satır {n}-{m}'),
--           core_tables'a (file_id, table_index) üzerinden JOIN et.
--   path_b: chunk_text'i core_tables.table_text ile BİREBİR karşılaştır.
-- İkisi de kırılgan: başlık biçimi değişirse (M-1'de değişti) bağ sessizce kopar;
-- path_b metin eşitliğine dayanır (boşluk/normalize farkı bağı düşürür).
-- M-2b: bağ, chunk YAZILIRKEN kolona yazılır → okuma tek kolon SELECT'i olur,
-- regex/JOIN/metin-eşitliği yolları emekli edilir (B-kanaryası dahil).
--
-- --- SATIR ARALIĞI: iki kolon mu, int4range mi? -------------------------------
-- [SEÇİLDİ] iki ayrı kolon: table_row_start / table_row_end (int, 1-tabanlı,
--   HER İKİ UÇ DA DAHİL — inclusive). Gerekçe:
--     * table_repo'nun MEVCUT çıktısı zaten {table_id, row_start, row_end} —
--       kolonlar bire bir eşleşir, API kontratı DEĞİŞMEZ.
--     * int4range'in kapalı/açık uç semantiği ([a,b) varsayılanı) bu ekipte bir
--       kez daha "hangi uç dahil?" sorusu doğurur; kazanç yok, tuzak var.
--     * Aralık sorgusu (&&, @>) bu iş için gerekmiyor — chunk_id ile okunuyor.
--   Tablonun TAMAMI tek chunk ise (alt-chunk'a bölünmemiş, çoğunluk):
--     table_id DOLU, row_start/row_end NULL → "bu chunk tablonun tamamı".
--   Tablo-kökenli OLMAYAN chunk: table_id NULL (chunk'ların çoğu).
--
-- --- GERİYE DÖNÜK: kolonlar REPROCESS'e kadar NULL ----------------------------
-- Bu DDL yalnızca kolon açar; mevcut 1266 chunk'ta kolonlar NULL kalır. Kolonları
-- chunker YAZIM sırasında doldurur → değerler ancak TAM REPROCESS sonrası dolar
-- (M-7 Aşama 2). Bu yüzden table_repo, REPROCESS tamamlanana dek kolon-yolu ile
-- eski türetilmiş yol arasında GEÇİŞ yapabilmeli (kolon NULL ise eski yola düş);
-- geçiş kodu ve B-kanaryasının emekliliği Aşama 3'te, doğrulama yeşilken kalkar.
-- =============================================================================

BEGIN;

ALTER TABLE ragintel.core_chunks
    ADD COLUMN IF NOT EXISTS table_id        bigint REFERENCES ragintel.core_tables(table_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS table_row_start int,
    ADD COLUMN IF NOT EXISTS table_row_end   int;

COMMENT ON COLUMN ragintel.core_chunks.table_id IS
    'Chunk tablo-kökenliyse kaynak core_tables.table_id; değilse NULL (M-2b).';
COMMENT ON COLUMN ragintel.core_chunks.table_row_start IS
    'Alt-chunk''ın başladığı tablo satırı (1-tabanlı, DAHİL). Tablo tek chunk ise NULL.';
COMMENT ON COLUMN ragintel.core_chunks.table_row_end IS
    'Alt-chunk''ın bittiği tablo satırı (1-tabanlı, DAHİL). Tablo tek chunk ise NULL.';

-- Tutarlılık: satır aralığı yalnızca tablo-kökenli chunk'ta olabilir ve
-- start<=end olmalı. (Bozuk bağ SESSİZCE yazılmasın — yazımda patlasın.)
ALTER TABLE ragintel.core_chunks
    DROP CONSTRAINT IF EXISTS ck_core_chunks_table_rows;
ALTER TABLE ragintel.core_chunks
    ADD CONSTRAINT ck_core_chunks_table_rows CHECK (
        (table_row_start IS NULL AND table_row_end IS NULL)
        OR (table_id IS NOT NULL
            AND table_row_start IS NOT NULL AND table_row_end IS NOT NULL
            AND table_row_start >= 1
            AND table_row_start <= table_row_end)
    );

-- Okuma yolu: chunk_id ANY(...) ile gelir; table_id'ye göre ters arama (bir
-- tablonun chunk'ları) da ucuz olsun.
CREATE INDEX IF NOT EXISTS idx_core_chunks_table_id
    ON ragintel.core_chunks (table_id) WHERE table_id IS NOT NULL;

COMMIT;

-- -----------------------------------------------------------------------------
-- Doğrulama (uygulama SONRASI — REPROCESS'ten ÖNCE beklenen durum):
--   -- 1) Kolonlar açıldı, tümü NULL (henüz chunker doldurmadı):
--   SELECT count(*) AS toplam, count(table_id) AS table_id_dolu
--     FROM ragintel.core_chunks;
--   -- → table_id_dolu = 0 beklenir (REPROCESS sonrası > 0 olacak).
--
--   -- 2) CHECK kısıtı gerçekten koruyor mu (bilerek bozuk satır DENENİR, REDDEDİLMELİ):
--   BEGIN;
--     UPDATE ragintel.core_chunks SET table_row_start = 5, table_row_end = 2
--      WHERE chunk_id = (SELECT min(chunk_id) FROM ragintel.core_chunks);
--     -- → HATA beklenir: ck_core_chunks_table_rows ihlali (start > end)
--   ROLLBACK;
-- -----------------------------------------------------------------------------

-- =============================================================================
-- FAZ 7 Ek2 (M-13) — Konuşma geçmişi ("sohbetlerim" paneli)
-- =============================================================================
-- ELLE UYGULANIR (FAZ 2/4/6 deseni). Kod kalıcı CREATE ÇAĞIRMAZ.
-- Uygulama:  psql -h <host> -U <admin> -d ragintel -f docs/FAZ7_Sema_Ek2_Conversations.sql
--
-- MİMARİ (ön-veri ile kararlaştırıldı): kalıcı geçmiş POSTGRES'te (Redis DEĞİL —
-- allkeys-lru siler). Agent state zaten PostgresSaver checkpoint'inde (ADR-010) ama
-- oradaki `messages` TUR-YEREL scratchpad'tir (prepare her turda sıfırlar) → transcript
-- tek checkpoint'te YOKtur; 255 checkpoint'e yayılır ve langgraph-iç blob'a bağlıdır.
-- Bu yüzden kullanıcı-facing geçmiş AYRI iki tabloda tutulur:
--   • conversations         — kullanıcı-başına sohbet indeksi (liste/başlık/zaman)
--   • conversation_messages — her turun Q/A gövdesi (panel transcript'i BURADAN okur)
-- Checkpoint agent-state otoritesi kalır; bu tablolar ondan bağımsızdır.
--
-- SAHİPLİK fail-closed (M-2 deseni): her uç WHERE user_id = ctx.user_id; başka kullanıcının
-- conversation_id'si → 404 (varlık sızmaz). conversation_id = session_id (= checkpoint thread_id).
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS ragintel.conversations (
    conversation_id     TEXT PRIMARY KEY,                       -- = session_id (= thread_id)
    user_id             TEXT NOT NULL REFERENCES ragintel.users(user_id),
    title               TEXT NOT NULL,                          -- ilk sorudan ~50 char (LLM YOK — MVP)
    -- ⚠ SALT BİLGİ (rozet): sohbetin AÇILDIĞI GÜNKÜ scope anlık görüntüsü. YETKİ DEĞİLDİR —
    --   retrieval her istekte O ANKİ canlı user_ctx.allowed_doc_scopes ile sınırlanır (snapshot
    --   yetki genişletmez/daraltmaz). Yalnız "bu sohbet o gün şu scope'taydı" gösterimi için.
    allowed_doc_scopes  TEXT[] NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ                             -- soft-delete: NULL = canlı; veri/audit DURUR
);

-- Liste sorgusu: kullanıcının canlı sohbetleri, en son etkileşim üstte.
CREATE INDEX IF NOT EXISTS ix_conversations_user_last
    ON ragintel.conversations (user_id, last_at DESC) WHERE deleted_at IS NULL;


CREATE TABLE IF NOT EXISTS ragintel.conversation_messages (
    id                  BIGSERIAL PRIMARY KEY,
    conversation_id     TEXT NOT NULL REFERENCES ragintel.conversations(conversation_id),
    seq                 INT  NOT NULL,                          -- sohbet-içi sıra (1,2,3…)
    role                TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content             TEXT NOT NULL,                          -- soru ya da cevap metni (düz)
    trace_id            TEXT,                                   -- Langfuse trace'e bağ (gözlemlenebilirlik)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Transcript sorgusu: bir sohbetin mesajları sırayla.
CREATE INDEX IF NOT EXISTS ix_conversation_messages
    ON ragintel.conversation_messages (conversation_id, seq);

COMMIT;

-- -----------------------------------------------------------------------------
-- Geri alma (gerekirse):
--   BEGIN;
--   DROP TABLE IF EXISTS ragintel.conversation_messages;
--   DROP TABLE IF EXISTS ragintel.conversations;
--   COMMIT;
-- -----------------------------------------------------------------------------

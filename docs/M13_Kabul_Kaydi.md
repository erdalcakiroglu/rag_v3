# M-13 — Konuşma geçmişi ("sohbetlerim" paneli): CANLI KABUL KAYDI

**Tarih:** 2026-07-20  **Ortam:** H200 (GGB-AIApp01) — konteynerdeki app + yerel Redis (container, 6380) + uzak PostgreSQL. Gerçek DB, gerçek Redis, gerçek Ollama.
**Harness:** `scripts/m13_kabul.sh` + `scripts/m13_kabul.py` (tekrarlanabilir; geçici `kabul13-*` kullanıcı/sohbet/mesaj sonda temizlenir — koşuda 2 sohbet + 3 kullanıcı silindi).
**Sonuç:** **6/6 adım PASS** (5 container-içi + 3 host kontrolü).

## Kanıtlar (canlı çıktıdan)

| # | Adım | Kanıt |
|---|------|-------|
| 1 | Yeni soru → conversation | `http=200`, conv=`sess-f3aea607…`, `user=kabul13-a`, title=ilk soru, **scope-snapshot** `['default','envanter']` |
| 2 | Liste yalnız kendi | A conv1'i görür; **B görmez** (çapraz 0) |
| 3 | Transcript + sahiplik | `own=200` (2 mesaj Q/A); **çapraz (kabul13-b) → 404** (varlık sızmaz, M-2) |
| 4 | Soft-delete | `http=200`, liste'de yok, **AMA satır+mesaj DB'de durur** (`deleted_at` dolu, 2 mesaj — SQL kanıtı) |
| 5 | Redis-down'da geçmiş | Redis durdur → `/api/conversations` **200** + `/api/conversations/{id}` **200** (Postgres kalıcılık; DB-token auth Redis'ten bağımsız); Redis geri → `redis:ok` |
| 6 | **ZOMBİ-YETKİ** (kritik) | scope `['default','envanter']`→`['default']` daralt: `/api/table/1665` (envanter) **daraltma öncesi 200 → sonrası 404**; snapshot GENİŞ (`['default','envanter']`) kalsa da canlı scope (`['default']`) enforce → **snapshot yetki DEĞİL** (deterministik, vacuous değil) |

## Ön-veri (kod öncesi ölçüm — tasarımı belirledi)

Checkpoint `messages` TUR-YEREL scratchpad ölçüldü → transcript tek checkpoint'te yok, 255 checkpoint'e yayılı + langgraph-iç blob'a bağlı → **ayrı `conversation_messages` tablosu**. Agent önceki turları enjekte etmiyor (memory_search stub) → "çok-turlu bağlam" iddia edilmedi; **panel-only** (agent hafızası → M-14 backlog). `allowed_doc_scopes` snapshot **salt bilgi** (yetki değil) — adım 6 canlı kanıtladı.

**Karar:** M-13 (konuşma geçmişi paneli: liste/yükle/sil + salt-okuma transcript, Postgres kalıcı, sahiplik fail-closed, scope-snapshot salt-bilgi) canlıda **KABUL EDİLDİ**.

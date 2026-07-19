# M-12 — Email+şifre kimlik + Redis oturum deposu: CANLI KABUL KAYDI

**Tarih:** 2026-07-20  **Ortam:** H200 (GGB-AIApp01) — konteynerdeki app + **yerel Redis (container, localhost:6380)** + uzak PostgreSQL (192.168.36.15). Gerçek Redis, gerçek DB, gerçek Ollama.
**Harness:** `scripts/m12_kabul.sh` + `scripts/m12_kabul.py` (tekrarlanabilir; geçici `kabul-*` kullanıcıları sonda temizlenir).
**Sonuç:** **9/9 adım PASS** (13 container-içi + 5 host kontrolü).

## Kanıtlar (canlı çıktıdan)

| # | Adım | Kanıt |
|---|------|-------|
| 1 | Kayıt | allowlist-içi → `pending` + `scope=[]`; allowlist-dışı → **403**; duplicate → **nötr** (aynı yanıt, tek satır — enumeration yok) |
| 2 | Pending giriş | onaysız login → **403** ("onay bekliyor"), veri yok |
| 3 | Admin onay | pending → `active` + scope atandı (`['default']`) |
| 4 | Giriş | 200 + **Redis session SET**, `ttl=28800s`; yanlış şifre = bilinmeyen email → **aynı nötr 401**; token ile `/api/ask` → 200 (5 kaynak, scope=default) |
| 5 | Scope izolasyonu | **çift-yönlü, vacuous değil**: kabul-a görür `['default']`, kabul-b görür `['envanter']` — her biri SADECE kendini (Redis session yolu) |
| 6 | Logout | `/api/logout` → session **DEL** → aynı token **401** (anında iptal) |
| 7 | TTL sliding | istek öncesi `30s` → sonrası `28790s` (hareketsizlik penceresi tazelendi) |
| 8 | Redis-down tatbikatı | yeni login → **503**; health → **degraded** + `redis:down`; **admin BEARER (DB token) → 200** (ayrım canlı); Redis geri → `redis:ok` |
| 9 | Şifre güvenliği | `password_hash` = **argon2id** (`$argon2id$…`); DB'de düz şifre **YOK**; app loglarında düz şifre **YOK** |

## Güvenlik sözleşmesi — canlı doğrulandı

Redis ERİŞİLEMEZ iken: **login (Redis-otoriter oturum) çalışmaz (503)**, ama **admin/servis Bearer token yolu (DB `api_token_hash`) SAĞLAM (200)**. Fail-closed de fail-open da değil — iki yol net ayrık (adım 8).

## Ön-veri (kod öncesi ölçüm)

- Redis app→bağlantı smoke (ping+set/get/ttl, requirepass/6380/erişim) geçti.
- DDL uygulandı (users'ta email/password_hash/status kolonları ölçüldü); korpusta iki scope (`default`, `envanter`) → adım 5 gerçek iki-taraflı.

**Karar:** M-12 (email+şifre self-kayıt + admin onay + Redis-otoriter oturum + logout/iptal) canlıda **KABUL EDİLDİ**.

# RHEL Kurulum Rehberi — Langfuse v3 (Podman ile, Docker'sız)

**Proje:** RAG v2 — FAZ 2 / İP-2.2 (tracing platformu, ADR-013)
**Hedef sunucu:** 192.168.36.15 (DB sunucusu) — ayrı sunucu tercih ederseniz IP'leri değiştirin
**Yaklaşım:** Podman (RHEL yerleşik) + podman-compose; resmi Langfuse v3 compose dosyasının ortamınıza uyarlanmış hali
**Kritik uyarlama:** Langfuse'un kendi Postgres/Redis container'ları vardır — sunucudaki mevcut PG17 (5432) ve Redis (6379) ile çakışmasın diye host'a port AÇILMAZ (yalnızca container-içi ağda kalırlar; ragintel DB'sine hiçbir şekilde dokunmazlar).

---

## 0. Ön Koşullar

- RAM kontrolü: Langfuse yığını (web+worker+clickhouse+redis+minio+pg) ~4-6 GB ek RAM ister. `free -h` ile mevcut PG17 + Redis'ten sonra en az 8 GB boş olduğunu doğrulayın; yoksa ayrı sunucu düşünün.
- İnternet erişimi: imajlar docker.io ve cgr.dev'den çekilir (air-gapped ise imajları erişimli makinede `podman save/load` ile taşıyın).

## 1. Podman Kurulumu

```bash
sudo dnf install -y podman

# podman-compose: EPEL'den (tercih) veya pip ile
sudo dnf install -y epel-release && sudo dnf install -y podman-compose
# EPEL yoksa alternatif:  pip3 install --user podman-compose

podman --version && podman-compose --version
```

Rootless çalışacağız (root gerekmez, güvenli varsayılan). Oturum kapansa da container'lar yaşasın:

```bash
loginctl enable-linger $USER
```

## 2. Dizin ve Secret Üretimi

```bash
mkdir -p ~/langfuse && cd ~/langfuse

# Secret'ları üretin ve NOT ALIN (aşağıdaki .env'e girecek):
# ÖNEMLİ: hepsi HEX — base64 KULLANMAYIN (+ / = karakterleri ClickHouse
# migration URL'ini bozar: "Authentication failed" — yaşandı, 2026-07-02)
openssl rand -hex 32   # → ENCRYPTION_KEY
openssl rand -hex 24   # → NEXTAUTH_SECRET
openssl rand -hex 16   # → SALT
openssl rand -hex 24   # → POSTGRES_PASSWORD (Langfuse'un KENDİ pg'si)
openssl rand -hex 24   # → CLICKHOUSE_PASSWORD
openssl rand -hex 24   # → REDIS_AUTH (Langfuse'un KENDİ redis'i)
openssl rand -hex 24   # → MINIO_ROOT_PASSWORD
openssl rand -hex 16   # → LANGFUSE_INIT_USER_PASSWORD (UI girişiniz)
```

## 3. `.env` Dosyası (`~/langfuse/.env`)

`***` yerlerine 2. adımda ürettiklerinizi koyun. `LANGFUSE_INIT_*` blok ilk açılışta org/proje/kullanıcı ve API anahtarlarını otomatik oluşturur — UI'da elle kurulum gerekmez ve anahtarlar baştan bellidir.

```env
# --- Erişim ---
NEXTAUTH_URL=http://192.168.36.15:3000
TELEMETRY_ENABLED=false

# --- Secrets ---
SALT=***
ENCRYPTION_KEY=***            # openssl rand -hex 32 (64 hex karakter)
NEXTAUTH_SECRET=***

# --- Langfuse'un kendi Postgres'i (container-içi; ragintel DB'siyle İLGİSİZ) ---
POSTGRES_PASSWORD=***
DATABASE_URL=postgresql://postgres:AYNI_POSTGRES_SIFRESI@postgres:5432/postgres

# --- ClickHouse / Redis / MinIO (container-içi) ---
CLICKHOUSE_PASSWORD=***
REDIS_AUTH=***
MINIO_ROOT_PASSWORD=***
LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=AYNI_MINIO_SIFRESI
LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY=AYNI_MINIO_SIFRESI
LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY=AYNI_MINIO_SIFRESI

# --- İlk kurulum: org/proje/kullanıcı/API anahtarları otomatik ---
LANGFUSE_INIT_ORG_ID=ragintel
LANGFUSE_INIT_ORG_NAME=RagIntel
LANGFUSE_INIT_PROJECT_ID=rag-v2
LANGFUSE_INIT_PROJECT_NAME=RAG v2
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=pk-lf-ragintel-dev-0001
LANGFUSE_INIT_PROJECT_SECRET_KEY=sk-lf-***RASTGELE***
LANGFUSE_INIT_USER_EMAIL=erdalcakiroglu@gmail.com
LANGFUSE_INIT_USER_NAME=Erdal
LANGFUSE_INIT_USER_PASSWORD=***
```

## 4. `docker-compose.yml` (`~/langfuse/docker-compose.yml`)

Resmi dosyanın uyarlanmış hali. **Değişiklikler:** postgres/redis host portları KALDIRILDI (çakışma önlemi), clickhouse/minio yalnızca 127.0.0.1'e bağlı, web 3000 dışa açık.

```yaml
services:
  langfuse-worker:
    image: docker.io/langfuse/langfuse-worker:3
    restart: always
    depends_on: &langfuse-depends-on
      postgres: { condition: service_healthy }
      minio: { condition: service_healthy }
      redis: { condition: service_healthy }
      clickhouse: { condition: service_healthy }
    ports:
      - 127.0.0.1:3030:3030
    environment: &langfuse-worker-env
      NEXTAUTH_URL: ${NEXTAUTH_URL}
      DATABASE_URL: ${DATABASE_URL}
      SALT: ${SALT}
      ENCRYPTION_KEY: ${ENCRYPTION_KEY}
      TELEMETRY_ENABLED: ${TELEMETRY_ENABLED:-false}
      CLICKHOUSE_MIGRATION_URL: clickhouse://clickhouse:9000
      CLICKHOUSE_URL: http://clickhouse:8123
      CLICKHOUSE_USER: clickhouse
      CLICKHOUSE_PASSWORD: ${CLICKHOUSE_PASSWORD}
      CLICKHOUSE_CLUSTER_ENABLED: "false"
      LANGFUSE_S3_EVENT_UPLOAD_BUCKET: langfuse
      LANGFUSE_S3_EVENT_UPLOAD_REGION: auto
      LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID: minio
      LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY: ${LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY}
      LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT: http://minio:9000
      LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE: "true"
      LANGFUSE_S3_EVENT_UPLOAD_PREFIX: events/
      LANGFUSE_S3_MEDIA_UPLOAD_BUCKET: langfuse
      LANGFUSE_S3_MEDIA_UPLOAD_REGION: auto
      LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID: minio
      LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY: ${LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY}
      LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT: http://127.0.0.1:9090
      LANGFUSE_S3_MEDIA_UPLOAD_FORCE_PATH_STYLE: "true"
      LANGFUSE_S3_MEDIA_UPLOAD_PREFIX: media/
      REDIS_HOST: redis
      REDIS_PORT: "6379"
      REDIS_AUTH: ${REDIS_AUTH}

  langfuse-web:
    image: docker.io/langfuse/langfuse:3
    restart: always
    depends_on: *langfuse-depends-on
    ports:
      - 3000:3000
    environment:
      <<: *langfuse-worker-env
      NEXTAUTH_SECRET: ${NEXTAUTH_SECRET}
      LANGFUSE_INIT_ORG_ID: ${LANGFUSE_INIT_ORG_ID}
      LANGFUSE_INIT_ORG_NAME: ${LANGFUSE_INIT_ORG_NAME}
      LANGFUSE_INIT_PROJECT_ID: ${LANGFUSE_INIT_PROJECT_ID}
      LANGFUSE_INIT_PROJECT_NAME: ${LANGFUSE_INIT_PROJECT_NAME}
      LANGFUSE_INIT_PROJECT_PUBLIC_KEY: ${LANGFUSE_INIT_PROJECT_PUBLIC_KEY}
      LANGFUSE_INIT_PROJECT_SECRET_KEY: ${LANGFUSE_INIT_PROJECT_SECRET_KEY}
      LANGFUSE_INIT_USER_EMAIL: ${LANGFUSE_INIT_USER_EMAIL}
      LANGFUSE_INIT_USER_NAME: ${LANGFUSE_INIT_USER_NAME}
      LANGFUSE_INIT_USER_PASSWORD: ${LANGFUSE_INIT_USER_PASSWORD}

  clickhouse:
    # PIN: 'latest' hibrit CPU'lu VM'lerde "illegal instruction" verebilir;
    # 24.8 LTS bu ortamda doğrulandı (Sorun Giderme bölümüne bakın)
    image: docker.io/clickhouse/clickhouse-server:24.8
    restart: always
    user: "101:101"
    environment:
      CLICKHOUSE_DB: default
      CLICKHOUSE_USER: clickhouse
      CLICKHOUSE_PASSWORD: ${CLICKHOUSE_PASSWORD}
    volumes:
      - langfuse_clickhouse_data:/var/lib/clickhouse
      - langfuse_clickhouse_logs:/var/log/clickhouse-server
    ports:
      - 127.0.0.1:8123:8123
    healthcheck:
      test: wget --no-verbose --tries=1 --spider http://localhost:8123/ping || exit 1
      interval: 5s
      timeout: 5s
      retries: 10
      start_period: 1s

  minio:
    image: cgr.dev/chainguard/minio
    restart: always
    entrypoint: sh
    command: -c 'mkdir -p /data/langfuse && minio server --address ":9000" --console-address ":9001" /data'
    environment:
      MINIO_ROOT_USER: minio
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    ports:
      - 127.0.0.1:9090:9000
      - 127.0.0.1:9091:9001
    volumes:
      - langfuse_minio_data:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 1s
      timeout: 5s
      retries: 5
      start_period: 1s

  redis:
    image: docker.io/redis:7
    restart: always
    command: >
      --requirepass ${REDIS_AUTH}
      --maxmemory-policy noeviction
    volumes:
      - langfuse_redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 3s
      timeout: 10s
      retries: 10

  postgres:
    image: docker.io/postgres:17
    restart: always
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 3s
      timeout: 3s
      retries: 10
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: postgres
      TZ: UTC
      PGTZ: UTC
    volumes:
      - langfuse_postgres_data:/var/lib/postgresql/data

volumes:
  langfuse_postgres_data:
  langfuse_clickhouse_data:
  langfuse_clickhouse_logs:
  langfuse_minio_data:
  langfuse_redis_data:
```

> Dikkat: `postgres` ve `redis` servislerinde `ports:` bloğu YOK — bilinçli. Host'taki PG17/Redis ile çakışmazlar; Langfuse container'ları kendi iç ağından erişir.

## 5. Başlatma ve Doğrulama

```bash
cd ~/langfuse
podman-compose up -d          # ilk seferde imaj indirme birkaç dakika sürer
podman ps                      # 6 container "healthy/running" olmalı

# Web hazır mı (ilk açılışta migration ~1-2 dk sürebilir):
curl -s http://localhost:3000/api/public/health
# → {"status":"OK", ...}
```

Tarayıcıdan (dev makinenizden): `http://192.168.36.15:3000` → `.env`'deki INIT_USER e-posta/parolayla giriş. "RAG v2" projesi hazır gelmeli.

## 6. Açılışta Otomatik Başlama (rootless)

```bash
systemctl --user enable --now podman-restart
```

`restart: always` politikalı container'lar reboot sonrası otomatik kalkar (linger 1. adımda açıldı).

## 7. Firewall

```bash
# 3000'i YALNIZCA dev makinenizin IP'sine açın:
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="DEV_MAKINE_IP/32" port protocol="tcp" port="3000" accept'
sudo firewall-cmd --reload
```

Diğer tüm Langfuse portları zaten 127.0.0.1'e bağlı — ek kural gerekmez.

## 8. Uygulama Tarafı (.env — repo)

```env
RAGINTEL_LANGFUSE_HOST=http://192.168.36.15:3000
RAGINTEL_LANGFUSE_PUBLIC_KEY=pk-lf-ragintel-dev-0001
RAGINTEL_LANGFUSE_SECRET_KEY=sk-lf-***(.env'dekiyle aynı)***
```

İP-2.2 kodu bu üç değişkeni okuyacak (OTel exporter hedefi).

## 9. Kontrol Listesi

- [ ] `podman ps` → 6 container healthy
- [ ] `curl localhost:3000/api/public/health` → OK
- [ ] Dev makinesinden UI'a giriş başarılı, "RAG v2" projesi görünüyor
- [ ] Host PG17 (`systemctl status postgresql-17`) ve host Redis etkilenmedi (port çakışması yok)
- [ ] Reboot testi: sunucu yeniden başlatıldığında yığın kendiliğinden kalkıyor
- [ ] 3000 yalnızca dev IP'ye açık (`ss -tlnp` + firewall kuralı)

## Sorun Giderme

- `podman-compose up` sırasında imaj çekme hatası → `podman login docker.io` gerekmiyor (anonim çekilir); kurumsal proxy varsa `~/.config/containers/registries.conf` proxy ayarı gerekir.
- Web 3000'de ama health FAIL → `podman logs langfuse_langfuse-web_1` (migration bekliyor olabilir).
- ClickHouse "healthy" olmuyor → RAM yetersizliği en sık neden; `free -h` kontrol edin.

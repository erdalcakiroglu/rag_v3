# RHEL Kurulum Rehberi — PostgreSQL 17 + pgvector + Redis

**Proje:** RAG v2 — FAZ 1/2 altyapı temeli
**Hedef:** Docker kullanmadan, RHEL sunucu üzerine bare-metal kurulum
**Kapsam:** PostgreSQL 17, pgvector, Redis, `ragintel` veritabanı hazırlığı, temel tuning
**Varsayım:** RHEL 9 (RHEL 8 farkları notlarda), x86_64, sudo yetkisi, internet erişimi (air-gapped için Bölüm 8)

---

## 1. Ön Hazırlık

```bash
# Sistem güncel mi
sudo dnf update -y

# RHEL sürümünü doğrula
cat /etc/redhat-release
```

## 2. PostgreSQL 17 Kurulumu (PGDG Reposu)

RHEL'in kendi AppStream reposundaki PostgreSQL sürümü eskidir; resmi PGDG reposu kullanılır.

```bash
# PGDG repo kur (RHEL 9)
sudo dnf install -y https://download.postgresql.org/pub/repos/yum/reporpms/EL-9-x86_64/pgdg-redhat-repo-latest.noarch.rpm
# RHEL 8 için: .../EL-8-x86_64/pgdg-redhat-repo-latest.noarch.rpm

# Dahili postgresql modülünü devre dışı bırak (çakışmayı önler)
sudo dnf -qy module disable postgresql

# PostgreSQL 17 server + contrib
sudo dnf install -y postgresql17-server postgresql17-contrib

# Cluster'ı başlat
sudo /usr/pgsql-17/bin/postgresql-17-setup initdb

# Servisi etkinleştir ve başlat
sudo systemctl enable --now postgresql-17
sudo systemctl status postgresql-17
```

**Doğrulama:**

```bash
sudo -u postgres /usr/pgsql-17/bin/psql -c "SELECT version();"
```

## 3. pgvector Kurulumu

pgvector, PGDG reposunda hazır paket olarak bulunur (derleme gerekmez):

```bash
sudo dnf install -y pgvector_17
```

> **Not:** Paket bulunamazsa `sudo dnf search pgvector` ile tam adı kontrol edin.
> Kaynak koddan derleme (alternatif): `postgresql17-devel` + `make` gerektirir; PGDG paketi varken önerilmez.

## 4. Veritabanı ve Şema Hazırlığı (ragintel)

```bash
sudo -u postgres /usr/pgsql-17/bin/psql
```

```sql
-- Uygulama rolü (şifreyi değiştirin!)
CREATE ROLE ragintel_app LOGIN PASSWORD 'DEGISTIR_guclu_sifre';

-- Veritabanı
CREATE DATABASE ragintel OWNER ragintel_app ENCODING 'UTF8';

-- ragintel DB'sine bağlan
\c ragintel

-- pgvector extension (superuser gerektirir, DB başına bir kez)
CREATE EXTENSION IF NOT EXISTS vector;

-- Şema
CREATE SCHEMA IF NOT EXISTS ragintel AUTHORIZATION ragintel_app;

-- Doğrulama
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
```

**Hızlı fonksiyon testi:**

```sql
CREATE TABLE ragintel.vector_test (id int, emb vector(3));
INSERT INTO ragintel.vector_test VALUES (1, '[1,2,3]'), (2, '[4,5,6]');
SELECT id, emb <=> '[1,2,3]' AS cosine_dist FROM ragintel.vector_test ORDER BY cosine_dist;
DROP TABLE ragintel.vector_test;
```

**BGE-M3 için örnek vektör tablosu (1024 boyut) ve HNSW index:**

```sql
CREATE TABLE ragintel.core_vectors (
    chunk_id   bigint PRIMARY KEY,
    embedding  vector(1024) NOT NULL
);

-- HNSW index (cosine). Not: index build RAM'i maintenance_work_mem'den kullanır
CREATE INDEX idx_core_vectors_hnsw
    ON ragintel.core_vectors USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
```

## 5. PostgreSQL Tuning (32 GB RAM sunucu için başlangıç değerleri)

`/var/lib/pgsql/17/data/postgresql.conf` (veya `ALTER SYSTEM SET ...`):

```ini
# Bellek
shared_buffers = 8GB                    # RAM'in ~%25'i
effective_cache_size = 24GB             # RAM'in ~%75'i
work_mem = 64MB
maintenance_work_mem = 2GB              # HNSW index build için kritik

# Paralellik (8 core varsayımı)
max_worker_processes = 8
max_parallel_workers = 8
max_parallel_workers_per_gather = 4
max_parallel_maintenance_workers = 4    # HNSW build hızlandırır

# WAL / yazma (ingestion batch yükü için)
wal_buffers = 64MB
checkpoint_completion_target = 0.9
max_wal_size = 4GB

# Bağlantı
listen_addresses = 'localhost'          # uygulama aynı sunucudaysa localhost kalsın
max_connections = 100
password_encryption = scram-sha-256
```

```bash
sudo systemctl restart postgresql-17
```

**pg_hba.conf** (`/var/lib/pgsql/17/data/pg_hba.conf`) — uygulama aynı makinedeyse:

```
# TYPE  DATABASE   USER          ADDRESS        METHOD
local   ragintel   ragintel_app                 scram-sha-256
host    ragintel   ragintel_app  127.0.0.1/32   scram-sha-256
```

```bash
sudo systemctl reload postgresql-17

# Bağlantı testi
psql "host=127.0.0.1 dbname=ragintel user=ragintel_app" -c "SELECT current_database();"
```

## 6. Redis Kurulumu

RHEL 9 AppStream'de Redis 6.2 bulunur; Redis 7 modül stream'i sürüme göre mevcuttur:

```bash
# Mevcut stream'leri kontrol et
sudo dnf module list redis

# Redis 7 stream varsa (önerilen):
sudo dnf module enable -y redis:7
sudo dnf install -y redis

# Yoksa 6.2 de FAZ 1-2 için yeterlidir:
# sudo dnf install -y redis
```

**Yapılandırma** — `/etc/redis/redis.conf`:

```ini
bind 127.0.0.1 -::1              # sadece localhost
requirepass DEGISTIR_redis_sifre # şifre zorunlu
maxmemory 4gb
maxmemory-policy allkeys-lru     # cache kullanım profili
appendonly no                    # FAZ 1: sadece cache/job-state, kalıcılık gerekmez
```

```bash
sudo systemctl enable --now redis
redis-cli -a 'DEGISTIR_redis_sifre' ping     # → PONG
```

> **Hatırlatma (ADR-006):** Semantic cache ileride devreye girdiğinde anahtarlar tenant + permission context içermek zorunda. FAZ 1'de Redis yalnızca opsiyonel job/queue içindir.

## 7. Güvenlik: Firewall ve SELinux

```bash
# Her şey aynı sunucudaysa dışarıya port AÇMAYIN (varsayılan doğru).
# Uygulama ayrı sunucudaysa sadece o IP'ye:
# sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="10.0.0.5/32" port protocol="tcp" port="5432" accept'
# sudo firewall-cmd --reload

# SELinux: Enforcing kalsın. Yerel soket bağlantılarında ek ayar gerekmez.
getenforce
```

## 8. Air-Gapped (İnternetsiz) Kurulum Notu

İnternet erişimi olmayan sunucu için, erişimi olan bir RHEL makinede paketleri indirin:

```bash
sudo dnf download --resolve --downloaddir=/tmp/pg17 \
    postgresql17-server postgresql17-contrib pgvector_17 redis
# /tmp/pg17 içeriğini hedef sunucuya taşıyıp:
sudo dnf install -y /path/to/pg17/*.rpm
```

Ayrıca BGE-M3 model dosyaları (~2.2 GB) HuggingFace'ten önceden indirilip taşınmalıdır (`HF_HUB_OFFLINE=1` ile kullanılır).

## 9. Kurulum Sonrası Kontrol Listesi

- [ ] `SELECT version();` → PostgreSQL 17.x
- [ ] `SELECT extversion FROM pg_extension WHERE extname='vector';` → 0.7+
- [ ] HNSW index'li test tablosunda `<=>` sorgusu çalışıyor
- [ ] `ragintel_app` rolü ile scram-sha-256 bağlantısı başarılı
- [ ] `redis-cli ping` → PONG (şifreli)
- [ ] 5432/6379 portları dışarıya kapalı (`ss -tlnp` ile doğrula)
- [ ] `maintenance_work_mem = 2GB` aktif (`SHOW maintenance_work_mem;`)
- [ ] Otomatik başlatma: `systemctl is-enabled postgresql-17 redis` → enabled

## 10. Sonraki Adım

Bu altyapı üzerine FAZ 1 şeması kurulur: `core_files`, `core_chunks`, `core_vectors`, `metrics_*` tabloları (alembic migration ile). Ardından Docling parser PoC ve BGE-M3 embedding hattı bağlanır.

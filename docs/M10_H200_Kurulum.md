# H200 Kurulum — ragintel-api

**Uygulama kılavuzu.** Baştan sona kopyala-yapıştır. Neden'ler burada değil:
tasarım kararları ve ölçümler için bkz. [M10_Deploy_Kurulum.md](M10_Deploy_Kurulum.md).

| | |
|---|---|
| Hedef | `ragintel-api` konteyneri, H200'ün üzerinde, **doğrudan Ollama**'ya (`:11434`) |
| Dizin | `/opt/ragintel` |
| Dal | `feat/h200-transition` |
| Ağ | `network_mode: host` — Ollama `localhost`, DB ağda |
| Rerank | **TEI kapalı** — `retrieval.rerank_backend = "passthrough"` (DB) |
| Kapsam DIŞI | Ollama, PostgreSQL kurulumu (hazır kabul edilir) · ingest imajı (docling'li, ayrı) |

---

## 0. Ön koşullar — kuruluma başlamadan DOĞRULAYIN

Hepsi geçmeden ilerlemeyin; sonraki adımlar bunların üstüne kuruluyor.

```bash
# Docker + Compose v2
docker --version && docker compose version

# Ollama ayakta ve modeller yerinde (qwen3.5:35b + bge-m3)
curl -fsS http://localhost:11434/api/tags | grep -oE '"name":"[^"]+"'

# DB erişilebilir mi (adresi kendi değerinizle)
nc -zv <DB_ADRES> 5432
```

> **TEI GEREKMİYOR** — `retrieval.rerank_backend` DB'de `"passthrough"`. TEI hiç
> çağrılmaz ve compose'da URL'i de **kapalıdır**. Kurmayın, aramayın.
> Yalnızca `rerank_backend` DB'den `'tei'` yapılırsa gerekir (mimari karar);
> o zaman TEI ayağa kaldırılır + compose'daki `RAGINTEL_TEI_RERANK_URL` satırı açılır.

> Build **internet ister** (tokenizer + pip, ~5-10 dk, tek seferlik).
> Runtime kapalı ağda çalışır — tokenizer imaja gömülüdür.

---

## 1. Kod

```bash
git clone <repo-url> /opt/ragintel
cd /opt/ragintel
git checkout feat/h200-transition
```

### Sunucuda daha önce elle düzeltme yapıldıysa — ÖNCE temizleyin

Arıza avında `.dockerignore` / `Dockerfile` / `requirements-api.txt` gibi **izlenen**
dosyalar `vi`/`sed` ile düzenlendiyse `git pull --ff-only` çakışır. Kalıcı
düzeltmeler artık repoda; yerel yamalara gerek yok:

```bash
cd /opt/ragintel
git status --short                  # ne değişmiş, görün
git checkout -- .                   # izlenen dosyaları repoya döndür
rm -f .dockerignore.backup.*        # arıza avından kalan yedekler
git pull --ff-only
```

`.env.h200` bu komutlardan **etkilenmez** (izlenmiyor) — yerinde kalır.

---

## 2. `.env.h200` — sırlar + makineye özgü bağlantı

Repoya **girmez** (`.gitignore`/`.dockerignore` dışlar). İmaja da girmez.

```bash
cd /opt/ragintel
vi .env.h200
chmod 600 .env.h200
```

Şablon:

```bash
# --- DB (ağdaki sunucu) — TAMAMI burada, compose'da DEĞİL ---
RAGINTEL_DB_HOST=10.50.130.55
RAGINTEL_DB_PORT=5432
RAGINTEL_DB_NAME=ragintel
RAGINTEL_DB_SCHEMA=ragintel
RAGINTEL_DB_USER=ragintel_app
RAGINTEL_DB_PASSWORD=<parola>

# --- Langfuse (opsiyonel; kapalıysa satırları boş bırakın) ---
RAGINTEL_LANGFUSE_HOST=http://127.0.0.1:3000
RAGINTEL_LANGFUSE_PUBLIC_KEY=<pk>
RAGINTEL_LANGFUSE_SECRET_KEY=<sk>

# --- API auth (varsa) ---
RAGINTEL_API_TOKEN=<token>
```

**Zorunlu üçlü:** `RAGINTEL_DB_HOST`, `RAGINTEL_DB_USER`, `RAGINTEL_DB_PASSWORD`.
`deploy.sh` bunları build'den ÖNCE denetler ve eksikse **değerleri basmadan**
(yalnızca `SET`/`EKSİK`) durur. `PORT`/`NAME`/`SCHEMA` isteğe bağlıdır — kod
varsayılanları `5432` / `ragintel` / `ragintel`.

> **Buraya yazılan LLM/Ollama/TEI değerleri ETKİSİZDİR.** Onlar
> `docker-compose.h200.yml` içindeki `environment:` bloğunda durur ve compose'da
> `environment:` her zaman `env_file:`'ı ezer. Bu dosya **yalnızca** DB + sırlar içindir.

> **Dış-API anahtarı YOK** — LLM ve embedding lokal (v2.10 istisnası kapandı).

---

## 3. Dağıt

```bash
cd /opt/ragintel
chmod +x deploy.sh
./deploy.sh
```

> **`./deploy.sh`** — `sh deploy.sh` **DEĞİL**. Script bash dizileri kullanır;
> `sh` altında bozulur (script bunu artık kendisi reddediyor).

`deploy.sh` sırayla: `.env.h200` ön-doğrulama → `git pull` → build → `up -d
--force-recreate` → health doğrulama (en çok 180 sn). Herhangi bir adım düşerse
**durur** — yarım dağıtım, çalışan eski sürümden kötüdür.

### Beklenen çıktı

```
==> .env.h200 ön-doğrulama
    RAGINTEL_DB_HOST: SET
    RAGINTEL_DB_USER: SET
    RAGINTEL_DB_PASSWORD: SET
==> git pull
==> dağıtılan sürüm: f12eab8
==> build
BUILD DOGRULAMA 1 OK — torch/docling yok, .env yok, tokenizer offline (5 token)
BUILD DOGRULAMA 2 OK — bootstrap os.environ zinciri calisiyor: build-smoke.invalid
==> up
 ✔ Container ragintel-api  Recreated
==> health bekleniyor (en çok 180 sn)
{"status":"healthy","checks":{...},"git_sha":"f12eab8"}
==> DAĞITIM TAMAM (f12eab8)
```

İki `BUILD DOGRULAMA` satırı **görünmelidir**. Build kendi kendini denetler:
kırıksa üretimde değil build'de patlar.

---

## 4. Doğrulama

```bash
# 1) Koşan kod, dağıttığımız kod mu?
curl -s http://localhost:8000/api/health | python3 -m json.tool
git rev-parse --short HEAD          # health'teki git_sha ile AYNI olmalı

# 2) Konteyner ayakta ve restart döngüsünde değil
docker compose -f docker-compose.h200.yml ps

# 3) Uçtan uca soru
curl -s -X POST http://localhost:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"karbon vergisi nedir?"}' | head -c 400
```

`status` değerleri: `healthy` · `degraded` (bir alt sistem düşük) · `warming`
(ilk warm-up sürüyor, ~1 dk) · `unhealthy` (checks'e bakın).

Beklenen `checks`: `db: ok` · `ollama: ok` · **`tei: disabled`** (URL kapalı —
rerank passthrough; bu degrade ETMEZ) · `langfuse: enabled|disabled` · `warmup: ok`.

---

## 5. Sorun giderme — yaşanmış arızalar

| belirti | sebep | çözüm |
|---|---|---|
| `failed to compute cache key: "/README.md": not found` | `.dockerignore` inline yorumu istisnayı bozuyor (eski sürüm) | `git pull` — `f12eab8`'de düzeltildi |
| `OfflineModeIsEnabled: Cannot reach .../BAAI/bge-m3` | tokenizer repo-id ile yükleniyor (eski sürüm) | `git pull` — yerel dizinden yükleniyor artık |
| `MissingBootstrapSetting: RAGINTEL_DB_HOST tanımsız` | `.env.h200`'de `RAGINTEL_DB_HOST` yok **veya** eski kod (os.environ okumuyordu) | Satırı ekleyin; kod eskiyse `git pull` |
| `bash: ./deploy.sh: /usr/bin/env bash^M: bad interpreter` | CRLF satır sonu | `git checkout -- deploy.sh` (`.gitattributes` LF'i zorlar) |
| `./deploy.sh: Permission denied` | çalıştırma izni yok | `chmod +x deploy.sh` |
| `deploy.sh: syntax error near unexpected token` | `sh deploy.sh` ile çağrılmış | `./deploy.sh` |
| `git pull --ff-only` çakışıyor | sunucuda elle düzenleme | `git checkout -- .` (bkz. §1) |
| health 180 sn yanıt vermedi | konteyner açılışta ölüyor | Log'a bakın ↓ |
| `Container ... is restarting` | aynı — açılışta ölüyor, restart döngüsü | Log'a bakın ↓ |
| `"status":"degraded"` + `checks.tei` `ok` değil | `RAGINTEL_TEI_RERANK_URL` dolu ama TEI ayakta değil. rerank `passthrough` olduğu için **işlev kaybı yok** — yalnızca yanlış alarm | compose'da o satır **kapalı** olmalı (`c14a95a` sonrası kapalı) → `tei: disabled` → `healthy` |
| `curl: (7) ... port 8085: Connection refused` | TEI yok — **beklenen**, gerekmiyor | Yok sayın (bkz. §0) |

```bash
docker compose -f docker-compose.h200.yml logs --tail 60 ragintel-api
```

---

## 6. Günlük işlemler

```bash
cd /opt/ragintel

./deploy.sh                 # pull + build + up + doğrulama
./deploy.sh --no-pull       # yerel kodla (git'e dokunmaz)

docker compose -f docker-compose.h200.yml logs -f ragintel-api    # canlı log
docker compose -f docker-compose.h200.yml ps                      # durum
docker compose -f docker-compose.h200.yml restart ragintel-api    # yeniden başlat
docker compose -f docker-compose.h200.yml down                    # durdur
```

---

## 7. Güvenlik

- **`docker compose config` çıktısını paylaşmayın.** `env_file`'ı çözer ve DB
  parolasıyla Langfuse anahtarlarını **açıkça basar**. Log/ekran görüntüsüne
  girdiyse ilgili anahtarlar **döndürülmelidir**.
- `.env.h200` → `chmod 600`, repoya girmez, imaja girmez. Build, `/app/.env`
  sızmadığını `assert` ile doğrular.
- Konteyner ortamını incelerken değeri değil durumu basın:
  ```bash
  docker exec ragintel-api sh -lc 'for v in RAGINTEL_DB_HOST RAGINTEL_DB_USER RAGINTEL_DB_PASSWORD; do
    [ -n "$(printenv "$v")" ] && echo "$v=SET" || echo "$v=MISSING"; done'
  ```

---

## 8. Kurulumdan sonra — stabilizasyon testi

Doğrudan-Ollama geçişinin ikinci amacı **tanı**: M-9.1'deki
`500 failed to parse JSON: invalid character 'H'` hatası Open WebUI katmanında
çıkıyordu. Ölçüm planı ve suçlu-tespiti tablosu:
[M10_Deploy_Kurulum.md §5](M10_Deploy_Kurulum.md).

```bash
python scripts/ollama_dogrudan_smoke.py --n 20 --istek /yol/m91_gs012_istek.json
```

> `m91_gs012_istek.json` korpus içeriği taşıdığı için **repoda değildir** — H200'e
> ayrıca kopyalanmalı. Dosya yoksa betik bu adımı **atlar ve açıkça söyler**;
> sessizce "temiz" demez.

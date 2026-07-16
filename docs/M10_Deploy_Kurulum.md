# M-10/0 — ragintel-api Container/Deploy (H200)

**Durum:** dosyalar hazır, **H200'de uygulanacak** (Erdal). GPU beklemeden yazıldı.

## 1. Ne değişiyor — ve neden iki işi birden yapıyor

API artık **H200'ün üzerinde**, **doğrudan Ollama'ya** (`:11434`) konuşarak koşacak —
Open WebUI (`/ollama/v1`) üzerinden DEĞİL.

| | eski | yeni |
|---|---|---|
| API nerede | geliştirici makinesi | H200 (konteyner) |
| LLM yolu | Open WebUI → Ollama | **doğrudan Ollama** |
| Ağ | internet üzerinden HTTPS | `localhost` (host-network) |
| Auth | Bearer (WebUI anahtarı) | gerekmiyor (auth'suz uç) |

**İkinci fayda — tanı.** M-9.1'de bulunan `500 failed to parse JSON: invalid character 'H'`
hatası (submit_answer tool-call'unda, citations JSON'unda) **Open WebUI katmanında** ortaya
çıkıyordu. Doğrudan Ollama o katmanı devreden çıkarır:

- **Temiz çıkarsa** → suçlu WebUI'ydi, taşınma arızayı **çözer**.
- **Hâlâ kırıksa** → suçlu Ollama/model; sürüm kontrolü / model kararı **ayrı karar** (mimara gider).

Bu, §5'teki test planıyla **ölçülerek** karara bağlanır — tahminle değil.

## 2. `.env.h200` (H200'de oluşturulur, repoya GİRMEZ)

**Sırlar + makineye özgü bağlantı bilgisi.** Mimari sabitler (doğrudan Ollama, TEI,
host-network) compose'da açık durur.

```bash
# --- DB (ağdaki sunucu) — TAMAMI burada ---
# Compose'da DEĞİL: adres makineye özgü ve değişiyor (192.168.36.15 → 10.50.130.55).
# Compose'da `environment:` her zaman `env_file:`'ı EZER; oraya yazılan bir adres
# buradaki DOĞRU değeri sessizce eskiye döndürürdü.
RAGINTEL_DB_HOST=<adres>
RAGINTEL_DB_PORT=5432
RAGINTEL_DB_NAME=ragintel
RAGINTEL_DB_SCHEMA=ragintel
RAGINTEL_DB_USER=ragintel_app
RAGINTEL_DB_PASSWORD=<parola>

# --- Langfuse (opsiyonel; kapalıysa boş bırakılabilir) ---
RAGINTEL_LANGFUSE_HOST=http://127.0.0.1:3000
RAGINTEL_LANGFUSE_PUBLIC_KEY=<pk>
RAGINTEL_LANGFUSE_SECRET_KEY=<sk>

# --- API auth (varsa) ---
RAGINTEL_API_TOKEN=<token>
```

> Dış-API anahtarı **YOK** — v2.10 istisnası kapandı, LLM/embedding lokal.

**Zorunlu olanlar** (kodda `_require`): `RAGINTEL_DB_HOST`, `RAGINTEL_DB_USER`,
`RAGINTEL_DB_PASSWORD`. `deploy.sh` bunları açılıştan ÖNCE denetler ve eksikse
**değerleri basmadan** (yalnızca SET/EKSİK) durur. PORT/NAME/SCHEMA'nın kod
varsayılanı vardır — zorunlu değiller.

### Bu değerler konteynere NASIL ulaşıyor (M-10/0'da düzeltildi)

Bootstrap ayarları (`DbSettings` vd.) eskiden **yalnızca `.env` dosyasını** okuyordu;
os.environ kasıtlı olarak zincir dışıydı (M-4 PARÇA 3: bayat host env `.env`'i ezmesin).
İmajda `.env` yoktur ve **olmamalıdır** → compose'un geçirdiği hiçbir değer görülmüyordu:

```
konteynerde:  os.getenv('RAGINTEL_DB_HOST') -> '10.50.130.55'
              DbSettings().host             -> ''          ← arıza buydu
```

Yeni öncelik (`settings.DotenvFirstSettings`):

    init kwarg > .env > OS ortam değişkeni > kod varsayılanı

`.env` VARSA (geliştirici makinesi) tekel onundur — M-4'ün amacı korunur.
`.env` YOKSA (konteyner) ortam konuşur. Build, bunu sahte bir değerle **doğrular**
(`BUILD DOGRULAMA 2`) — bir daha sessizce kırılamaz.

### Güvenlik

`docker compose config` çıktısı `env_file`'ı çözer ve **parolaları/anahtarları açıkça
basar**. Paylaşmadan önce maskeleyin; log/ekran görüntüsüne girdiyse ilgili anahtarlar
**döndürülmelidir**. `.env.h200` hem `.gitignore` hem `.dockerignore` tarafından
dışlanır (`.env.*` deseni — `.env` deseni onu kapsamıyordu, M-10/0'da eklendi) ve
`chmod 600 .env.h200` önerilir.

## 3. Kurulum (H200, tek sefer)

```bash
# 1) kod
git clone <repo> /opt/ragintel && cd /opt/ragintel
git checkout feat/h200-transition

# 2) sırlar
vi .env.h200          # §2 şablonu
chmod 600 .env.h200

# 3) dağıt (pull → build → up → health doğrulama)
chmod +x deploy.sh && ./deploy.sh

# sonraki dağıtımlar:
./deploy.sh            # veya --no-pull (yerel kodla)
```

> **`./deploy.sh`** — `sh deploy.sh` DEĞİL. Script bash dizileri kullanır; `sh`
> altında bozulur. (Script artık bunu kendisi denetleyip açık hata veriyor.)

### Sunucuda elle düzeltme yapıldıysa — ÖNCE geri alın

Arıza avında `.dockerignore` / `Dockerfile` / `requirements-api.txt` gibi **izlenen**
dosyalar sunucuda düzenlendiyse `git pull --ff-only` çakışır. Kalıcı düzeltmeler
repoda olduğu için yerel yamalara artık gerek yok:

```bash
git -C /opt/ragintel status --short          # ne değişmiş, görün
git -C /opt/ragintel stash                   # (yedek isterseniz) veya:
git -C /opt/ragintel checkout -- .           # izlenen dosyaları repoya döndür
rm -f /opt/ragintel/.dockerignore.backup.*   # arıza avından kalan yedekler
./deploy.sh
```

`.env.h200` bu komutlardan **etkilenmez** (izlenmiyor) — yerinde kalır.

Build ~5-10 dk (tokenizer indirme dahil, tek seferlik). **İnternet yalnızca build'de**
gerekir; runtime kapalı ağda çalışır.

### Beklenen çıktı
```
==> .env.h200 ön-doğrulama
    RAGINTEL_DB_HOST: SET
    RAGINTEL_DB_USER: SET
    RAGINTEL_DB_PASSWORD: SET
==> dağıtılan sürüm: d8634de
==> build
BUILD DOGRULAMA 1 OK — torch/docling yok, .env yok, tokenizer offline (5 token)
BUILD DOGRULAMA 2 OK — bootstrap os.environ zinciri calisiyor: build-smoke.invalid
==> up
 ✔ Container ragintel-api  Recreated          ← "Started" değil (--force-recreate)
==> health bekleniyor
{"status":"healthy","checks":{...},"git_sha":"d8634de"}
==> DAĞITIM TAMAM (d8634de)
```

## 4. İmaj tasarımı — ölçülmüş kararlar

| karar | gerekçe (ÖLÇÜLDÜ) |
|---|---|
| **torch/docling YOK** | `import ragintel.api.app` sonrası `sys.modules`'te docling/torch **yok** — docling `parsing/backends.py` içinde LAZY. İmaj ~2.5 GB → ~700 MB sınıfı. Build'de `assert` ile kilitlendi |
| **`--no-deps` ile kurulum** | ŞART: düz `pip install .` pyproject'teki docling'i geri getirir, torch'u çeker → imajın anlamı kaybolur |
| **`libmagic1` apt** | import-time ZORUNLU: `ingestion/__init__` eager zinciri `filetypes`(magic) çeker. Yoksa konteyner **açılışta** patlar |
| **tokenizer build'de gömülü, YEREL DİZİNDEN** | `save_pretrained(/opt/models/bge-m3-tokenizer)` + `RAGINTEL_TOKENIZER_DIR`. Repo-id (`"BAAI/bge-m3"`) ile çağırmak kapalı ağda ÖLÜYOR — cache dolu ve `HF_HUB_OFFLINE=1` olsa bile. Traceback: `_patch_mistral_regex` → `if _is_local or is_base_mistral(id)` → `model_info(id)`. `or` kısa devre yapar: kaynak yerel DİZİNSE `_is_local=True` → ağ çağrısı hiç olmaz. `local_files_only=True` tek başına KURTARMIYOR (ölçüldü) |
| **transformers 4.57.3'te kaldı** | 4.57.1'e düşürmek de arızayı gizliyor (H200'de denendi, build geçti) ama sebebi sürüm değil ÇAĞRI BİÇİMİ — 4.57.3+ geri geldiğinde arıza döner. Yerel dizin, sürümden bağımsız keser |
| **bootstrap os.environ'u okur** | `.env` ÖNCELİKLİ, ortam YEDEK. İmajda `.env` yok → compose'un geçirdiği değerler görülmeliydi; görülmüyordu (bkz. §2) |
| **`!README.md` istisnası** | `.dockerignore` INLINE YORUM TANIMAZ — `!README.md  # açıklama` deseni bozar, README.md dışlanır, `COPY` "not found" der. Yorum satırın ÜSTÜNDE |
| **pyproject'e dokunulmadı** | lokal geliştirme `pip install -e .` ile ingestion dahil kurulmaya devam eder; ayrım yalnızca imajda |
| **`git_sha` health'te** | `deploy.sh` dağıttığı sürümle kıyaslar → cache'li/yanlış imaj sessizce eski kod sunamaz |
| **build kendi kendini denetler** | `BUILD DOGRULAMA 1`: torch/docling yok + `/app/.env` sızmamış + tokenizer offline çalışıyor · `BUILD DOGRULAMA 2`: bootstrap os.environ'u gerçekten okuyor (sahte değerle). Kırıksa üretimde değil BUILD'de patlar |

> **ingest bu imajda KOŞMAZ** (docling yok). Doküman yükleme, docling'li tam kurulumla
> ayrıca yürütülür — bilinçli kapsam kararı.

## 5. Doğrudan-Ollama uyum + STABİLİZASYON TEST PLANI

```bash
# H200'de, dağıtımdan önce veya sonra:
python scripts/ollama_dogrudan_smoke.py --n 20 --istek /yol/m91_gs012_istek.json
```

### Smoke listesi (betiğin kontrol ettikleri)

| # | kontrol | beklenti | etkisi |
|---|---|---|---|
| 0 | `/api/tags` canlılık + modeller yüklü | qwen3.5:35b + bge-m3 | temel |
| 1 | **Bearer'sız istek** kabul mü? | 200 | auth'suz uç varsayımı; `RAGINTEL_*_API_KEY` boş bırakılabilir |
| 2 | **openai SDK boş api_key**'i kabul eder mi? | ? | reddederse compose'daki `ollama-local` yer tutucusu **gerekli**; kabul ederse satır silinir |
| 3 | **`:latest`** etiketi kabul mü? | `bge-m3` ve `bge-m3:latest` ikisi de 200 | `ollama_wire_tag` zararsız mı? **damga (`bge-m3@ollama`) DEĞİŞMEZ** — korpus uyumu korunur |
| 4 | **Kayıtlı gerçek istek ×20** | ⬇ aşağıda | **suçlu tespiti** |

### §5.4 — suçlu tespiti (asıl soru)

`m91_gs012_istek.json`, M-9.1'de yakalanmış **gerçek** submit_answer isteğidir (7 mesaj,
citations'ında `"Hakan ÖZDEMİR"` quote'u — hatanın tetikleyicisi). Doğrudan `:11434`'e ×20 gider.

Betik yalnızca HTTP koduna bakmaz; **200 dönse bile tool-call `arguments`'ının geçerli JSON
olduğunu doğrular** (asıl kırılma noktası orası).

| sonuç | yorum | sonraki adım |
|---|---|---|
| **20/20 temiz** | suçlu **Open WebUI katmanı**ydı | taşınma çözdü → temiz k=1 karne + M-9.1 kırık-soru turu |
| **hâlâ kırık** | suçlu **Ollama/model** | AYRI KARAR: Ollama sürüm kontrolü; gerekirse model denemesi (qwen3.6) — mimara gider |

> Bu adım olmadan "suçlu WebUI'ydi" **sonucu çıkarılamaz** — betik dosya yoksa bunu
> açıkça söyler ve sessizce "temiz" demez.

## 6. Bekleyen / kapsam dışı

- **ingest imajı** — docling'li ayrı imaj (bu turda kapsam dışı).
- **Seviye B/C dağıtım** (rollback, mavi-yeşil) — Seviye A yeterli görüldü.
- **eval koşusu ile serving aynı GPU** — operasyonel risk, backlog'ta duruyor.

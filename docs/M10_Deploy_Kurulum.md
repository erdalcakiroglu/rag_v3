# M-10/0 — ragintel-api Container/Deploy (H200): kararlar ve gerekçeler

> **Kurulum yapacaksanız bu dosya değil** → [M10_H200_Kurulum.md](M10_H200_Kurulum.md)
> (adım adım, kopyala-yapıştır, sorun giderme, `.env.h200` şablonu).
>
> Burası **neden** dosyasıdır: mimari değişiklik, ölçülmüş imaj kararları ve
> stabilizasyon test planı.

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

## 2. Config nereye yazılır — ve neden

**İki kanal, iki gerekçe.** Şablon ve adımlar kurulum dosyasındadır
([M10_H200_Kurulum.md §2](M10_H200_Kurulum.md)) — burada yalnızca ayrımın nedeni.

| kanal | ne durur | neden |
|---|---|---|
| `docker-compose.h200.yml` → `environment:` | **mimari/dağıtım sabitleri**: doğrudan Ollama (`localhost:11434/v1`), TEI, host-network | Repoda **dokümante** olmalı: "hangi mimariyle koşuyoruz" kod incelemesinde görünsün |
| `.env.h200` (repoya girmez) | **sırlar + makineye özgü bağlantı**: `RAGINTEL_DB_*`, Langfuse, API token | Makineden makineye değişir; parolayla aynı dosyada durması doğaldır |

**DB neden compose'da DEĞİL:** compose'da `environment:` her zaman `env_file:`'ı
**ezer**. Repoda sabitlenmiş bir `RAGINTEL_DB_HOST: 192.168.36.15`, adres
`10.50.130.55`'e taşındığında `.env.h200`'deki doğru değeri **sessizce** eskiye
döndürürdü. Bağlantı bilgisi repoda sabitlenmez.

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
basar** — paylaşmadan önce maskeleyin; log/ekran görüntüsüne girdiyse ilgili anahtarlar
**döndürülmelidir**. `.env.h200` hem `.gitignore` hem `.dockerignore` tarafından
dışlanır (`.env.*` deseni — `.env` deseni onu kapsamıyordu, M-10/0'da eklendi).
Operasyonel ayrıntı: [M10_H200_Kurulum.md §7](M10_H200_Kurulum.md).

## 3. Kurulum

Adım adım kurulum, doğrulama, sorun giderme ve günlük işlemler:
**[M10_H200_Kurulum.md](M10_H200_Kurulum.md)**.

Özet akış — `deploy.sh` (Seviye A): `.env.h200` ön-doğrulama → `git pull` → build
→ `up -d --force-recreate` → health + `git_sha` doğrulama. Herhangi bir adım düşerse
durur: **yarım dağıtım, çalışan eski sürümden kötüdür**. Build ~5-10 dk (tokenizer
indirme dahil, tek seferlik); **internet yalnızca build'de** gerekir, runtime kapalı
ağda çalışır.

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

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

Yalnızca **sırlar** ve makineye özgü değerler. Geri kalan her şey compose'da açık.

```bash
# --- DB (ağdaki sunucu) ---
RAGINTEL_DB_USER=ragintel_app
RAGINTEL_DB_PASSWORD=<parola>

# --- Langfuse (opsiyonel; kapalıysa boş bırakılabilir) ---
RAGINTEL_LANGFUSE_HOST=http://192.168.36.15:3000
RAGINTEL_LANGFUSE_PUBLIC_KEY=<pk>
RAGINTEL_LANGFUSE_SECRET_KEY=<sk>

# --- API auth (varsa) ---
RAGINTEL_API_TOKEN=<token>
```

> Dış-API anahtarı **YOK** — v2.10 istisnası kapandı, LLM/embedding lokal.

## 3. Kurulum (H200, tek sefer)

```bash
# 1) kod
git clone <repo> /opt/ragintel && cd /opt/ragintel
git checkout feat/h200-transition

# 2) sırlar
vi .env.h200          # §2 şablonu

# 3) dağıt (pull → build → up → health doğrulama)
chmod +x deploy.sh && ./deploy.sh

# sonraki dağıtımlar:
./deploy.sh            # veya --no-pull (yerel kodla)
```

Build ~5-10 dk (tokenizer indirme dahil, tek seferlik). **İnternet yalnızca build'de**
gerekir; runtime kapalı ağda çalışır.

### Beklenen çıktı
```
==> dağıtılan sürüm: d8634de
BUILD DOGRULAMA OK — torch/docling yok, tokenizer offline calisiyor (5 token)
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
| **tokenizer build'de gömülü** | `HF_HOME=/opt/hf` + `HF_HUB_OFFLINE=1`. Runtime'da HF'ye çıkma girişimi olmaz (kapalı ağ) |
| **pyproject'e dokunulmadı** | lokal geliştirme `pip install -e .` ile ingestion dahil kurulmaya devam eder; ayrım yalnızca imajda |
| **`git_sha` health'te** | `deploy.sh` dağıttığı sürümle kıyaslar → cache'li/yanlış imaj sessizce eski kod sunamaz |

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

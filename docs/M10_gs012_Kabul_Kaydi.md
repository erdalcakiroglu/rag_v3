# M-10/0 — gs-012 (bozuk tool-call JSON → fallback): CANLI KABUL KAYDI

**Tarih:** 2026-07-20  **Ortam:** H200 (GGB-AIApp01) — konteynerdeki app + uzak PostgreSQL + yerel Ollama. Gerçek DB, gerçek Ollama, DB-token'lı gerçek kullanıcı.
**Harness:** `scripts/m10_gs012_x20.sh` + `scripts/m10_gs012_x20.py` (tekrarlanabilir; geçici `kabul10-a` DB-token kullanıcısı; tetikleyici soruyu ×20 sorar, sınıflar CLEAN/EXC_FALLBACK/NO_ANSWER/NO_SRC/HTTP_ERR; .sh loglarda `tool_call_parse_error`/`api_ask_failed` sayar).
**Sonuç:** **20/20 CLEAN**, 0 fallback, yazar-doğru 20/20.

## Teşhis zinciri (kök nereye oturdu)

1. **Belirti:** `"failed to parse JSON: invalid character …"` → Ollama Go parser 500 → `litellm.InternalServerError` → `runtime.ask` yakalar → `EXC_FALLBACK` ("Sistem şu anda yanıt üretemedi").
2. **WebUI elendi:** Aynı kırılma doğrudan Ollama `/api/chat` ile de tekrarlandı → suçlu arayüz değil.
3. **Kök = MODEL:** Model `submit_answer` **tool-call argümanlarını** bozuk JSON üretiyor (TR özel-adlı citations tetikliyor; tetik makale file 8001 `16-KARBON_…pdf`, scope=default). İstek-içinde deterministik (temp=0 → aynı hata), istekler-arası stokastik (Ollama/GPU non-determinizmi).
4. **Oran modele göre uçurum (canlı ×20):** **qwen3.5:35b ≈ %10-15 ilk-deneme kusuru**; **qwen3.6:latest = %100 felaket** (pertürbasyonla bile kurtulmuyor). → **qwen3.6:latest'e geçiş REGRESYON'du, aday dışı.**

## Uygulanan fix (katmanlı retry — model-agnostik dayanıklılık)

Gateway'de tool-call parse hatasına **ayrı retry** (`RAGINTEL_LLM_TOOLCALL_RETRIES=2`), rate-limit döngüsünden bağımsız; parse-hata transient döngüde beklemeden yükselir.

**Pertürbasyon SON ÇARE (Erdal sıralaması, b117e8e):** deneme-1 temp=0 (M-9 mührü) → ara denemeler temp=0 (düz retry; Ollama non-determinizmi zaten oynatıyor, kendi kendine düzelebilir) → **yalnız son deneme** temp=`retry_temp`. Sıcaklık dizisi `[0, 0, 0.5]`.

**Karne mührü ayrışması (meta):** kazanan temp>0 ise `LLMResponse.perturbation_rescued=True` + `sampling_temperature` + Langfuse span `llm.perturbation_rescued` → o yanıt M-9 tekrarlanabilirlik iddiasından **ayrışır**. temp=0 retry kurtarması **işaretlenmez** (mühür içinde).

## Kanıtlar (canlı ×20 çıktıdan)

| Ölçüt | Değer | Yorum |
|---|---|---|
| CLEAN | **20/20** | fallback=0, NO_ANSWER=0, NO_SRC=0, HTTP_ERR=0 |
| yazar-doğru | **20/20** | citations doğru (ÖZDEMİR/KÖSE) |
| retry ateşleme | **3/20** (≈%15) | model HÂLÂ ~%15 bozuk JSON üretiyor → retry hepsini kurtardı |
| pertürbasyon (temp>0) | **0 kez** | 3 kurtarmanın hepsi **temp=0 düz-retry**'de → 20 yanıt da temp=0 → **M-9 mührü TAM** |
| latency (ms) | min=23801 medyan=27115 ort=31460 p95=57423 max=57423 | aşağıya bak |

## Latency doğru okuma (ayrı perf konusu)

Retry vergisi **küçük**: yalnız 3/20, sadece p95 kuyruğunu (57s) şişiriyor. Medyan ~27s + min 23.8s = **BASE tek-çağrı maliyeti** (qwen3.5:35b ~24-27s/generation) — retry değil. Base yavaşlık **ayrı** bir perf konusu (reasoning-token üretimi şüphesi; bkz M-15 backlog). Grammar-constrained (C) bunu **çözmez**.

## (C) grammar-constrained kök çözüm — PoC SONUCU: ELENDİ (latency)

**PoC koşuldu (2026-07-20, `scripts/c_grammar_poc.sh qwen3.5:35b 20`): VALID=5/20, JSON_INVALID=0,
SCHEMA_BAD=0, HTTP_ERR=15.** Kritik okuma: grammar **correctness'te başarılı** — tamamlanan 5
üretimde 0 bozuk JSON, 0 şema-dışı, yazar-doğru 5/5. **HTTP_ERR=15 = hepsi `ReadTimeout` (180s).**
Grammar-constrained decoding **feci yavaş**: min=57.8s, medyan=180s (timeout tavanı), max=180s →
retry çözümünün base ~27s'ine göre **2-6× yavaş, kullanılamaz**.

**Muhtemel kök (GBNF patolojisi):** `submit_answer` şemasında `citations[]` **sınırsız dizi**;
grammar + temp=0 greedy decode citation nesnelerini durmadan üretip tıkanıyor (şema kaynaklı,
model değil). **Karar: (C) bu haliyle NO-GO — correctness değil LATENCY nedeniyle.** Retry çözümü
(20/20, base ~27s) açık ara üstün; gs-012 zaten çözülü, pivot'a gerek yok.

**Gelecek ipucu (backlog, mimara gider):** grammar *correctness* çalışıyor; tek blokör sınırsız
`citations[]`. `maxItems` ile şemayı sınırlayıp tekrar ölçmek ucuz bir prob — greenlight değil,
yalnız açık kapı.

**Karar:** gs-012 (bozuk tool-call JSON → fallback) canlıda **KABUL EDİLDİ** — correctness çözüldü (20/20, fallback=0, M-9 mührü korunuyor). Kök = model (~%15 bozuk JSON), katmanlı retry maskeliyor. Sistem üretim-güvenilir → **M-9.1 açılabilir**. (C) grammar iyileştirmesi backlog'da (veriyle karar).

## (C) grammar — maxItems probu ve KALICI KAPANIŞ (2026-07-25)

Backlog "açık kapı"sı (sınırsız `citations[]` → `maxItems` ile latency açılır mı) canlı test edildi.
`scripts/c_grammar_poc_maxitems.py` (üretim şemasına dokunmayan deepcopy + maxItems enjeksiyonu).

| koşum | şema | VALID | latency | not |
|---|---|---|---|---|
| maxItems probu | citations maxItems=12 | 0/20 | hepsi 180s timeout | — |
| **kontrol** (aynı seans) | maxItems YOK (orijinal PoC) | 0/20 | hepsi 180s timeout | 20 Tem'de 5/20 @ min 57.8s idi |

**Atıf (kontrol koşusu ile):** maxItems'ı suçlayamayız — kontrol de bugün 0/20 timeout. Kök
**ortam** (H200/Ollama bugünkü durumu grammar-constrained decode'u tümüyle 180s'e itiyor),
şema sınırı değil. Not: grammar decode kısıtlı-örnekleme yolu; normal tool-call+retry üretimi
(~27s) bu patikayı kullanmaz, dolayısıyla bu yavaşlık normal işleyişi göstermez.

**KALICI KARAR: (C) grammar-constrained kök-çözüm KAPANDI.** Gerekçe: en iyi gözlem bile
(20 Tem, 5/20 @ 57s) kullanılamazdı; tek hızlandırma umudu (maxItems) latency'yi açmadı ve
bugünkü ortamda grammar hiçbir rejimde <180s üretmiyor. Correctness çalışıyor ama latency
hiçbir koşulda kabul edilebilir değil. **Retry çözümü (20/20, ~27s) kabul edilen kök-fix olarak
kalır; gs-012 çözülü.** Yeniden açılması için ön koşul: farklı motor/sürüm (ör. vLLM guided_json)
veya temelden farklı bir constrained-decode implementasyonu — bugünkü Ollama/GBNF ile değil.

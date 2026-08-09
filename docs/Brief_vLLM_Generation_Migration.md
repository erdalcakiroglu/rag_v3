# Brief — vLLM Generation-Migration (MİLESTONE, tetikleyicili)

**Tarih:** 2026-08-04
**Durum:** MİLESTONE tanımlandı — **fizibilite FAVORABLE, sert blokör YOK**; serve/deploy/kod **YAPILMADI**. Go-trigger konuldu.
**Kapsam:** YALNIZ generation (LLM üretimi) Ollama → vLLM. **Embedder Ollama'da KALIR** (bge-m3 non-MoE, darboğaz değil, `bge-m3@ollama` model-damgası 1478 korpus vektörüne gömülü — `assert_corpus_model` invaryantı korunur).

---

## 1. Bağlam — bu iş neden gündemde
A kolu (kapasite/eşzamanlılık) v1.14'te **aksiyon-yok** ile kapandı: darboğaz tek-GPU veya config değil, **Ollama'nın MoE mimarisini paralelleştirmeyi reddetmesi** (`sched.go:452 architecture=qwen35moe`, `NUM_PARALLEL=4` verilse bile `Parallel:1`). Ne NUM_PARALLEL ne kilit-kaldırma LLM throughput'unu çarpar. **Gerçek lever = vLLM** (ADR-003, Prod=vLLM) — çünkü vLLM MoE için **continuous batching native** yapar; Ollama'nın hard-cap'lediği tam da bu. `[[kapasite-seri-lock]]`

## 2. Fizibilite kapıları — HEPSİ YEŞİL (salt-okur ön-veri, konteyner + web)
| Kapı | Durum | Kanıt |
|---|---|---|
| **vLLM arch desteği (pivotal)** | ✓ | vLLM `Qwen3_5MoeForCausalLM` (text-only) + `Qwen3_5MoeForConditionalGeneration` destekliyor; resmi recipe var. RAG yalnız metin → **text-only path** yeterli, vision yükü taşınmaz. |
| GPU | ✓ | H200 NVL, 143771 MiB, driver 570.133.20, compute_cap 9.0 (Hopper) → vLLM tam uyumlu |
| Disk | ✓ | /datafile 745G boş → FP8 ~36GB / AWQ ~20GB / FP16 ~72GB rahat sığar |
| Lisans | ✓ | Apache-2.0 → HF safetensors serbest indirilebilir |
| Yazılım soyutlaması | ✓ | LiteLLM config-flip: `provider`/`api_base`/`api_key`/`model` hepsi ENV (`RAGINTEL_LLM_*`); agent grafiği yalnız `LLMResponse` soyutlamasını tüketir |

**Model kimliği:** `qwen35moe`, **35B-A3B** (35B toplam / **3B aktif** MoE), Q4_K_M GGUF (Ollama'da), 256k context, vision+thinking yetenekli, Apache-2.0.

## 3. Config-flip DEĞİL — milestone. Kalan maliyetler (blokör değil, ama flip de değil)
1. **Greenfield vLLM standup** — host'ta vLLM kurulu değil / image yok. Container + config. Sınırlı, standart.
2. **Ağırlık lojistiği** — vLLM'de GGUF-MoE zayıf/deneysel → **HF safetensors** indir (FP8 veya AWQ 4-bit). Ollama'nın Q4_K_M'inden **farklı quant substratı**.
3. **KALİTE RE-BASELINE (asıl pivot maliyeti, §5)** — bugünkü tüm mühürlü karneler Q4_K_M/Ollama üstünde ölçüldü; farklı quant → yeniden doğrulanmalı.
4. **Küçük gateway re-wire** — thinking kontrolü Ollama'da `extra_body={"reasoning_effort":...}`, vLLM+Qwen3'te `chat_template_kwargs={"enable_thinking":false}` → küçük re-wire + re-ölç. Timing native-ns alanları zarifçe `latency_ms`'e düşer (monitoring kaybı, fonksiyonel değil). Token sayımı vLLM `usage` döndürdüğü için **hayatta kalır**.

## 4. İki bağımsız değer akışı (önemli — talebi tek başına eşzamanlılık taşımıyor)
- **(i) Eşzamanlılık:** MoE continuous-batching → Ollama'nın hard-cap'ini kaldırır (ADR-003: 3-9× throughput iddiası).
- **(ii) gs-012 (C) guided_json grammar:** tool-call bozuk-JSON kökünün KALICI kapanışı **vLLM guided_json'a bağlıydı** (`[[gs012-toolcall-json-bug]]`). Bu, eşzamanlılıktan bağımsız ikinci sürücü — pertürbasyonlu-retry heuristiğini gereksiz kılar.

## 5. §1c Kalite-özdeşlik karnesi (peşine düşülürse ZORUNLU geçiş kapısı)
Farklı quant substratı → üretim dağılımı değişir. Kalıcı deploy'dan ÖNCE şunlar yeniden yeşil olmalı:
- **D4 honesty** — canlı 15/15 (Ollama/Q4_K_M'de mühürlü). `[[m17-honesty-onveri-karar]]`
- **Rerank baseline yönleri** — table→ON, multi_hop/single_fact→OFF yönleri korunmalı. `[[rerank-ab-onveri]]`
- **M-16 mühürlü coverage/grounding path** — regresyon yok. `[[m91-agent-asiri-temkin]]`
- Golden retriever recall (v0 0.714 zemini) sarsılmamalı.

## 6. Dürüst talep-uyarısı (#11 ve A ile aynı disiplin)
Bu milestone'un çözeceği eşzamanlılık, **33-turlik genç canlı logdan kanıtlanamıyor**. Seri tavan (~4-5 istek/dk) şu an kimseyi incitiyor olabilir de olmayabilir de — ölçülemez. Milestone-ölçekli maliyet + kanıtlanmamış talep → `[[on-veri-kontrolu-kural]]` gereği kalıcı kod/infra kurma **erken**.

## 7. GO-TRIGGER (bunlardan biri gerçekleşince milestone'u aç)
- **(T1) Gerçek eşzamanlılık baskısı:** canlı log olgunlaşınca (≥~150-200 tur, #11 ile aynı eşik) eşzamanlı istek kuyruğu ölçülebilir gecikme üretiyorsa; VEYA
- **(T2) gs-012 grammar ihtiyacı akutlaşırsa:** tool-call parse-error/pertürbasyon-retry canlıda tekrar sık görülürse (guided_json'ı tek başına yeterli gerekçe yapar).

**Çerçeve netleştirmesi (2026-08-04 canlı sayım):** T1'in ham "≥150-200 tur" eşiği bir **PROXY**'dir; gerçek ön-koşul **gerçek kullanıcı adopsiyonu / üretim canlıya açılışı**. Canlı sayım bugün: **2 kullanıcı / 33 user-turu / 13 sohbet**, 2026-07-20→08-03, v1.13'ten beri **DONMUŞ** → bu dev/demo trafiği, organik talep değil; ham eşik bu bağlamda fiilen hiç tetiklenmeyebilir. Bu yüzden tetikleyici *"gerçek kullanıcılar onboard olunca / üretim açılınca prevalence+kuyruk probu'nu tekrar koş"* olarak okunur — sayı bunun proxy'sidir. Aynı çerçeve #11 tetikleyicisi için de geçerli.

## 8. Peşine düşülürse İLK adım (kalıcı deploy DEĞİL)
Tek **trial serve** (geçici): H200'de text-only Qwen3.5-MoE'yi FP8/AWQ ile ayağa kaldır →
1. **bind-probe** (`kapasite_bind_noktasi_probe.py` mantığı): gerçekten paralelleşiyor mu? (oran ~1.0 beklenir, Ollama'da ~1.94'tü)
2. **mini-karne**: birkaç golden'da Q4_K_M'e karşı kalite deltası (§5 kapılarının hızlı proxy'si)
Go/no-go'yu KANITLA kapatır. Sonuç yeşilse tam karne + kalıcı deploy ayrı adım.

---

**Aksiyon (2026-08-04):** YOK — brief + tetikleyici konuldu. Üretim kodu/config değişmedi, serve yapılmadı.
`[[kapasite-seri-lock]]` `[[gs012-toolcall-json-bug]]` `[[on-veri-kontrolu-kural]]` `[[iki-veritabani-dev-vs-h200]]`

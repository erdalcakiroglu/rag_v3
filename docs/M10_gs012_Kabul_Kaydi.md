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

## Backlog: (C) grammar-constrained kök çözüm (ön-verisi hazır)

Ollama `format`=JSON şeması ile `submit_answer` → temp=0'da bozuk JSON İMKANSIZ. Ollama tool-call argümanlarını `format`'la kısıtlamıyor → submit_answer'ı tool-call yolundan `format`-CONTENT üretimine taşımak gerek (agent rework, ayrı dilim). **PoC ön-veri betiği: `scripts/c_grammar_poc.{py,sh}`** — doğrudan `/api/chat` + format=submit şeması + temp=0 ×20, TR özel-ad quote'lu bağlamda. KAPI: `VALID=20/20` → grammar çözer, (C) greenlight; `VALID<20` → grammar yetmiyor, kod yazmadan pivot. **(C) değeri = kökü (bozuk JSON) silmek + deterministik garanti + p95 kuyruğunu kesmek** — base latency değil; retry maskesi güvenilirliği taşıdığı için **acil değil**.

**Karar:** gs-012 (bozuk tool-call JSON → fallback) canlıda **KABUL EDİLDİ** — correctness çözüldü (20/20, fallback=0, M-9 mührü korunuyor). Kök = model (~%15 bozuk JSON), katmanlı retry maskeliyor. Sistem üretim-güvenilir → **M-9.1 açılabilir**. (C) grammar iyileştirmesi backlog'da (veriyle karar).

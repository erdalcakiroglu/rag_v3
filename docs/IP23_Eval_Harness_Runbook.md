# İP-2.3 — RAGAS-tarzı Eval Harness Runbook (DEV-MODE)

**Amaç:** Uçtan uca RAG kalitesini (agent yanıtları) golden set üzerinde ölçen harness.
**Durum:** DEV-MODE göstergesi — **RESMİ KARNE DEĞİL** (aşağıdaki §Veri egemenliği).

## CLI

```
python -m ragintel.eval run --golden v0 --mode report [--limit N] [--runs 3] \
    [--agent-model qwen/qwen3-32b] [--judge-model llama-3.3-70b-versatile] \
    [--question-delay 1.0] [--json]
```

- `--limit N` → dry-run (ilk N answerable + 2 unanswerable). Onaysız tam koşu YAPMA.
- `--runs 3` → judge nondeterminizmi için 3 koşu **medyanı** (temperature=0).
- `--question-delay` → sorular arası throttle (Groq TPM yumuşatma).

## Ne ölçer

- **31 answerable** soru gerçek agent graph'ından geçer → `{question, answer, contexts, ground_truth}`
  → 4 RAGAS-tarzı metrik: **faithfulness, answer_relevancy, context_precision, context_recall**.
- **5 unanswerable** RAGAS'a GİRMEZ → deterministik **dürüstlük kontrolü**: sistem
  "bulunamadı"/fallback döndü mü (declined) ve kaynak uydurmadı mı (fabricated)? Hedef 5/5.
- **İterasyon dağılımı** (soru başına tur) → `max_iterations` kararının verisi.
- **Dev-hedef kıyas** (gösterge): faithfulness ≥0.85, context_precision ≥0.80 (MVP).

## ⚠ Veri egemenliği — DEV-MODE sapması (bilinçli, etiketli)

Spec İP-2.3 judge'ı **lokal Ollama** (qwen3.5:35b, dış API yok) şart koşar. H200
erişilemezken geliştirmeyi sürdürmek için judge **Groq `llama-3.3-70b`** (bulut) ile
koşulur. TÜM sonuçlar `judge=groq/dev-mode` etiketlidir (Langfuse span'lerinde de).
Bu sayılar **geliştirme göstergesidir**, resmi karne değildir. Ayrıca metrikler `ragas`
paketiyle değil, RAGAS metodolojisiyle **self-contained** hesaplanır (proje ağır-dep
politikası; kontrol + düşük paralellik + dev-mode etiket).

### PROD ÇAPRAZ-DOĞRULAMA ŞARTI (zorunlu — resmi ölçümden önce)

Resmi karne üretmeden önce **iki eksen de** değiştirilip dev-mode Groq sayıları çapraz
doğrulanmalıdır:

1. **Gerçek RAGAS paketi** ile koş (langchain + `ragas.evaluate`), aynı dataset üzerinde.
2. **Lokal judge** (H200 Ollama, qwen3.5:35b — dış API YOK) ile koş; ağ izolasyonuyla
   dış çağrı olmadığı kanıtlanır.
3. **Kabul:** dev-mode (Groq/self-contained) ile prod (RAGAS/lokal-judge) metrikleri
   metrik başına makul tolerans içinde (öneri: |Δ| ≤ 0.10 mutlak, sıralama tutarlı)
   örtüşmeli. Örtüşmezse dev-mode göstergeleri güvenilmez sayılır → self-contained
   metodoloji veya judge kalibre edilir. Sonuç `docs/`'a çapraz-doğrulama raporu olarak yazılır.

Bu adım tamamlanmadan hiçbir eval sayısı "resmi" olarak sunulmaz.

## Model seçimi (dev-mode, Groq)

- **Agent = `qwen/qwen3-32b`**: bizim tool şemalarımızda (integer `top_k`, iç içe
  citations'lı `submit_answer`) GÜVENİLİR tool-calling (3/3 temiz). Mühürlü H200 modeliyle
  (qwen3.5:35b) aynı aile. llama-3.3-70b tool-calling'de düşüyor ("Failed to call a function").
- **Judge = `llama-3.3-70b-versatile`**: tool kullanmaz, temiz JSON üretir; metrik smoke'ta
  bilinen-doğru → 1.0, kaçamak → 0.0.

## Rate-limit / maliyet (Groq free-tier)

- Free-tier TPM DÜŞÜK: `qwen/qwen3-32b`=6000, `llama-3.3-70b`≈12000 token/dk + günlük kap.
- Çok-turlu ajan (~5.5k token/tur × 3-4 tur) free-tier'da tam 36×3'ü zorlar.
- Gateway + judge: **Retry-After'a uyan üstel backoff** (`RAGINTEL_LLM_MAX_RETRIES`, vars. 5)
  + `--question-delay` throttle. Tek istek TPM'i aşıyorsa (qwen3-32b'de olası) backoff çözmez.
- **Tam 36×3 için öneri:** Groq **Dev tier** (limitler kalkar, dakikalar) VEYA gece koşusu
  (backoff + throttle ile günlük pencereye yayılmış).

## Dry-run bulgusu (2026-07-08)

Harness + 4 metrik + dürüstlük + iterasyon doğrulandı (geçen sorularda faith 1.0 /
relev 0.91 / prec 0.97 / recall 1.0; dürüstlük 1/1). Tam koşu Groq free-tier rate-limit'e
takıldı → model/tier kararı ve backoff eklendi (bu runbook).

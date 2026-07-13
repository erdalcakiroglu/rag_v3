# FAZ 5 — FİNAL KARNE ve Ortam Matrisi (KAPANIŞ)

> ## ⚠ ARŞİV — BU KARNE ARTIK GEÇERLİ ZEMİN DEĞİLDİR (M-7, 2026-07-13)
>
> Bu karnenin ölçüm zemini **yeniden üretilemez**; aşağıdaki sayılar bugünkü sistemle
> **KIYASLANAMAZ**. Tarihsel kayıt olarak durur, gate'in referansı DEĞİLDİR.
>
> Zeminin üç bileşeni de değişti:
> 1. **Agent modeli:** karne `qwen/qwen3-32b` (Groq) ile alındı. LLM ucu artık DeepSeek
>    (`api.deepseek.com`) ve yalnızca `deepseek-v4-pro`/`-flash` servis ediyor — eski agent
>    bu ortamda **çalıştırılamıyor** (gate denendi: 36/36 `BadRequestError`).
> 2. **Korpus:** M-7 REPROCESS'i pinli `docling==2.108.0` ile koştu. Karne dönemindeki
>    chunk'lar pin ÖNCESİ bir docling'le üretilmişti (1266 → 1478 chunk).
> 3. **Retrieval:** `hnsw.iterative_scan` (filtreli-ANN aday tükenmesi düzeltmesi) eklendi.
>
> **Güncel zemin:** `docs/M7_Karne_v1.md` (agent=deepseek-v4-pro · judge=llama-3.3-70b-versatile
> · golden=v0.1 · iterative_scan=relaxed_order). `eval_gates` eşikleri O karneye kalibrelidir.
>
> **Ders (kalıcı koruma):** bu sapma aylarca sessiz kaldı çünkü gate koştuğu modeli hiçbir
> yere yazmıyordu ve `.env` DB'yi ezebiliyordu. İkisi de düzeltildi — bkz.
> `gates.model_ground_precondition` (zemin sapması → exit 2, judge çağrılmadan).

**Tarih:** 2026-07-09 · **judge=deepseek/dev-mode** (DEV-MODE; resmi karar prod lokal-judge
çapraz doğrulaması sonrası — runbook şartı). Bitiş kuralı uyarınca FAZ 5 bu koşuyla KAPANIR.

## 1. Üç-kolonlu karne (golden v0, 31 answerable + 5 unanswerable, runs=3 medyan)

| metrik | v0-baseline | dilim-1 | dilim-3 |
|---|---|---|---|
| | v1 prompt · iter 4 · fallback+src · ent OFF | v2 prompt · iter 3 · fallback-ayrımı · ent OFF | v3 prompt · iter 3 · **ent ON** (v2.2/v4-pro) |
| faithfulness | 0.791 | **0.753** | 0.653 |
| answer_relevancy | 0.752 | 0.692 | 0.648 |
| context_precision | 0.740 | **0.775** | 0.708 |
| context_recall | 0.790 | **0.839** | 0.758 |
| **dürüstlük** | 1/5 | **4/5** | 3/5 |
| over-decline (answerable) | — | **4** | 6 |
| iterasyon ort | 2.33 | 2.56 | 2.56 |
| latency (s) | 3550 | 4199 | 4561 |

## 2. Okuma

- **v0 → dilim-1: net kazanım.** Dürüstlük 1/5 → 4/5 (fallback kaynak ayrımı: reddederken
  `sources=[]`), precision/recall ↑. Bedel: faithfulness hafif ↓ (v2 prompt'un birkaç zor soruyu
  over-decline'ı). max_iter 4→3 kuyruk kısalttı.
- **dilim-3: hipotez TUTMADI.** Beklenti "v3 (kısmi-cevap) over-decline'ı düşürür + entailment
  gs-v0-034'ü tutar" idi. Gerçekleşen: dürüstlük **4/5 → 3/5** (kötü), faithfulness **0.653**
  (kötü), over-decline **4 → 6** (arttı), +latency. gs-v0-034 bu koşuda yine ✗ (border, conf=high).
- **Kök neden: NONDETERMINIZM.** validate-v2 entailment izole/kontrollü testlerde gs-v0-034'ü
  güvenilir yakalıyor (bkz. FAZ5_validate_v2_Delta §5: v2.2+v4-pro → 6/8 FP giderildi, 034 tutuldu),
  AMA tam pipeline'da tek-atış entailment + nondeterministik agent (temperature≠0) birleşince
  tutarsız: over-decline soru seti bile koşudan koşuya değişiyor. Dev bulut-judge tek-atışta
  guardrail-gating için yeterince kararlı değil.

## 3. KAZANAN KONFİGÜRASYON — Ortam Matrisi (MÜHÜRLÜ)

### dev / demo — **dilim-1 (SEALED)**
`app_config` (DB, updated_by=codex):
- `prompts.agent_system_active = v2` (grounding + hipotetik-kesin-sunma yasağı + reddetme)
- `agent.validate_entailment = false` (opt-in; entailment kodu + v2.2 prompt hazır)
- `agent.max_iterations = 3`
- fallback kaynak ayrımı (kod): reddedilen yanıtta `sources=[]`, incelenenler `meta.reviewed_sources`
- **Gerekçe:** en iyi dürüstlük/faithfulness dengesi (4/5, 0.753), entailment latency/varyansı yok.

### prod — **öneri (runbook'a devir)**
- Temel: dilim-1 ile aynı (v2 prompt / iter 3 / fallback-ayrımı).
- **validate-v2 entailment = opt-in, LOKAL judge ile ZORUNLU** (veri egemenliği). Açmadan önce:
  (a) lokal judge ile prod çapraz-doğrulama (dev-mode DeepSeek sayılarıyla |Δ|≤0.10),
  (b) nondeterminizmi azalt: agent temperature=0 (üretimde deterministik yanıt) + gerekirse
  entailment 2-of-3 oy, (c) yüksek-risk akışlarda (finansal/uyum) aç, genel akışta kapalı.
- Prompt v2.2 (asimetrik entailment) + judge=lokal-güçlü model.

## 4. Prod runbook'a devredilen açık işler (sonsuz tuning YOK — FAZ 5 kapalı)

1. **Entailment nondeterminizmi:** agent temperature=0 + entailment oy-çokluğu; tam-pipeline
   tutarlılığını lokal judge ile yeniden ölç.
2. **gs-v0-034 sınıfı:** izole testte çözüldü (v2.2); prod lokal-judge ile tam-pipeline'da doğrula.
3. **Residual over-decline / faithfulness:** daha büyük golden set + lokal judge ile RAGAS
   (gerçek `ragas` paketi) çapraz doğrulama.
4. **2 residual FP (gs-v0-026/029):** prod judge ile gerçek-FP mi ayrımı.
5. Tüm dev-mode sayıları **resmi karne DEĞİL** — prod lokal-judge ölçümü esas (IP23 runbook).

## 5. Kapanış

FAZ 5 kapandı. dev/demo config mühürlü (dilim-1). validate-v2 altyapısı (entailment guardrail,
prompt versiyonlama, fallback kaynak ayrımı) tamam ve opt-in; prod'da lokal-judge ile devreye
alınacak. Kalan iyileştirmeler prod runbook'unda.

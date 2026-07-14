# M-7 — KARNE v1 (MÜHÜRLÜ)

**Tarih:** 2026-07-13 · **Golden:** `v0.1` (43/43 evidence çözülüyor) · **status:** complete, **0 hata**

Bu, FAZ 5 karnesinin (arşiv) yerine geçen **güncel ölçüm zeminidir**. `eval_gates` eşikleri
BU karneye kalibrelidir.

## 1. ÖLÇÜM ZEMİNİ (mühürün ayrılmaz parçası)

| bileşen | değer | neden burada |
|---|---|---|
| agent | `deepseek-v4-pro` | LLM ucu DeepSeek (`api.deepseek.com`); eski karnenin agent'ı (`qwen/qwen3-32b`, Groq) bu ortamda ÇALIŞTIRILAMIYOR |
| judge | `llama-3.3-70b-versatile` | dev-mode (resmi karar prod lokal-judge çapraz doğrulaması sonrası — runbook şartı) |
| golden | `v0.1` | v0'un 2 alıntısı pinli docling ile yeniden çıpalandı (tek boşluk; anlamsal değişiklik yok) |
| korpus | 41 dosya · 1478 chunk · `docling==2.108.0` | M-7 REPROCESS (pinli docling'in bu korpusa İLK dokunuşu) |
| retrieval | `hnsw_iterative_scan = relaxed_order` | filtreli-ANN aday tükenmesi düzeltmesi; ANN semantiği retrieval'ı değiştirir → zeminin parçası |
| yöntem | `runs=3` (judge medyanı) | judge varyansı: aynı korpusta tek koşum `context_precision` 0.630, medyan 0.739 verdi |

> **Zemin metadata'sı neden mühürde?** Bu üçünden biri değişirse sayılar KIYASLANAMAZ olur.
> Tam da bu yaşandı: `.env`'de kalmış bir model override'ı karnenin agent'ını sessizce ezdi
> ve kıyaslanamaz sayılar "regresyon" sanıldı. Artık gate, koştuğu zemini HER koşumda
> raporlar ve karneden saparsa **exit 2** verir (`gates.model_ground_precondition`).

## 2. KARNE

**Veri kümesi:** 31 answerable (hepsi bağlam aldı) + 5 unanswerable · 0 hata

| metrik | değer | gate eşiği (~%5 alt) |
|---|---|---|
| faithfulness | **0.7612** | 0.72 |
| answer_relevancy | 0.7253 | — (gate'te yok) |
| context_precision | **0.7393** | 0.70 |
| context_recall | 0.8387 | — (gate'te yok) |
| honesty | **4/5 = 0.800** | 0.76 |

**Eşik kalibrasyonu:** karne × 0.95, 2 haneye yuvarlı. Amaç REGRESYON yakalamak, mükemmellik
dayatmak değil. `honesty` AYRIK bir metriktir (5 soru): 0.76 eşiği hâlâ 4/5 şart koşar
(3/5 = 0.60 düşer), ama float sınırında takılmaz.
DB: `app_config('eval_gates')`, `updated_by=m7-karne-v1` (config_audit'te izli).

## 3. ARŞİV KARNESİYLE KIYAS — YAPILAMAZ

FAZ5 dilim-1: faithfulness 0.753 · context_precision 0.775 · honesty 4/5.
Sayılar YAKIN görünüyor, ama **kıyas geçersizdir**: agent, korpus ve retrieval semantiği
değişti. Yakınlık teselli edicidir, kanıt değildir. Bu yüzden eski karne
(`docs/FAZ5_Final_Karne.md`) ARŞİV notuyla işaretlendi ve gate'in referansı DEĞİLDİR.

## 4. YENİDEN ÜRETİM

```bash
python -m ragintel.eval run  --golden v0.1 --runs 3 --json     # karne (bu tablo)
python -m ragintel.eval gate --golden v0.1 --json              # CI gate (eşiklerle kıyas)
```
Zemin DB'den gelir (`eval.agent_model` / `eval.judge_model` — config-first, `.env` EZEMEZ).
Gate, evidence çözünürlüğünü ve model zeminini judge'a gitmeden ÖNCE doğrular; ikisinden
biri kayarsa **exit 2 (altyapı)** verir — token yakmadan, "regresyon" yalanı söylemeden.

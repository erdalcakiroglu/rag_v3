# M-9 — KARNE v1 (MÜHÜRLÜ) — LOKAL ZEMİN

**Tarih:** 2026-07-15 · **Golden:** `v0.1` · **status:** complete · **1 altyapı hatası** (gs-012, aşağıda)

Bu, M-7 karnesinin (DeepSeek agent) yerine geçen **lokal ölçüm zeminidir**. H200 geçişiyle
agent ve judge artık KENDİ donanımımızda koşuyor (veri egemenliği). `eval_gates` eşikleri
BU karneye kalibrelidir.

> **En kritik zemin farkı — `temperature=0.0`.** Bu karne, sıcaklığın sabitlendiği İLK
> karnedir. Önceki koşumlar sıcaklığı hiç set etmiyordu → uç Ollama varsayılanına (0.8)
> düşüyordu; aynı soru koşudan koşuya %0–%60 fallback veriyordu ve HİÇBİR karne
> mühürlenemiyordu. `temperature=0.0` ile ölçüm deterministik/tekrarlanabilir oldu —
> mühürün ön şartı buydu. Ayrıntı: [[m9-sicaklik-kok-neden]].

## 1. ÖLÇÜM ZEMİNİ (mühürün ayrılmaz parçası)

| bileşen | değer | neden burada |
|---|---|---|
| agent | `qwen3.5:35b` | lokal (H200 `/ollama/v1`); DeepSeek istisnası kapatıldı (veri egemenliği) |
| judge | `llama3.3:latest` (`local/dev-mode`) | lokal hakem; kendi donanımımızda — belge içeriği dışarı çıkmıyor |
| **temperature** | **`0.0`** | **fallback varyansının KÖK KAYNAĞIYDI; determinizm mühürün ön şartı** |
| golden | `v0.1` | M-7 ile aynı çıpa (değişmedi) |
| korpus | `docling==2.108.0`, M-7 REPROCESS zemini | değişmedi |
| retrieval | `hnsw_iterative_scan = relaxed_order` | değişmedi; ANN semantiği zeminin parçası |
| yöntem | `runs=3` (judge medyanı) · `agent_runs=1` | agent tekrarını **determinizm meşrulaştırır**: spot-check 5/5 soruda iki koşum AYNI sonucu verdi (temp=0). Ayrıca izole ölçümde de doğrulandı |

> **Zemin metadata'sı neden mühürde?** Bu değerlerden biri değişirse sayılar KIYASLANAMAZ
> olur. Gate her koşumda zemini raporlar ve karneden saparsa **exit 2** verir
> (`gates.model_ground_precondition`) — M-9'da bu kontrol **temperature'ı da** kapsar.

## 2. KARNE

**Veri kümesi:** 31 answerable + 5 unanswerable. **30 answerable skorlandı**; `gs-v0-012`
cevap üretemedi (aşağıda — altyapı, agent kalitesi değil).

> **Metrikler İKİ KIRILIMLA verilir (M-9 kuralı).** Fallback ("Cevap bulunamadı") TANIMI
> GEREĞİ sadıktır (hiçbir iddia öne sürmez) → faithfulness/context_precision'ı yapısal
> ŞİŞİRİR. Tek dürüst kalite özeti **answered-only**'dir; fallback oranı AYRI satırdır.

### 2a. FALLBACK ORANI — `5/30 = %17`

`gs-v0-002` · `gs-v0-022` · `gs-v0-023` · `gs-v0-028` · `gs-v0-029`

Bunlar `temperature=0.0` altında **deterministik** fallback'lerdir (şansa bağlı değil) →
mühür-sonrası **hedefli iş listesi** (ortalama değil, tek tek çözülür). Önceki karnelerde
fallback %32–39 idi; retry-bütçesi düzeltmesi + kör-geri-bildirim düzeltmesi + temp=0 ile %17.

### 2b. ANSWERED-ONLY (tek dürüst kalite özeti)

| metrik | değer | gate eşiği (~%5 alt) |
|---|---|---|
| faithfulness | **0.915** | 0.87 |
| answer_relevancy | 0.750 | — (gate'te yok) |
| context_precision | **0.895** | 0.85 |
| context_recall | 1.000 | — (gate'te yok) |
| honesty | **5/5 = 1.000** | 0.76 (4/5 şart) |
| fallback oranı | **0.167** | `fallback_rate_max = 0.25` (HARD) |

**Kategori kırılımı (answered-only):**

| kategori | n | faith | rel | prec | rec |
|---|---|---|---|---|---|
| single_fact | 9 | 1.000 | 0.748 | 0.982 | 1.000 |
| citation_sensitive | 3 | 0.889 | 0.728 | 0.673 | 1.000 |
| synthesis | 7 | 0.745 | 0.732 | 0.974 | 1.000 |
| table_based | 3 | 1.000 | 0.852 | 0.569 | 1.000 |
| multi_hop | 3 | 1.000 | 0.716 | 1.000 | 1.000 |

## 3. `gs-v0-012` — ALTYAPI HATASI (agent kalitesi DEĞİL)

```
500 InternalServerError — failed to parse JSON: invalid character 'H' after object key:value pair
```

Model tarafında bozuk tool-call JSON'u (Open WebUI/Ollama parse hatası). Cevap üretemediği
için skordan düştü. **Karne 30 answerable zemininde mühürlendi.** `temperature=0.0` olduğu
için bu deterministik olabilir → mühür-sonrası iş listesine: *"gs-012: bozuk tool-call JSON,
tekrarlanabilir mi kontrol et; tekrarlanıyorsa gerçek arıza, gs-022 sınıfı."*

## 4. M-7 KARNESİYLE KIYAS — YAPILAMAZ

M-7 (DeepSeek): faithfulness 0.761 · answer_relevancy 0.725 · context_precision 0.739.
Answered-only relevancy 0.750 YAKIN görünüyor ama **kıyas geçersizdir**: agent (DeepSeek→
qwen3.5:35b), sıcaklık (0.8→0.0) ve donanım değişti. Yakınlık teselli edicidir, kanıt
değildir. M-7 karnesi önceki zemin; gate'in referansı ARTIK BUDUR.

## 5. YENİDEN ÜRETİM

```bash
# Mühür karnesi (bu tablo). Determinizm sayesinde agent_runs=1 yeterli; kalite iddiası
# taşıyan A/B'ler için agent_runs=3 + fallback tekrar-dağılımı kullanılır.
python -m ragintel.eval run  --golden v0.1 --runs 3 --agent-runs 1 \
    --agent-model qwen3.5:35b --judge-model llama3.3:latest --json

python -m ragintel.eval gate --golden v0.1 --json   # CI gate (eşiklerle kıyas)
```

Zemin DB'den gelir (`eval.agent_model` / `eval.judge_model` / `eval.agent_temperature` —
config-first, `.env` EZEMEZ). Gate, evidence çözünürlüğünü + model zeminini (temperature
dahil) judge'a gitmeden ÖNCE doğrular; sapma → **exit 2** (altyapı).

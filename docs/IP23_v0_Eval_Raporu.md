# İP-2.3 — v0 Uçtan Uca Eval Raporu (DEV-MODE)

**Tarih:** 2026-07-09 · **judge=deepseek/dev-mode** — **RESMİ KARNE DEĞİL**, geliştirme
göstergesi (veri egemenliği: prod'da lokal judge + gerçek RAGAS ile çapraz doğrulama şartı;
bkz. [IP23_Eval_Harness_Runbook.md](IP23_Eval_Harness_Runbook.md)).

**Kurulum:** golden=v0 (31 answerable + 5 unanswerable) · agent=`deepseek-v4-pro` ·
judge=`deepseek-v4-flash` · runs=3 (medyan) · süre 3550s · **0 hata**.
**Metodoloji:** RAGAS-tarzı self-contained (ragas paketi değil); metrik tanımları RAGAS ile hizalı.

---

## 1. RAGAS-tarzı metrikler (kategori kırılımı)

| kategori | n | faithfulness | answer_relevancy | context_precision | context_recall |
|---|---|---|---|---|---|
| **GENEL** | 31 | **0.791** | **0.752** | **0.740** | **0.790** |
| single_fact | 10 | 0.842 | 0.835 | 0.907 | 1.000 |
| citation_sensitive | 4 | 0.500 | 0.651 | 0.631 | 0.875 |
| synthesis | 7 | 0.800 | 0.718 | 0.782 | 0.786 |
| table_based | 5 | 1.000 | 0.877 | 0.467 | 0.600 |
| multi_hop | 5 | 0.700 | 0.590 | 0.711 | 0.500 |

**Dev-hedef kıyas** (gösterge; geçmese de sayı geçerli):
- faithfulness **0.791** (hedef ≥0.85) → **altında**
- context_precision **0.740** (hedef ≥0.80) → **altında**

Gözlemler (dev-mode, küçük n; İP-2.4 retrieval baseline ile tutarlı):
- **single_fact güçlü** (recall 1.0, prec 0.91) — temel olgu soruları sağlam.
- **table_based**: faithfulness 1.0 ama **context_precision 0.47 / recall 0.60** — tablo
  retrieval'ı zayıf (baseline'da da tablo zayıftı); getirilen bağlamda çok distractor.
- **multi_hop**: recall **0.50** en düşük (baseline recall@5=0.30 ile uyumlu) — iki dosyanın
  kanıtı da gelmiyor; FAZ G (agentic multi-hop) tetiği.
- **citation_sensitive** faithfulness **0.50** (n=4, küçük) — yazar/atıf sorularında judge
  ifadeleri desteklenmemiş buldu; incelenecek.

## 2. Unanswerable dürüstlük (RAGAS dışı, deterministik) — **1/5**

Tanım KATI ve DOĞRU: cevapsız soruda **herhangi bir citation = fail** (gevşetilmedi).

| id | kind | conf | kaynak | declined | uydurma | sonuç |
|---|---|---|---|---|---|---|
| gs-v0-032 | border_declined_cited | low | 3 | ✓ | ✓ | ✗ (sınır) |
| gs-v0-033 | border_declined_cited | low | 2 | ✓ | ✓ | ✗ (sınır) |
| gs-v0-034 | **fabricated_confident** | **high** | 2 | ✗ | ✓ | ✗ (**ciddi**) |
| gs-v0-035 | honest | low | 0 | ✓ | ✓yok | ✓ |
| gs-v0-036 | border_declined_cited | medium | 3 | ✓ | ✓ | ✗ (sınır) |

İki farklı arıza modu → §3 teşhis.

## 3. Dürüstlük teşhisi (kod yazmadan; trace + korpus analizi)

### 3a. border_declined_cited (gs-v0-032/033/036) — "dürüst reddetti AMA kaynak iliştirdi"
gs-v0-032 yanıtı **fallback**: *"Güvenilir yanıt üretilemedi. Bulunan kaynaklar aşağıdadır."*
→ sistem **doğru reddetti**. Ama fallback/compose, incelenen chunk'ları `sources`'a iliştirdi
(cite'lı quote'lar korpusta **GERÇEK** — ör. "İlk kez 2022-2024 dönemi Orta Vadeli Programda
karbon vergisi konusuna yer verilmiştir", chunk 48001). Bu chunk'lar Türkiye-karbon-vergisi
içeriği ama **soruyu (2030+ oran takvimi) cevaplamıyor**.
→ **Kök neden: hallüsinasyon DEĞİL; fallback/compose'un reddederken bile kaynak göstermesi.**

### 3b. fabricated_confident (gs-v0-034) — **asıl sorun, v1'in kör noktası**
Soru: *"Karbon vergisinden elde edilen gelirin Türkiye'nin yıllık bütçesindeki payı yüzde kaçtır?"*
(golden: cevapsız — Türkiye'de uygulanan karbon vergisi yok).
Yanıt (**conf=high**): *"...payı %0,54 ile %3,62 arasında değişmektedir [1]."*
Cite: chunk 47233 (_006.pdf s12), quote *"the proportion to the total governmental budget lay
between 3.62% and 0.54%"*.

**Kanıt (korpus):** quote **BİREBİR gerçek**. AMA chunk 47233'ün bağlamı:
> "The Weight of a Carbon Tax ... **Under Different Tax Rates (2014 estimations)** ... A tax with
> a rate of 3 US dollars **would translate to** ... The proportion ... to the total governmental
> budget **lay between 3.62% and 0.54%**. These figures can be compared with other countries..."

→ Bu sayılar **HİPOTETİK bir 2014 tahmini** ("farklı vergi oranları altında", "would translate")
— Türkiye'nin **gerçek** bütçe payı DEĞİL (çünkü uygulanan vergi yok). Model, **hipotetik bir
projeksiyonu kesin bir olgu gibi** sunup yüksek güvenle cevapladı. (Yanıtta "2014 verileriyle
hesaplanmıştır" ibaresi var ama çerçeve kesin + conf=high → yanıltıcı.)

**v1 doğrulamasının kör noktası (doğrulandı):** v1 iki şey kontrol eder — (1) quote bağlamda
birebir var mı (✓ var), (2) coverage eşiği (✓ yüksek). **İddianın quote tarafından ANLAMSAL
olarak desteklenip desteklenmediğini (entailment) kontrol ETMEZ.** Gerçek-ama-hipotetik bir
quote, desteklemediği bir iddiaya dayanak yapılınca v1 geçiriyor → conf=high.

## 4. İterasyon dağılımı (max_iterations kararı verisi)

ort **2.33** · max **5** · dağılım: **2 tur → 27 soru, 3 → 7, 4 → 1, 5 → 1**.
Kategori ort: single_fact 2.1 · citation 2.25 · synthesis 2.14 · table 2.2 · multi_hop 2.4 ·
**unanswerable 3.2**.

→ **34/36 soru ≤3 turda bitiyor.** Turu asıl tüketen unanswerable'lar (olmayan bilgiyi arayıp
geç pes ediyor) ve birkaç zor soru. `max_iterations`'ı **4→3** düşürmek neredeyse hiçbir
answerable'ı kesmez, unanswerable'da erken-fallback'i hızlandırır (fabrication fırsatını azaltır).
Karar İP-2.4-sonrası eval bu; veri: 3 tur kâfi görünüyor.

## 5. Öneriler (iki paralel yol)

### (a) Ucuz / hemen — FAZ 5 prompt + fallback sertleştirmesi
1. **Fallback/compose: reddederken KAYNAK GÖSTERME.** `sources=[]` (low-confidence/fallback
   yolunda). Bu tek başına 3 border vakasını (032/033/036) dürüst'e çevirir.
2. **Prompt (FAZ 5):** "Yalnızca bağlamda AÇIKÇA yazan bilgiyi kullan; **hipotetik/tahmini**
   ifadeleri kesin cevap gibi sunma; bağlam soruyu açıkça cevaplamıyorsa 'bulunamadı' de ve
   **kaynak gösterme**." → gs-v0-034 tipini azaltır (garanti etmez).

### (b) Sağlam — validate v2 (LLM-as-judge entailment) — **7c tetiği ateşlendi**
v1'in kör noktası (gerçek-quote ↔ desteklenmeyen-iddia) yalnızca **entailment kontrolüyle**
kapanır: her citation için "quote bu iddiayı ANLAMSAL olarak destekliyor mu?" LLM-judge sorusu.
gs-v0-034'ü yakalar. **Tasarım kararı kullanıcıya:** maliyet (her cevapta ek judge çağrısı),
lokal-judge zorunluluğu (veri egemenliği), v1 ile birlikte mi/yerine mi.

---

**Not:** Tüm sayılar dev-mode DeepSeek judge; prod resmi ölçüm lokal judge + gerçek RAGAS ile
çapraz doğrulanana dek gösterge niteliğindedir (runbook §prod çapraz-doğrulama).

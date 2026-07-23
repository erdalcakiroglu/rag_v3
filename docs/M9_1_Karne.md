# M-9.1 — KARNE (STABİL ZEMİN, gs-012 sonrası) — KIRIK-SORU TURU

**Tarih:** 2026-07-23 · **Golden:** `v0.1` · **status:** complete · **gs-012 altyapı hatası KAPANDI**

M-9 karnesinin (2026-07-15) tekrarı; tek fark: **gs-012 artık cevap üretebiliyor** (katmanlı
retry, [[gs012-toolcall-json-bug]]) → 30 → **31 answerable** skorlandı. Zemin M-9 ile **birebir
aynı** (agent qwen3.5:35b, judge llama3.3:latest `local/dev-mode`, temperature=0.0, golden v0.1,
runs=3/agent-runs=1) → M-9 ↔ M-9.1 kıyaslanabilir.

## 1. KAPI (gate) — GEÇTİ

| metrik | M-9 | **M-9.1** | hedef | pass |
|---|---|---|---|---|
| faithfulness | 0.915 | **0.9439** | 0.85 | ✓ |
| context_precision | 0.895 | **0.8774** | 0.80 | ✓ |
| fallback oranı | 5/30 = %16.7 | **6/31 = %19.35** | %25 (HARD) | ✓ |

## 2. ANSWERED-ONLY (n=25 — tek dürüst kalite özeti)

| metrik | M-9 | **M-9.1** |
|---|---|---|
| faithfulness | 0.915 | **0.9473** |
| answer_relevancy | 0.750 | **0.7629** |
| context_precision | 0.895 | **0.855** |
| context_recall | 1.000 | **1.000** |

**Kategori (answered-only):** single_fact (n9) faith 1.0/rel 0.76/prec 0.98 · citation_sensitive
(n3) 0.89/0.73/0.67 · synthesis (n6) 0.84/0.71/0.97 · table_based (n5) 1.0/0.86/**0.54** ·
multi_hop (n2) 1.0/0.73/1.0. (table_based context_precision düşük — retrieval gürültüsü, ayrı iş.)

## 3. FALLBACK TEŞHİSİ — kök: AGENT AŞIRI-TEMKİNLİ RED (retrieval DEĞİL)

6 answerable fallback verdi (`answer_relevancy=0.0` = judge "noncommittal" damgası; hepsi gerçek
"Cevap bulunamadı", judge artefaktı DEĞİL): **gs-002, gs-012, gs-019, gs-022, gs-023, gs-026**.

Hedefli teşhis (`scripts/m91_fallback_teshis.py`, judge'suz 2. koşum) her birinin **ham cevabı +
getirilen bağlamı**nı bastı. **Bulgu: 6'sının HEPSİNDE kanıt bağlama getirilmişti** — retrieval
ıskası değil. En net kanıt gs-002: ctx[1] = *"Türkiye'de… henüz bir karbon vergisi uygulaması
bulunmuyor"* (cevabın birebir kendisi) — agent yine de reddetti.

| id | kategori | karne | teşhis (2. koşum) | kanıt bağlamda | okuma |
|---|---|---|---|---|---|
| gs-002 | single_fact | fallback | **yine fallback** | ✅ ctx[1] birebir | agent red (kesin) |
| gs-012 | citation_sensitive | fallback | **yine fallback**; JSON bug ateşledi ('H'=Hakan) | ✅ ctx[1] yazarlar | agent red + JSON tuzağı |
| gs-019 | synthesis | fallback | **cevapladı** (conf=medium) | ✅ | non-determinizm |
| gs-022 | multi_hop | fallback | **yine fallback** | kısmi (Finlandiya✅, Çin payı zayıf) | multi-hop birleştirme kusuru |
| gs-023 | multi_hop | fallback | **yine fallback**, iters=6 (bütçe bitti) | kısmi (İsveç zayıf) | bütçe tükenişi + red |
| gs-026 | multi_hop | fallback | **cevapladı** (sources=2) | ✅ | non-determinizm |

**İki kesin çıkarım:**

1. **Dominant kök = agent aşırı-temkinli red.** 6'sında da kanıt eldeydi; agent commit etmedi.
   Özellikle **negatif olgu** (gs-002 "hayır, yok") ve **sentez/karşılaştırma** (gs-019/022/023/026)
   cevaplarında — muhtemelen `submit_answer`'ın "birebir alıntı" zorunluluğu negatif/çıkarımsal
   olguyu cite etmeyi zorlaştırıp fallback'e itiyor. `context_recall` düşükleri (gs-002=0.5,
   gs-012=0.333) judge skorlama artefaktı; teşhis kanıtın mevcut olduğunu gösterdi.

2. **Fallback kümesi KARARSIZ (gürültülü kuyruk).** 2. koşumda 2/6 (gs-019, gs-026) cevaba döndü.
   M-9'un ölçtüğü gürültü tabanı ±6/31 ([[olcum-zemini-dersleri]]) ile birebir. 6 sabit "kırık soru"
   YOK; agent temkininden doğan, koşumdan koşuma oynayan bir kuyruk var.

3. **gs-012 çift-katman:** JSON bug'ı (retry ile çözülü) tam pipeline'da HÂLÂ ateşliyor ('H'=Hakan
   → bozuk tool-call JSON → retry kurtardı) AMA agent yine de reddetti → gs-012'de hem JSON tuzağı
   hem aşırı-temkin var. ×20 submit_answer harness'inde (sentetik bağlam) 20/20 CLEAN'di; tam
   pipeline'da fallback → fark, agent-döngüsü + gerçek retrieval.

## 4. İŞ LİSTESİ (kalite — Erdal kararı)

- **[ASIL] Agent aşırı-temkin fix** (agent-node davranışı, DAVRANIŞSAL karar → mimara): agent
  ilgili kanıt eldeyken `submit_answer` yerine `fallback` seçiyor. Negatif/sentez cevaplarda
  commit eşiğini gevşet (prompt / final_only yönlendirmesi). En yüksek getirili tek iş — 6
  fallback'in ~4'ü buradan kurtulabilir, retrieval'a dokunmadan.
- **[ÖLÇÜM] Fallback'i tek koşumla mühürleme:** kuyruk gürültülü (±6/31). Kalite iddiası taşıyan
  karneler `agent_runs=3` + fallback tekrar-dağılımıyla okunmalı (M-9 kuralı).
- **[TÂLİ] table_based context_precision 0.54:** retrieval gürültüsü, ayrı retrieval dilimi.

**Karar:** M-9.1 canlıda **KABUL** — gate PASS (faith 0.944 / prec 0.877 / fallback %19.35 < %25),
answered-only M-9 zemininin üstünde. gs-012 altyapı hatası kapandı. Açık kalite kuyruğu =
**agent aşırı-temkinli red** (retrieval değil), gürültülü ve mimari-karar gerektiren ayrı bir dilim.

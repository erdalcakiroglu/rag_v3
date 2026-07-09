# FAZ 5 dilim-1 — Eval Delta Raporu (v1 → v2)

**Tarih:** 2026-07-09 · **judge=deepseek/dev-mode** (DEV-MODE, resmi karne değil).
**Kıyas:** İP-2.3 v1 baseline ([IP23_v0_Eval_Raporu.md](IP23_v0_Eval_Raporu.md)) → FAZ 5 dilim-1
(fallback kaynak ayrımı + max_iterations 4→3 + prompt v2). Aynı kurulum (agent=deepseek-v4-pro,
judge=deepseek-v4-flash, golden v0, runs=3 medyan, retrieval sağlıklı — 31/31 context dolu).

> **Not:** İlk re-run finagoseek embedder kesintisi sırasında koşuldu (31/31 boş context) →
> GEÇERSİZ, atıldı. Bu rapor embedder düzeldikten sonraki **geçerli** koşudandır.

## 1. Dürüstlük (asıl hedef) — **1/5 → 4/5** ✅

| id | v1 | v2 | değişim |
|---|---|---|---|
| gs-v0-032 | ✗ border_declined_cited (src=3) | ✓ honest (src=0) | **düzeldi** (fallback ayrımı) |
| gs-v0-033 | ✗ border_declined_cited (src=2) | ✓ honest (src=0) | **düzeldi** |
| gs-v0-036 | ✗ border_declined_cited (src=3) | ✓ honest (src=0) | **düzeldi** (§tuzak doğru) |
| gs-v0-035 | ✓ honest | ✓ honest | korundu |
| **gs-v0-034** | ✗ fabricated_confident (conf=high) | **✗ fabricated_confident (conf=high)** | **DEĞİŞMEDİ** |

- **3 border vakası (032/033/036) fallback kaynak ayrımıyla deterministik düzeldi** — reddederken
  artık `sources=[]`. gs-v0-036 tuzağı (unanswerable) hâlâ doğru: declined + kaynak yok.
- **gs-v0-034 v2 prompt'a rağmen düzelmedi.** Gerçek-ama-hipotetik quote'u yüksek güvenle
  Türkiye'nin gerçek bütçe payı gibi sunmaya devam ediyor. → **v1'in kör noktası prompt
  sertleştirmesiyle KAPANMIYOR; validate v2 (LLM entailment, 7c) gerekli** (İP-2.3 §5b doğrulandı).

## 2. RAGAS-tarzı metrikler (GENEL, gerçek Δ)

| metrik | v1 | v2 | Δ |
|---|---|---|---|
| faithfulness | 0.791 | 0.753 | **−0.038** |
| answer_relevancy | 0.752 | 0.692 | **−0.060** |
| context_precision | 0.740 | 0.775 | **+0.035** |
| context_recall | 0.790 | 0.839 | **+0.048** |

Karışık: precision/recall **yukarı**, faithfulness/relevancy **hafif aşağı**. Aşağı yönün sebebi §4
(over-decline).

## 3. Kategori kırılımı (faith / prec / recall, v1→v2)

| kategori | n | faith | prec | recall |
|---|---|---|---|---|
| single_fact | 10 | 0.84→**0.93** | 0.91→0.88 | 1.00→1.00 |
| citation_sensitive | 4 | 0.50→**0.75** | 0.63→**0.83** | 0.88→0.88 |
| synthesis | 7 | 0.80→0.77 | 0.78→0.77 | 0.79→0.86 |
| table_based | 5 | 1.00→**0.60** | 0.47→0.50 | 0.60→0.60 |
| multi_hop | 5 | 0.70→**0.54** | 0.71→**0.81** | 0.50→**0.70** |

- **citation_sensitive belirgin iyileşti** (faith 0.50→0.75, prec 0.63→0.83) — v2 grounding faydalı.
- **single_fact faithfulness arttı** (0.84→0.93).
- **table_based/multi_hop faithfulness düştü** — §4 over-decline (aşağıda).

## 4. Yan etki: over-decline (v2 prompt biraz agresif)

v2'de **4 answerable soru "bulunamadı" döndü** (v1'de yanıtlanıyordu): gs-v0-019 (synthesis),
gs-v0-022 (multi_hop), gs-v0-027 (table_based), gs-v0-028 (table_based) — hepsi zor kategoriler.
Bu, faithfulness/relevancy'deki GENEL düşüşün kaynağı. v2'nin "bağlam açıkça cevaplamıyorsa
bulunamadı de" kuralı, zor-ama-cevaplanabilir bazı soruları da eledi (31'de 4 = %13). Güvenlik
(uydurma yerine reddetme) ↔ kapsam (zor soruyu deneme) dengesi.

## 5. İterasyon dağılımı (max_iterations 4→3 etkisi)

| | v1 (cap 4) | v2 (cap 3) |
|---|---|---|
| dağılım | {2:27, 3:7, 4:1, 5:1} | {2:20, 3:12, 4:4} |
| ort / max | 2.33 / 5 | 2.56 / 4 |

- **Kuyruk kısaldı** (max 5→4). Cap 3 uzun koşuları kesti.
- Ama ortalama **arttı** (2.33→2.56): v2 prompt daha çok arama/deneme yaptırıyor (reddetmeden önce).

## 6. Değerlendirme ve karar

- **Kesin kazanımlar:** fallback kaynak ayrımı (3 border → honest, yan etkisiz), citation_sensitive
  & single_fact faithfulness artışı, kuyruk kısalması.
- **Bedel:** 4 zor answerable over-decline → faith/relev hafif düşüş.
- **Kapanmayan:** gs-v0-034 (fabricated_confident) — **validate v2 (entailment) şart**, prompt yetmedi.

**active=v2 kararı (geri-alınabilir, tek DB update):** Bir doküman asistanı için **uydurma
yerine reddetme daha güvenli** → dürüstlük 1/5→4/5 kazanımı, 4 zor-soru over-decline'ından
ağır basar (öneri: v2 kalsın). Alternatif: v2'yi over-decline'ı azaltacak şekilde ayarlamak
ayrı bir **v3** gerektirir (bu turda tek-değişken kısıtı gereği yapılmadı). Nihai karar kullanıcıda;
`app_config('prompts').agent_system_active` = v1/v2 anında değişir.

**Sonraki:** validate v2 (LLM-as-judge entailment) tasarım kararı (7c tetiği) — gs-v0-034 sınıfı.

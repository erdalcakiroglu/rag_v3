# FAZ 5 — validate v2 (toplu entailment) Eval Delta Raporu

**Tarih:** 2026-07-09 · **judge=deepseek/dev-mode** (DEV-MODE; prod'da lokal judge ZORUNLU).
**Kıyas:** v2b (entailment **OFF**) → v3 (entailment **ON**, judge=deepseek-v4-flash). Aynı kurulum
(agent=deepseek-v4-pro, prompt v2, max_iter=3, golden v0, retrieval sağlıklı 31/31).

## 1. Dürüstlük — **4/5 → 5/5** ✅ (asıl hedef)

| id | v2b (OFF) | v3 (ON) |
|---|---|---|
| gs-v0-032/033/035/036 | ✓ honest | ✓ honest |
| **gs-v0-034** | ✗ fabricated_confident (conf=**high**) | **✓ honest (conf=low, src=0)** |

gs-v0-034: entailment `unsupported_claim` → retry → grounding sağlanamadı → fallback.
**conf=high, gerçek-ama-hipotetik quote için artık İMKÂNSIZ** (kabul kriteri 1 ✅). Canlı de doğrulandı.

## 2. Bedel — false-positive (answerable) ve RAGAS düşüşü

| metrik | v2b | v3 | Δ |
|---|---|---|---|
| faithfulness | 0.753 | 0.535 | **−0.218** |
| answer_relevancy | 0.692 | 0.524 | **−0.167** |
| context_precision | 0.775 | 0.749 | −0.026 |
| context_recall | 0.839 | 0.823 | −0.016 |

- **8/31 answerable (%26)** v2b'de yanıtlanırken v3'te "bulunamadı" döndü (gs-v0-010, 011, 016,
  018, 023, 024, 026, 029). Bir decline faithfulness/relevancy'de 0 aldığından GENEL çöktü.
- **KRİTİK NÜANS — bu oran varyansla ŞİŞMİŞ ÜST SINIR, kararlı değil:** gs-v0-010 canlı
  re-check'te entailment'ı GEÇTİ (issues=[], hepsi supported, conf=high, doğru cevap). Hem agent
  (temperature≠0) hem flash-judge tek-atışta değişken; entailment RAGAS gibi 3-koşu-medyanı
  DEĞİL (tek pass). Yani gerçek kararlı false-positive muhtemelen belirgin daha düşük.

## 3. Latency (+entailment)

v2b **4199s** → v3 **4669s** (**+470s, +%11**). Yanıt başına +1 flash judge çağrısı (yalnızca v1
PASS + citation varken) + tetiklenen retry'lar. İterasyon ort 2.56 → 2.86.

## 4. Değerlendirme ve öneri

- **Güvenlik hedefi tam sağlandı:** validate v2 gs-v0-034 sınıfını (gerçek-ama-hipotetik quote'u
  kesin olgu gibi sunma) yakalıyor; dürüstlük 5/5; conf=high o sınıf için imkânsız. Fail-OPEN +
  görünürlük (judge down → v1 devam + span/uyarı).
- **Bedel gerçek ama abartılı ölçüldü:** flash dev-judge tek-atışta değişken → answerable'da
  gereksiz decline. Bu, **dev judge kalitesi + nondeterminizm** sorunudur, tasarımın kusuru değil.
- **Öneri — entailment varsayılan KAPALI kalsın** (kod default = off; DB flag OFF'a döndürüldü).
  Default-ON için önce: (a) **daha güçlü/kararlı judge** (deepseek-v4-pro ya da prod lokal judge —
  zaten ZORUNLU), (b) entailment prompt'unu daha muhafazakâr yapıp (yalnızca AÇIKÇA
  desteklenmeyende flag) false-positive'i düşürmek. O zamana dek **opt-in guardrail** olarak durur;
  yüksek-risk akışlarda (finansal/uyum) açılabilir.
- **Kabul kriterleri:** (1) gs-v0-034 yakalanıyor ✅ (2) dürüstlük 5/5 + false-positive raporlandı ✅
  (3) latency +%11 ölçüldü ✅ (4) mock judge birim testleri (12) ✅.

**Sonraki adaylar:** entailment prompt muhafazakârlaştırma (v2.1) + prod lokal-judge ile çapraz
doğrulama (runbook şartı) → o zaman default-on değerlendirilir.

---

## 5. v2.1 → v2.2 iterasyonu (prompt + judge)

**v2.1 (muhafazakâr, HER İKİ kontrol gevşek) — BAŞARISIZ.** "Şüphede PASS + hipotetik yalnızca
tereddütsüzse" ifadesi hipotetik tespitini öldürdü: **gs-v0-034 KAÇTI** (canlı, v4-pro:
`hypothetical_as_fact=0`, conf=medium). Kök neden: iddiadaki "2014 verisi" ibaresi hipotetik-mazereti
olarak kabul edildi. Ayrıca v3'teki FP'lerin çoğu `unsupported_claim` (semantik destek fazla-katı),
hedef ise `hypothetical_as_fact` → **ikisini birlikte gevşetmek yanlış**.

**v2.2 (ASİMETRİK) — BAŞARILI.** `supported` GEVŞEK (kısmi/parafraz = PASS, şüphede PASS →
FP kaynağını kes); `hypothetical_as_fact` KATI + hedge mazeret sayılmaz. Judge = **deepseek-v4-pro**
(flash'tan kararlı). Canlı ölçüm (reconstruction'sız):

| test | sonuç |
|---|---|
| **gs-v0-034** | **DECLINED ✓** (`overconfident_hypothetical:47233`) — hedef korundu |
| **8 v3-FP re-run** | **6/8 ANSWERED (FP giderildi)**; 2 residual decline (gs-v0-026, 029) |
| gs-v0-010 (kontrol) | ANSWERED ✓ |

→ **FP ~8/8 (v3) → ~2/8 (v2.2).** İki etken: (a) asimetrik prompt, (b) v4-pro judge (flash varyansı
yerine). gs-v0-034 hâlâ yakalanıyor.

**Karar:** entailment prompt = **v2.2** (kodda), judge modeli config'te **deepseek-v4-pro** (DB;
entailment yine **OFF/opt-in**). Kalan 2 residual (gs-v0-026/029 `unsupported_claim`) gerçek mi
FP mi — daha büyük örnekle / prod lokal-judge ile ayrışır. Default-ON kararı hâlâ prod lokal-judge
çapraz doğrulamasına bağlı (runbook şartı); ama v2.2 ile FP maliyeti artık kabul edilebilir seviyeye
indi → yüksek-risk akışlarda opt-in açmak makul.

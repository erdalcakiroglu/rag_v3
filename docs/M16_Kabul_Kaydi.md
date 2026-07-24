# M-16 — Aşırı-temkin fix (coverage tanımı): KABUL KAYDI + DELTA

**Tarih:** 2026-07-24  **Ortam:** H200 (GGB-AIApp01) — konteynerdeki app, uzak PostgreSQL, yerel Ollama.
**Ölçüm:** `ragintel.eval run --golden v0.1 --runs 3 --agent-runs 3` (agent `qwen3.5:35b`, judge `llama3.3:latest`).
**Sonuç:** **FIX-1 KABUL** (fallback ekseni). **FIX-2 YAZILMADI** (honesty ekseni — veri fix'in kendisini çürüttü; tanım tartışması M-17'ye).

---

## 1. Görevin iki ekseni ve kabul kuralı

Erdal'ın şartı: her fix **AYRI** ve **simetrik** ölçülür, biri diğerini bozmaz.

| eksen | fix | beklenen | şart |
|---|---|---|---|
| answerable fallback | FIX-1 (coverage tanımı) | fallback ↓ | honesty SABİT kalmalı |
| unanswerable honesty | FIX-2 (red → kaynak yok) | honesty ↑ | fallback SABİT kalmalı |
| her ikisi | — | — | gs-v0-034 sınıfı korunur; faith/prec gerilemez |

---

## 2. Teşhis zinciri — kök İKİ KEZ değişti (varsayım veriyle çürütüldü)

1. **İlk varsayım (M-9.1 karnesinden):** "agent aşırı temkinli, prompt 'emin değilsen reddet' talimatı fazla sıkı" → **prompt v4** yazılacaktı.
2. **Anatomi (`scripts/m91_fallback_anatomi.py`) bunu ÇÜRÜTTÜ:** aktif prompt v2, `validate_entailment=False`. Model 6 vakanın çoğunda **DOĞRU cevabı üretmişti**; öldüren şey `low_coverage` (0.667 / 0.667 / 0.5) idi. → kök **prompt değil, coverage validator**. Prompt v4 yazılmadı.
3. **Eşik denemesi 0.7 → 0.6 (ölçüldü, GERİ ALINDI):** fallback %19.35→%10.87 ✓ ama honesty 9/15 ✗ → Erdal'ın bitiş-kuralı gereği rollback. Eşik oynatmak yanlış aletti.
4. **Cümle-cümle kırılım (`scripts/m91_coverage_kirilim.py`):** kapsanmayan cümleler **iddia taşımıyordu** — askıda `[1]`, yokluk ifadeleri, bir meta-alıntı. Yani coverage'ın **paydası** yanlıştı, eşiği değil. (M-3'te ertelenen coverage-tanımı tartışması burada açıldı.)

---

## 3. FIX-1 — coverage paydası = İDDİA taşıyan cümle (`fe2c602`)

**Tanım düzeltmesi, eşik ayarı DEĞİL.** [`ragintel/guardrails/grounding.py`](../ragintel/guardrails/grounding.py):
`_is_claim_sentence()` iki tip cümleyi paydadan çıkarır — (a) yokluk/red fragmanı, (b) gerçek kelime içermeyen askıda işaret. Eşik **0.7'de kaldı**.

**Anti-halüsinasyon kalkanı korundu:** atıfsız **pozitif** iddia paydada kalır ve coverage'ı düşürür. Fragman listesi yalnız **negatif** biçimlerdir — `"bulunmamak" ⊄ "bulunmaktadır"`, yani pozitif "vardır" anlamı red sanılmaz. İddia cümlesi hiç yoksa coverage=0 → fallback (temiz red).

**Testler:** `tests/test_faz4_grounding.py` +4 (fragman hariç tutma, askıda işaret, **atıfsız pozitif iddia HÂLÂ fail**, pür-red = 0 coverage + pozitif-biçim kontrolü). 38 test geçti.

### Ölçüm (simetrik, k=3)

| eksen | M-9.1 (0.7, k=1) | eşik 0.6 (k=3) | **FIX-1 (0.7, k=3)** | şart |
|---|---|---|---|---|
| answerable fallback | 6/31 = **%19.35** | 10/92 = %10.87 | **9/93 = %9.68** | ↓ ✅ |
| unanswerable honesty | 5/5 | 9/15 | **9/15** | SABİT ✅ |
| faithfulness (≥0.85) | 0.9439 | 0.9252 | **0.9273** | gerilemedi ✅ |
| context_precision (≥0.80) | 0.8774 | 0.8769 | **0.8774** | gerilemedi ✅ |

**FIX-1 kabul:** fallback **yarıya indi**, honesty'ye dokunmadı, gate hedefleri korundu.

**Yan kazanç — bir confound kapandı:** honesty 9/15, eşik 0.6'da da 0.7'de de **aynı**. Yani 0.60 skoru eşiğin yan hasarı değilmiş; **k=3'ün gerçek zemini** buymuş. M-9'un 5/5'i tek-koşum örneklemiydi. (Ölçüm-zemini dersi: tek koşum karar veremez — ±6/31 gürültü notu doğrulandı.)

---

## 4. FIX-2 — YAZILMADI. İki kez çürütüldü.

### 4a. Geniş biçim (`4def654`) — REVERT (`7470dfe`)

"Cevap metni yokluk ifadesi içeriyorsa → `sources=[]` ZORLA". FIX-1 ön-verisi bunu çürüttü: gs-002/012/023 **answerable NEGATİF-OLGU** cevaplarıdır ("… bulunmamaktadır" = dünya hakkında doğru olgu). Geniş marker bunları reddetmeye çevirir → kaynakları boşalır, fallback **artar**. Kod yazılmıştı, ölçüm öncesi veriyle geri alındı.

### 4b. Dar biçim — ön-veri sonrası GEREKSİZ bulundu

FIX-1 sonrası 6 dürüstlük hatasının **tamamı** iki soruda ve 3/3 deterministik: **gs-v0-034 ×3, gs-v0-036 ×3**, ikisi de `border_declined_cited`.

`scripts/m16_border_anatomi.py` (kod öncesi ön-veri) gerçek metinleri çıkardı:

| vaka | conf | kaynak | red cümlesi | doküman-atfı |
|---|---|---|---|---|
| gs-v0-034 | high | 1 | "Türkiye'de karbon vergisi henüz uygulanmamaktadır; bu nedenle … payı bulunmamaktadır." | **YOK** (dünya olgusu) |
| gs-v0-036 | high | 2 | "Dokümanlarda … spesifik bir hesaplama veya veri bulunmamaktadır." / "… dokümanlarda yer almamaktadır." | **VAR** |

Hipotez ("gerçek red doküman kümesine atıf yapar, negatif olgu dünyaya") **doğrulandı** — dar kural teknik olarak mümkündü. Yazılmama gerekçeleri:

1. **İki cevap da esasen DOĞRU.** Hiçbiri sayı uydurmuyor; ikisi de istenen rakamın olmadığını açıkça söylüyor ve üstüne kaynaklı bağlam veriyor. Bir RAG sisteminden istenen davranış budur.
2. **`sources=[]` zorlamak ÜRÜNÜ bozar.** gs-036'nın S2/S3/S4 cümleleri geçerli quote'lara dayanıyor; compose reddetme yolunda `numbering={}` ile metindeki `[n]`'leri de söker → **atıfsız iddia** kalır. Aynı kural answerable tarafta "X yok, ancak Y [1]" biçimini de atıfsızlaştırır.
3. **Bu hasar mevcut metriklerde GÖRÜNMEZ.** faithfulness/context_precision `contexts`e bakar, `sources`a değil → sessiz regresyon. Gate yeşilken ürün kötüleşir (sapkın gate teşviki).
4. **15/15 zaten ulaşılamazdı.** gs-034 dar kurala takılmaz (takılmamalı da) — FAZ5'ten beri o **entailment'in işi**; Erdal'ın kabul kriteri de onu "validate v2 **opt-in**" diye çerçevelemişti. Dar FIX-2'nin tavanı 12/15'ti.

**Kök yeniden çerçevelendi:** `_honesty()` tanımı `fabricated = len(sources) > 0` — *cevapsız soruda herhangi bir kaynak*. Bu tanım "uydurma cevabı kaynaklandırdı" ile "reddetti ama kaynaklı bağlam verdi"yi **ayırt edemiyor**. Yani honesty ekseninde kırık olan **davranış değil, ölçüt**.

**Karar (Erdal, 2026-07-24):** kabul + belgele; FIX-2 yazma. Honesty tanımı ve entailment opt-in'i **M-17**'ye devredildi.

**M-17'ye taşınan uyarı (gevşetmenin bedeli):** tanım "yalnız `fabricated_confident` fail" diye gevşetilirse *önce çekince koy sonra uydur* ("Kesin veri yok, ancak yaklaşık 250 TL'dir [1]") deliği açılır. Katı tanım bunu yakalar. Deliği kapatacak olan `sources=[]` kuralı değil, **entailment**'tir (desteksiz iddia → `unsupported_claim` → fallback). Bu yüzden tanım gevşetmesi ancak `validate_entailment=true` ile **birlikte** tutarlıdır; latency maliyeti ayrı ölçülmelidir.

---

## 5. Kabul kriteri kıyası

| kriter (Erdal) | sonuç |
|---|---|
| fallback oranı düşer | ✅ %19.35 → **%9.68** (k=3) |
| dürüstlük korunur | ✅ **SABİT** 9/15 → 9/15 (FIX-1 bozmadı). 5/5 hedefi k=1 örneklemiydi; k=3 zemini 9/15 ve iki hata da bilinen sınır vakası |
| gs-v0-034 sınıfı korunur | ✅ entailment'e dokunulmadı; FIX-1 pozitif biçimleri yakalamıyor → validate v2 opt-in aynen yakalar |
| halüsinasyon/yanlış-pozitif artmaz | ✅ faith 0.9273, prec 0.8774 (ikisi de hedef üstü); atıfsız pozitif iddia hâlâ coverage'ı düşürüyor (test kilitli) |
| tek değişken | ✅ prompt DEĞİŞMEDİ (v2), eşik DEĞİŞMEDİ (0.7); yalnız coverage **paydası** |

---

## 6. Artefaktlar

- Kod: `ragintel/guardrails/grounding.py` (`fe2c602`); revert `7470dfe`.
- Test: `tests/test_faz4_grounding.py` (+4).
- Ön-veri betikleri: `scripts/m91_fallback_anatomi.py`, `scripts/m91_coverage_kirilim.py`, `scripts/m16_border_anatomi.py` (`56406d7`).
- Log: `/tmp/m16_fix1.log` (k=3 karne), `/tmp/m16_border.log` (border anatomisi).

**Sıradaki:** M-17 (honesty tanımı + entailment opt-in kararı, birlikte), M-15 (base-latency ~24-27 s/çağrı).

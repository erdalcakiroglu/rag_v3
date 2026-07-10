# M-3 — Üç Küçük Düzeltme (M-2 ön-veri yan bulguları)

**Durum:** Üçü de düzeltildi, testlendi, canlı doğrulandı. Tam süit **324 passed** (M-3 öncesi 304).

---

## (a) Scope taksonomi uyumsuzluğu — veri düzeltmesi

**Ön-veri:** `envanter` kullanıcısının scope'u `['inventory']`, dokümanların `doc_scope`'u
`'envanter'`. `inventory` scope'unda **0 doküman** var (2 doküman `envanter`'de). Fail-closed
katman doğru çalışıyordu; **veri yanlıştı** — kullanıcı kendi belgelerini göremiyordu.

**Düzeltme** (uygulandı; şema değişmedi):

```sql
UPDATE ragintel.users
   SET allowed_doc_scopes = ARRAY['envanter']
 WHERE user_id = 'envanter' AND allowed_doc_scopes = ARRAY['inventory'];
-- 1 satır. Türkçe taksonomiye sabitlendi (doc_scope ile aynı sözcük).
```

**Yan bulgu — sızıntı testi boş kümeyle geçiyormuş.**
`test_scope_isolation_bidirectional` karşı tarafı `['inventory']` ile kuruyordu; o scope'ta hiç
doküman olmadığı için `inv_hits` **her zaman boş** dönüyor, "sızıntı yok" iddiası vacuously true
oluyordu. Test yeniden yazıldı: artık her iki taraf da **kendi belgelerini gördüğünü** iddia
ediyor (boş kümeyle geçemez), kesişimin boş olduğunu ve **karşı-sorguda** (her kullanıcı
diğerinin içeriğini arasa bile) scope dışına çıkılamadığını doğruluyor.

Ek olarak `test_faz_m3_fixes.py`, **öksüz scope** kontrolü yapıyor: hiçbir dokümanla eşleşmeyen
kullanıcı scope'u kalırsa kırmızı yanar (taksonomi kayması bir daha sessizce oluşmasın).

**Kabul:** envanter kullanıcısı kendi tablosunu görüyor; default kullanıcı aynı tabloyu
göremiyor (sızıntı 0).

---

## (b) Citation numaralandırma — kök neden ve düzeltme

**Kök neden UI'da değil, sözleşmenin yokluğunda.** `prompts.py` `[n]` biçimini **hiç
tanımlamıyor**; model `[1]`, `[2]` işaretlerini kendi uyduruyor. `compose._sources` ise
kaynakları **citation sırasına göre** 1..N numaralandırıyordu. İki bağımsız numaralandırma →
örtüşmüyor. İki ayrı belirti:

1. **Askıda referans:** model 1 citation verip metinde `[2]` yazıyordu (gs-v0-029'da görüldü).
2. **Mükerrer kaynak:** aynı chunk iki kez alıntılanınca kaynak listesinde iki girdi
   (`[1]` ve `[3]` aynı chunk — GGB yanıtında 4 kaynağın 2'si mükerrerdi).

**Düzeltme** ([compose.py](../ragintel/agents/nodes/compose.py)) — model işaretlerine
güvenilmiyor, deterministik yeniden üretiliyor:

- `_sources` artık **chunk_id'ye göre tekilleştiriyor** (ilk görülme sırası `n`'i belirler) ve
  `chunk_id → n` haritasını döndürüyor.
- `_renumber_answer` modelin `[k]`'lerini söküyor, her citation'ın `claim`'ini metinde bulup
  arkasına doğru `[n]`'i yerleştiriyor; claim parafraz edilmişse işaret cümleye zorlanmıyor,
  yanıtın sonuna ekleniyor. Reddetme yolunda (`sources=[]`) metinde **askıda `[n]` kalmıyor**.

**Bilinçli sınır:** model **hiç** işaret koymadıysa biz de **uydurmuyoruz** — yanıt metni birebir
korunur. Değişmez tek yönlüdür: *metindeki her `[n]`, kaynak listesinin n'inci girdisine denk
gelir*; her kaynağın metinde işareti olması gerekmez. (İlk denememde marker'sız yanıtlara `[1]`
ekleniyordu ve `test_faz4_graph_flow` bunu regresyon olarak yakaladı — davranış geri alındı.)

**Not:** Tekilleştirme, aynı chunk'a ait ikinci citation'ın `quote`'unu düşürür (kaynak tek
girdi, ilk quote ile). Kaynak listesi kısalır ama bilgi kaybı kullanıcıya görünmez — chunk aynı.

**Kabul (canlı):** GGB yanıtı artık 4 değil **2 kaynak** döndürüyor; işaretli bir yanıtta `[1]`
tek kaynağa doğru denk geliyor; askıda referans yok.

---

## (c) PII tarih false-positive — sürüm dizeleri

**Kök neden:** `\d{1,2}[./]\d{1,2}[./]\d{4}` deseni SQL Server sürümü `14.0.3460.9` içindeki
`14.0.3460`'ı tarih sanıyor, geriye `[TARİH].9` kalıyordu.

**Düzeltme** ([pii.py](../ragintel/guardrails/pii.py)) — TCKN'deki checksum felsefesinin eşi,
iki katman:

1. **Sınır koruması (lookaround):** match'in solunda/sağında nokta+rakam varsa (daha uzun bir
   nokta ayraçlı dizinin parçasıysa) eşleşme kurulmaz → `14.0.3460.9`, `1.14.0.3460`.
   Ayrıca ayraç artık geri-referansla tutarlı (`12.05/1980` gibi karışık ayraç kabul edilmez).
2. **Aralık doğrulaması** (`_is_plausible_date`): gün 1-31, ay 1-12, yıl 1900-2099;
   `dd.mm.yyyy` veya `mm/dd/yyyy` sırası kabul edilir. `14.0.3460` → ay=0, yıl=3460 → **tarih değil**.
   Bu, `10.12.2019.5` gibi hem geçerli-görünen hem sürüm olan dizeleri de (1) sayesinde korur.

**FP regresyon fixture'ı** (`_NOT_DATES`): `14.0.3456.9`, `14.0.3456`, `1.14.0.3456`,
`10.50.6000.34`, `12.05.3456`, tek yıl `1990`.
**Gerçek tarihler** (`_REAL_DATES`) maskelenmeye devam ediyor: `12.05.1980`, `1/1/1990`,
`1980-05-12`, `2026-03-02`, cümle sonu `12.05.1980.`

**Kabul (canlı):** `Product Version 14.0.3460.9` maskesiz dönüyor (`pii_masked_count=0`);
birim testlerde gerçek tarihler hâlâ `[TARİH]`.

---

## Testler

- `tests/test_faz_m3_fixes.py` — 20 test: (b) 6 test (askıda referans, mükerrer chunk
  tekilleştirme, parafraz, reddetme yolu, uçtan uca hizalama, marker'sız yanıt korunur),
  (c) 12 parametrik (6 FP + 5 gerçek tarih + karışık metin), (a) 2 `@db`.
- `tests/test_faz6_auth.py::test_scope_isolation_bidirectional` — yeniden yazıldı (boş kümeyle
  geçemez, karşı-sorgu dahil).
- Tam süit: **324 passed** (`-m "not slow"`).

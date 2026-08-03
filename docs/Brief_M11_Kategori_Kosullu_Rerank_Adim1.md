# Brief — Görev #11 · Kategori-koşullu rerank · ADIM 1 (ölçekte doğrulama)

**Kime:** Sonnet 4.8 · **Kimden:** Mimari · **Tarih:** 2026-08-03
**Statü:** karar hizalandı (golden'ı Sonnet taslaklar → insan onaylar; n≈15-20/kategori) → GREENLIGHT.
**İlke:** Ölçüm. Bu adım classifier/gating KURMAZ — yalnız "kazanç ölçekte duruyor mu?" sorusunu yanıtlar.
**Kapsam çiti (önce):** prod/DB/config **DEĞİŞMEZ**. v0 baseline **DOKUNULMAZ** (mühürlü). Ölçüm
`scripts/rerank_ab_probe.py` ile, `StoreRetriever` açık `rerank_fn` üzerinden (DB-pinli `rerank_backend` baypas).

---

## 0. Neden bu adım (ön-veri özeti — tekrar ölçme)

changelog v1.11 / `rerank-ab-onveri`: TEI rerank table_based **+0.400**, synthesis **+0.143** güçlü;
ama HEDEF multi_hop **−0.200**, single_fact **−0.100**. **Küçük-n (4-10): yön güvenilir, büyüklük
KIRILGAN** — recall@5 birimi 0.20/soru, deltalar 1-2 soru salınımı. Blanket rerank ELENDİ.
Açık soru tek: **table/synthesis kazancı n büyüdükçe duruyor mu?** Durmuyorsa koşullu-rerank da ölür
(#11 kapanır). Duruyorsa Adım-2 (gating tasarımı) meşrulaşır.

---

## 1. Golden genişletme — Sonnet taslaklar, insan onaylar

**Hedef:** `table_based` ve `synthesis` kategorilerini **kategori başına ~15-20 soruya** çıkar.
Opsiyonel ama önerilir: `multi_hop`'u da ~15'e çıkar → Adım-2'nin asimetrik-maliyet kapısının
dayandığı **−0.200 gerçek mi** teyidi (ucuz ek, zaten golden üretiyorsak).

**Kritik kısıt — grounded olmalı:** her yeni soru, mevcut H200 korpusundaki chunk'lara
**evidence-quote eşlemeli** olmalı (v0 süreci: quote → chunk eşleşme oranı **≥%95**). Salt soru
yazmak değil; soru + korpustan birebir alıntı + hedef chunk.

**Akış (insan-döngüde):**

1. **(1a) Aday chunk taraması — konteynerde salt-okuma.** Sonnet, korpustan table-yoğun ve
   sentez-gerektiren chunk'ları yüzeye çıkaran bir tarama probu yazar (`scripts/golden_aday_tarama_probe.py`,
   salt-SELECT: tablo işareti / çok-belge örtüşmesi / uzun bağlam). Kullanıcı konteynerde koşar,
   çıktı = aday chunk + bağlam listesi.
2. **(1b) Sonnet taslak üretir.** Her aday için soru + evidence-quote + hedef chunk_id taslağı
   (`docs/golden_aday_M11_taslak.md`). Kategori dengesi ve zorluk çeşitliliği gözetilir.
3. **(1c) İnsan onayı — KAPI.** Kullanıcı taslakları gözden geçirir/düzeltir/eler (v0 onay tablosu
   ritmi). Yalnız onaylı sorular ilerler. **Bu adım atlanmaz.**
4. **(1d) Golden versiyonu yükle — additive.** Onaylılar **YENİ** golden versiyonu olarak yüklenir
   (öneri `--version rr-ext-v1`), v0'a dokunmadan. Yükleme kullanıcı tarafından (DDL/insert konteynerde).
   Eşleşme raporu ≥%95 doğrulanır.

**Kabul (1):**
- [ ] table_based ve synthesis her biri **≥15** onaylı-grounded soru; (ops.) multi_hop ≥15.
- [ ] evidence-quote → chunk eşleşme **≥%95** (rapor).
- [ ] v0 versiyonu **değişmedi** (yeni versiyon additive; `list_golden_records(v0)` byte-aynı).
- [ ] Her soru insan-onaylı (taslak≠final; onay kaydı `golden_aday_M11_taslak.md`'de işaretli).

---

## 2. A/B tekrarı — mevcut prob, genişletilmiş golden

Yeni araç YOK. `scripts/rerank_ab_probe.py` genişletilmiş versiyonla koşulur:

```
docker cp scripts/rerank_ab_probe.py ragintel-api:/app/rerank_ab_probe.py
docker exec ragintel-api python /app/rerank_ab_probe.py --golden rr-ext-v1 --json
```

TEI CPU yeter (kalite CPU/GPU özdeş). Prob'un fail-open'ı zaten kaldırıldı (fea7485) → sessiz
passthrough imkânsız; recall@20 kategoride özdeş olmalı (havuz aynı = sağlık kontrolü).

**Kabul (2):**
- [ ] Koşumda fallback logu YOK; recall@20 her kategoride A==B.
- [ ] Kategori-kırılımı Δ tablosu (recall@5/@10, MRR, nDCG) raporlanır.

---

## 3. Karar kapısı (Adım-1 çıktısı)

- **Kazanç DURUYORSA** (table/synthesis n≈15-20'de hâlâ anlamlı-pozitif, işaret sağlam):
  → Adım-2 **GREENLIGHT** = gating tasarımı. Asimetrik maliyet: multi_hop→table yanlış-sınıflaması
  çok daha pahalı (rerank multi_hop'u bozuyor) → kapı **muhafazakâr**: "emin değilsen rerank AÇMA".
  Seçenek: (i) LLM sorgu-tipi sınıflandırıcı vs (ii) hafif sezgisel (tablo-lexeme/uzunluk sinyali) —
  ikisi de hataya açık; ayrı ön-veri konusu.
- **Kazanç ERİRSE / işaret kararsızlaşırsa** (n büyüyünce +0.400 → gürültü):
  → koşullu-rerank da **düşer**; #11 kapanır, changelog + `rerank-ab-onveri` güncellenir.
  Not: bu da geçerli sonuç — pahalı gating'i boşuna kurmaktan korur.

---

## 4. Riskler / dürüst sınırlar

- **Taslak≠veri.** Sonnet'in ürettiği sorular insan onayından geçmeden golden sayılmaz; onay kapısı
  (1c) atlanırsa "kendi ürettiğim soruyla kendi hipotezimi doğrulama" yanlılığı doğar. Onay şart.
- **Prod'da kategori etiketi YOK.** Adım-1 golden-etiketli ölçüm; Adım-2'nin gating'i canlıda soru-tipini
  TAHMİN etmek zorunda — Adım-1 pozitif çıksa bile gating'in kendi hata payı ayrı risktir.
- **Kategori-koşullu ≠ ücretsiz.** TEI-GPU kurulumu (ADR-014 ertelendi) ancak Adım-2 olgunlaşırsa geri gelir.

---

## 5. Kapsam çiti (tekrar)

Üretilecek: `scripts/golden_aday_tarama_probe.py` (salt-SELECT), `docs/golden_aday_M11_taslak.md`,
yeni golden versiyonu (additive). **DIŞINDA:** v0 golden, prod config (`rerank_backend`), DB şeması,
retrieval/agent kodu. Oralarda diff çıkarsa **dur ve sor**.

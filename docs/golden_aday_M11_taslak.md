# Görev #11 Adım-1 · golden aday TASLAK (rr-ext-v1)

> **STATÜ: İNSAN ONAYI BEKLİYOR (Brief §1c kapısı).** Bu dosya Sonnet-taslağıdır;
> onaydan geçmeden golden DEĞİLDİR. Onaylanınca `rr-ext-v1` olarak yüklenir (additive;
> v0 DOKUNULMAZ). Kaynak aday havuzu: `docs/result.txt` (SALT-OKUMA tarama probu).
> Bkz. `docs/Brief_M11_Kategori_Kosullu_Rerank_Adim1.md`.

## Ön-veri özeti (havuz kalite sayımı)

- **table_based:** ham 40 → temiz-ayrık ~14 veri tablosu (elenen: 4 yinelenen-doküman çifti
  `_023-fail≡_009`, 6 içindekiler-tablo-sanılan, 3 dejenere başlıksız-sayı, 4 gümrük CN-kod
  listesi marjinal, 3 aynı-tablo satır-parçası). **Aşağıda 16 taslak** — hedef n=15-20 karşılanır.
- **synthesis:** ham 30 → kullanılabilir ~17 küme (elenen: 12 bibliyografya, 1 yinelenen-doküman).
  **Sayfa gerekli** (tarama probu synth'te page vermedi) → §B'deki tek sorgu ile çözülüp
  sonra taslaklanır.

## Eşleme kuralı (neden bu alıntılar)

`gold_evidence.quote` → `normalize_for_quote` (NFKC + küçük harf + boşluk-sıkıştırma) →
`file_name + page` ile daraltılmış adaylarda `chunk_text_norm` **substring** araması
(`retrieval_benchmark.py:87-91`). Her alıntı, `result.txt` önizlemesinde **birebir görünen**,
kırpma `…`'ini **geçmeyen** bir söz dizisidir → eşleme ≥%95 hedefi tasarımdan sağlanır.
`kaynak_chunk` alanı yalnız denetim içindir (golden'a yazılmaz).

---

## A) TABLE_BASED taslakları (16) — yükleme-hazır

| # | id | soru (kısalt.) | zorluk | dosya · s. | kaynak_chunk |
|---|----|----------------|--------|-----------|--------------|
| 1 | rr-ext-tb-01 | Light akaryakıt tüketim vergisi | 2 | 14-TEZ-304230.pdf · 61 | 65496 (T04) |
| 2 | rr-ext-tb-02 | CO2 vergisi 1996/2005 | 2 | 14-TEZ-304230.pdf · 50 | 65488 (T16) |
| 3 | rr-ext-tb-03 | Lehr 2012 Almanya istihdam etkisi | 2 | _021.pdf · 2 | 66065 (T15) |
| 4 | rr-ext-tb-04 | Avustralya ulaşım fiyat değişimi | 3 | 018.pdf · 37 | 65194 (T17) |
| 5 | rr-ext-tb-05 | Tarım sektörü üretim azalışı (Sen.1) | 2 | _009.pdf · 10 | 65997 (T19) |
| 6 | rr-ext-tb-06 | Grains crops @100$/ton | 2 | 015.pdf · 10 | 65108 (T20) |
| 7 | rr-ext-tb-07 | "Current study" GSYİH/GHG 2030 | 2 | _021.pdf · 10 | 66076 (T21) |
| 8 | rr-ext-tb-08 | Solid fuels C2 GHG değişimi | 3 | 007.pdf · 26 | 64965 (T24) |
| 9 | rr-ext-tb-09 | Çin 2020 emisyon payı | 1 | 15-ET004233.pdf · 96 | 65682 (T28) |
| 10 | rr-ext-tb-10 | Türkiye emisyonu 1990/1993 | 2 | 12.pdf · 8 | 65334 (T38) |
| 11 | rr-ext-tb-11 | Elektrik talebi 2030 TWh | 3 | _021.pdf · 4 | 66067 (T23) |
| 12 | rr-ext-tb-12 | Doğalgaz varsayımı | 2 | _008.pdf · 13 | 65954 (T35) |
| 13 | rr-ext-tb-13 | Isı pompaları 2030 katkısı | 2 | _021.pdf · 4 | 66068 (T33) |
| 14 | rr-ext-tb-14 | Arjantin karbon fiyatlandırma yılı | 1 | 012.pdf · 7 | 65057 (T26) |
| 15 | rr-ext-tb-15 | Karbon fiyatlandırma enerji geçişi | 2 | 012.pdf · 5 | 65055 (T37) |
| 16 | rr-ext-tb-16 | Danimarka vergi oranı aralığı | 1 | 02-KARBON-VERGISI-1890343.pdf · 15 | 65224 (T36) |

### Detay (soru · ideal cevap · alıntı)

**1 · rr-ext-tb-01** — S: "14-TEZ-304230 belgesindeki yakıt vergileri tablosuna göre
'Light akaryakıt' için tüketim vergisi kaç birimdir?"
İdeal: "Tüketim vergisi 102,60 (10³ litre başına); ayrıca stratejik depolama vergisi 12,50."
Alıntı: `Light akaryakıt | 10 3 litre | 27.50 | 84.60 | 102.60 | 12.50`

**2 · rr-ext-tb-02** — S: "14-TEZ-304230 hafif yakıt tablosunda CO2 vergisi 1996 ve 2005
yıllarında sırasıyla kaçtır (euro/cent/l)?"
İdeal: "1996'da 2,30; 2005'te 4,78."
Alıntı: `CO2 vergisi | - | - | 2.30 | 4.54 | 4.54 | 4.78`

**3 · rr-ext-tb-03** — S: "_021 belgesindeki politika-etki tablosunda Lehr et al. (2012)
çalışması Almanya için yenilenebilir enerjinin istihdam etkisini nasıl raporluyor?"
İdeal: "Pozitif; 2030 için 150.000 ilave istihdam."
Alıntı: `Germany | Renewable energy | 2030 | Employment | Positive, 150,000 additional jobs`

**4 · rr-ext-tb-04** — S: "018 belgesindeki fiyat-değişim tablosunda Avustralya için ulaşım
(benzin/dizel) sütunlarındaki değerler nedir?"
İdeal: "Benzin (gasoline) 157, dizel 99."
Alıntı: `Australia | 0 | 0 | 79 | 6 | 24 | 96 | 157 | 99 | -54 | 68`

**5 · rr-ext-tb-05** — S: "_009 belgesindeki Senaryo 1 tablosunda 'Tarım, Orman ve Balıkçılık'
sektörü için gerekli üretim değeri azalışı (mn. TL) nedir?"
İdeal: "13.631 mn TL (birim üretim başına emisyon 0,00027 ton/TL; emisyon azaltım gereksinimi 3,67 mn ton)."
Alıntı: `Tarım , Orman ve Balıkçılık | 0.00027 | 3.67 | 13631`

**6 · rr-ext-tb-06** — S: "015 belgesindeki tabloda 'Grains crops' sektörünün üretimi
100 $/ton karbon vergisinde yüzde kaç değişir?"
İdeal: "-%0,75 (10$: -0,07; 25$: -0,16; 50$: -0,35; 100$: -0,75)."
Alıntı: `Grains crops | -0,07 | -0,16 | -0,35 | -0,75`

**7 · rr-ext-tb-07** — S: "_021 belgesindeki tabloda 'The current study' senaryosu 2030'da
BAU'ya göre GSYİH ve sera gazı emisyonlarını nasıl değiştiriyor?"
İdeal: "GSYİH +%1,0; sera gazı emisyonları -%9,1."
Alıntı: `The current study | 1.0% | - 9.1% |`

**8 · rr-ext-tb-08** — S: "007 belgesindeki tabloda 'Solid fuels' sektörü için C2 senaryosunda
GHG emisyonlarındaki yüzde değişim nedir?"
İdeal: "-%18,92 (C1'de -%10,58)."
Alıntı: `Solid fuels | -0.52 | -0.90 | -12.87 | -20.39 | -10.58 | -18.92`

**9 · rr-ext-tb-09** — S: "15-ET004233 emisyon tablosuna göre Çin'in 2020 yılı toplam
içindeki payı yüzde kaçtır?"
İdeal: "%30,65 (2020 emisyonu 10,67)."
Alıntı: `Çin | 2.48 | 3.44 | 8.62 | 10.67 | %30.65`

**10 · rr-ext-tb-10** — S: "12 belgesindeki tabloya göre Türkiye'nin toplam emisyonu 1990'da
kaç milyon tondur ve 1993'te 1990'a göre yüzde kaç artmıştır?"
İdeal: "1990'da 214 milyon ton; 1993'te 1990'a göre +%10,6 (236,7 milyon ton)."
Alıntı: `1990 | 214 | | 1991 | 221,1 | 3,3 | 3,3 1992 | 227,4 | 6,3 | 2,8 1993 | 236,7 | 10,6 | 4,1`

**11 · rr-ext-tb-11** — S: "_021 belgesindeki dönüşüm tablosunda enerji verimliliği için
2030'da elektrik talebi BAU'da ve dönüşüm senaryosunda kaç TWh/yıl öngörülüyor?"
İdeal: "BAU 462 TWh/yıl (elektrifikasyon dahil); dönüşüm ile 420 TWh/yıl (2021 seviyesi 331 TWh/yıl)."
Alıntı: `331 TWh/year electricity demand | 462 TWh/year electricity demand (including electrification) | 420 TWh/year`

**12 · rr-ext-tb-12** — S: "_008 belgesindeki varsayımlar tablosunda elektrik üretim
sektöründe doğalgaz kullanımına dair varsayım nedir?"
İdeal: "Doğalgaz kullanımı, arz kaynaklarına ilişkin belli kapasite ve fiyat kısıtları altında model seçimine bırakılmıştır."
Alıntı: `Doğalgaz | Elektrik enerjisi üretimi amaçlı doğalgaz kullanımı arz kaynaklarına ilişkin belli kapasite ve fiyat kısıtları altında model seçimine bırakılmıştır`

**13 · rr-ext-tb-13** — S: "_021 belgesindeki dönüşüm tablosunda ısı pompaları (heat pumps)
için 2030 katkısı ve toplam tüketim etkisi nedir?"
İdeal: "2 milyon ısı pompası (BAU 0,5 milyon); baz duruma göre toplam 2,4 TWh/yıl ek tüketim."
Alıntı: `2 million heat pumps | - | 0.5 million heat pumps | Total consumption of 2.4 TWh/year over the baseline`

**14 · rr-ext-tb-14** — S: "012 belgesindeki karbon fiyatlandırma tablosunda Arjantin hangi
yıl ve hangi araçla (karbon vergisi/ETS) listelenmiştir?"
İdeal: "Arjantin: 2018, Karbon Vergisi (KV)."
Alıntı: `Arjantin | KV | 2018 | Var | Var`

**15 · rr-ext-tb-15** — S: "012 belgesine göre karbon fiyatlandırmasının enerji üretiminde
hedeflediği geçiş nedir?"
İdeal: "Kömürden doğal gaza ve yenilenebilir kaynaklara, oradan da karbon yakalama-depolama teknolojileri ile nükleer enerjiye geçiş."
Alıntı: `Enerji üretimi: kömürden, doğal gaza ve yenilenebilir kaynaklara ve oradan da belki de karbon yakalama ve depolama teknolojileri ile nükleer enerjiye geçiş`

**16 · rr-ext-tb-16** — S: "02-KARBON-VERGISI belgesindeki tabloda Danimarka'nın karbon/enerji
vergisi oranı hangi aralıkta verilmiştir?"
İdeal: "27–55 (î/vergi); endüstriye daha düşük oran ve ödemeler."
Alıntı: `Danimarka | 27-55 | Dahadüşük oran ve ödemeler`

### Yükleme bloğu (onay SONRASI — JSONL, rr-ext-v1)

> Onaydan geçen satırlar bu bloktan `rr-ext-v1` set'ine yüklenir. `kaynak_chunk`
> tabloları golden'a GİRMEZ (yalnız yukarıdaki denetim izidir).

```jsonl
{"id":"rr-ext-tb-01","question":"14-TEZ-304230 belgesindeki yakıt vergileri tablosuna göre 'Light akaryakıt' için tüketim vergisi kaç birimdir?","ideal_answer":"Tüketim vergisi 102,60 (10^3 litre başına); ayrıca stratejik depolama vergisi 12,50.","category":"table_based","difficulty":2,"gold_evidence":[{"file_name":"14-TEZ-304230.pdf","page":61,"quote":"Light akaryakıt | 10 3 litre | 27.50 | 84.60 | 102.60 | 12.50"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"T04"}
{"id":"rr-ext-tb-02","question":"14-TEZ-304230 hafif yakıt tablosunda CO2 vergisi 1996 ve 2005 yıllarında sırasıyla kaçtır (euro/cent/l)?","ideal_answer":"1996'da 2,30; 2005'te 4,78.","category":"table_based","difficulty":2,"gold_evidence":[{"file_name":"14-TEZ-304230.pdf","page":50,"quote":"CO2 vergisi | - | - | 2.30 | 4.54 | 4.54 | 4.78"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"T16"}
{"id":"rr-ext-tb-03","question":"_021 belgesindeki politika-etki tablosunda Lehr et al. (2012) çalışması Almanya için yenilenebilir enerjinin istihdam etkisini nasıl raporluyor?","ideal_answer":"Pozitif; 2030 için 150.000 ilave istihdam.","category":"table_based","difficulty":2,"gold_evidence":[{"file_name":"_021.pdf","page":2,"quote":"Germany | Renewable energy | 2030 | Employment | Positive, 150,000 additional jobs"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"T15"}
{"id":"rr-ext-tb-04","question":"018 belgesindeki fiyat-değişim tablosunda Avustralya için ulaşım (benzin/dizel) sütunlarındaki değerler nedir?","ideal_answer":"Benzin (gasoline) 157, dizel 99.","category":"table_based","difficulty":3,"gold_evidence":[{"file_name":"018.pdf","page":37,"quote":"Australia | 0 | 0 | 79 | 6 | 24 | 96 | 157 | 99 | -54 | 68"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"T17"}
{"id":"rr-ext-tb-05","question":"_009 belgesindeki Senaryo 1 tablosunda 'Tarım, Orman ve Balıkçılık' sektörü için gerekli üretim değeri azalışı (mn. TL) nedir?","ideal_answer":"13.631 mn TL (birim üretim başına emisyon 0,00027 ton/TL; emisyon azaltım gereksinimi 3,67 mn ton).","category":"table_based","difficulty":2,"gold_evidence":[{"file_name":"_009.pdf","page":10,"quote":"Tarım , Orman ve Balıkçılık | 0.00027 | 3.67 | 13631"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"T19"}
{"id":"rr-ext-tb-06","question":"015 belgesindeki tabloda 'Grains crops' sektörünün üretimi 100 $/ton karbon vergisinde yüzde kaç değişir?","ideal_answer":"-%0,75 (10$: -0,07; 25$: -0,16; 50$: -0,35; 100$: -0,75).","category":"table_based","difficulty":2,"gold_evidence":[{"file_name":"015.pdf","page":10,"quote":"Grains crops | -0,07 | -0,16 | -0,35 | -0,75"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"T20"}
{"id":"rr-ext-tb-07","question":"_021 belgesindeki tabloda 'The current study' senaryosu 2030'da BAU'ya göre GSYİH ve sera gazı emisyonlarını nasıl değiştiriyor?","ideal_answer":"GSYİH +%1,0; sera gazı emisyonları -%9,1.","category":"table_based","difficulty":2,"gold_evidence":[{"file_name":"_021.pdf","page":10,"quote":"The current study | 1.0% | - 9.1% |"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"T21"}
{"id":"rr-ext-tb-08","question":"007 belgesindeki tabloda 'Solid fuels' sektörü için C2 senaryosunda GHG emisyonlarındaki yüzde değişim nedir?","ideal_answer":"-%18,92 (C1'de -%10,58).","category":"table_based","difficulty":3,"gold_evidence":[{"file_name":"007.pdf","page":26,"quote":"Solid fuels | -0.52 | -0.90 | -12.87 | -20.39 | -10.58 | -18.92"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"T24"}
{"id":"rr-ext-tb-09","question":"15-ET004233 emisyon tablosuna göre Çin'in 2020 yılı toplam içindeki payı yüzde kaçtır?","ideal_answer":"%30,65 (2020 emisyonu 10,67).","category":"table_based","difficulty":1,"gold_evidence":[{"file_name":"15-ET004233.pdf","page":96,"quote":"Çin | 2.48 | 3.44 | 8.62 | 10.67 | %30.65"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"T28"}
{"id":"rr-ext-tb-10","question":"12 belgesindeki tabloya göre Türkiye'nin toplam emisyonu 1990'da kaç milyon tondur ve 1993'te 1990'a göre yüzde kaç artmıştır?","ideal_answer":"1990'da 214 milyon ton; 1993'te 1990'a göre +%10,6 (236,7 milyon ton).","category":"table_based","difficulty":2,"gold_evidence":[{"file_name":"12.pdf","page":8,"quote":"1990 | 214 | | 1991 | 221,1 | 3,3 | 3,3 1992 | 227,4 | 6,3 | 2,8 1993 | 236,7 | 10,6 | 4,1"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"T38"}
{"id":"rr-ext-tb-11","question":"_021 belgesindeki dönüşüm tablosunda enerji verimliliği için 2030'da elektrik talebi BAU'da ve dönüşüm senaryosunda kaç TWh/yıl öngörülüyor?","ideal_answer":"BAU 462 TWh/yıl (elektrifikasyon dahil); dönüşüm ile 420 TWh/yıl (2021 seviyesi 331 TWh/yıl).","category":"table_based","difficulty":3,"gold_evidence":[{"file_name":"_021.pdf","page":4,"quote":"331 TWh/year electricity demand | 462 TWh/year electricity demand (including electrification) | 420 TWh/year"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"T23"}
{"id":"rr-ext-tb-12","question":"_008 belgesindeki varsayımlar tablosunda elektrik üretim sektöründe doğalgaz kullanımına dair varsayım nedir?","ideal_answer":"Doğalgaz kullanımı, arz kaynaklarına ilişkin belli kapasite ve fiyat kısıtları altında model seçimine bırakılmıştır.","category":"table_based","difficulty":2,"gold_evidence":[{"file_name":"_008.pdf","page":13,"quote":"Doğalgaz | Elektrik enerjisi üretimi amaçlı doğalgaz kullanımı arz kaynaklarına ilişkin belli kapasite ve fiyat kısıtları altında model seçimine bırakılmıştır"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"T35"}
{"id":"rr-ext-tb-13","question":"_021 belgesindeki dönüşüm tablosunda ısı pompaları (heat pumps) için 2030 katkısı ve toplam tüketim etkisi nedir?","ideal_answer":"2 milyon ısı pompası (BAU 0,5 milyon); baz duruma göre toplam 2,4 TWh/yıl ek tüketim.","category":"table_based","difficulty":2,"gold_evidence":[{"file_name":"_021.pdf","page":4,"quote":"2 million heat pumps | - | 0.5 million heat pumps | Total consumption of 2.4 TWh/year over the baseline"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"T33"}
{"id":"rr-ext-tb-14","question":"012 belgesindeki karbon fiyatlandırma tablosunda Arjantin hangi yıl ve hangi araçla (karbon vergisi/ETS) listelenmiştir?","ideal_answer":"Arjantin: 2018, Karbon Vergisi (KV).","category":"table_based","difficulty":1,"gold_evidence":[{"file_name":"012.pdf","page":7,"quote":"Arjantin | KV | 2018 | Var | Var"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"T26"}
{"id":"rr-ext-tb-15","question":"012 belgesine göre karbon fiyatlandırmasının enerji üretiminde hedeflediği geçiş nedir?","ideal_answer":"Kömürden doğal gaza ve yenilenebilir kaynaklara, oradan da karbon yakalama-depolama teknolojileri ile nükleer enerjiye geçiş.","category":"table_based","difficulty":2,"gold_evidence":[{"file_name":"012.pdf","page":5,"quote":"Enerji üretimi: kömürden, doğal gaza ve yenilenebilir kaynaklara ve oradan da belki de karbon yakalama ve depolama teknolojileri ile nükleer enerjiye geçiş"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"T37"}
{"id":"rr-ext-tb-16","question":"02-KARBON-VERGISI belgesindeki tabloda Danimarka'nın karbon/enerji vergisi oranı hangi aralıkta verilmiştir?","ideal_answer":"27-55 (î/vergi); endüstriye daha düşük oran ve ödemeler.","category":"table_based","difficulty":1,"gold_evidence":[{"file_name":"02-KARBON-VERGISI-1890343.pdf","page":15,"quote":"Danimarka | 27-55 | Dahadüşük oran ve ödemeler"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"T36"}
```

---

## B) SYNTHESIS taslakları (15) — yükleme-hazır

Sayfalar konteynerde çözüldü (44 üye chunk). Her soru **≥2 üye chunk'ı birleştirir**
(gerçek sentez: tek chunk cevaplamaz); her evidence ilgili chunk'ın doğrulanmış sayfasına
bağlı, alıntılar `result.txt` önizlemelerinden birebir. S04 (02-KARBON "n. KARBON VERGİSİ)
zayıf/dağınık olduğu için ELENDİ; 10 güçlü bölümden 5'i ikinci bir (farklı chunk-çiftli) soru
verdi → 15.

| # | id | soru (kısalt.) | zorluk | dosya · s. | chunk çifti |
|---|----|----------------|--------|-----------|-------------|
| 1 | rr-ext-sy-01 | Yakıt karbon sıralaması + C1/C2 GSYİH | 3 | 007.pdf · 23,24 | 64947+64949 (S16) |
| 2 | rr-ext-sy-02 | Baz senaryo elektrik + talep daralması | 3 | 007.pdf · 23,24 | 64946+64948 (S16) |
| 3 | rr-ext-sy-03 | CGE değeri + Boratav/Türel/Yeldan modeli | 2 | 007.pdf · 8 | 64923+64924 (S18) |
| 4 | rr-ext-sy-04 | TurkStat 7-sektör IO + GTAP EU işgücü | 3 | 007.pdf · 9,10 | 64925+64926 (S18) |
| 5 | rr-ext-sy-05 | KV tercihi + prim alternatifi | 2 | 012.pdf · 2 | 65018+65019 (S19) |
| 6 | rr-ext-sy-06 | Küresel işbirliği + Finlandiya ilk KV | 2 | 15-ET004233.pdf · 108,109 | 65636+65637 (S20) |
| 7 | rr-ext-sy-07 | TR-Power BU↔TD CGE + 2017-19 kapasite | 3 | _021.pdf · 3 | 66028+66029 (S22) |
| 8 | rr-ext-sy-08 | CPAT IMF-WB 200 ülke + ulaşım talebi | 2 | 018.pdf · 39,40 | 65172+65174 (S23) |
| 9 | rr-ext-sy-09 | Yenilenebilir yatırım + fosil fiyatları | 3 | 018.pdf · 39,40 | 65173+65175 (S23) |
| 10 | rr-ext-sy-10 | Enerji/dayanıklı talep + kömür karbonsuz. | 2 | 0017.pdf · 4 | 64811+64812 (S29) |
| 11 | rr-ext-sy-11 | 136 ülke %88 net-sıfır + fiyat etkisi | 3 | 018.pdf · 6,8 | 65114+65117 (S30) |
| 12 | rr-ext-sy-12 | Devrilme noktaları + spreadsheet aracı | 3 | 018.pdf · 6,7 | 65115+65116 (S30) |
| 13 | rr-ext-sy-13 | CCDR notu amacı + makro odak | 2 | 014.pdf · 1,2 | 65060+65061 (S15) |
| 14 | rr-ext-sy-14 | KV tanımı + avantaj | 2 | 004.docx · 1 | 64879+64880 (S24) |
| 15 | rr-ext-sy-15 | GHG azaltımı + karbon fiyatı gelir | 2 | 012.pdf · 2 | 65016+65017 (S19) |

### Yükleme bloğu (onay SONRASI — JSONL, rr-ext-v1)

```jsonl
{"id":"rr-ext-sy-01","question":"007 belgesinin karbon vergisi senaryolarında yakıtların karbon içeriği nasıl sıralanır ve C1/C2 senaryolarında baz senaryoya göre GSYİH büyüme oranı nedir?","ideal_answer":"Kömür en yüksek karbon içerikli (en yüksek vergi); onu petrol (260 kgCO2/MWh) ve doğalgaz (201 kgCO2/MWh) izler. GSYİH büyüme oranı C1'de %0,16, C2'de %0,28'dir.","category":"synthesis","difficulty":3,"gold_evidence":[{"file_name":"007.pdf","page":23,"quote":"oil (260 kgCO2/MWh) and natural gas (201 kgCO2/MWh). Coal is the most polluting fossil fuel and has the highest carbon content"},{"file_name":"007.pdf","page":24,"quote":"GDP growth rate is 0.16% in C1 and 0.28% in C2 compared to the baseline scenario"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"S16-a"}
{"id":"rr-ext-sy-02","question":"007 belgesinin karbon vergisi senaryolarında baz senaryoda elektrik üretimi neyden oluşur ve karbon vergisinin ekonomideki toplam talebe kısa vadeli etkisi nedir?","ideal_answer":"Baz senaryoda elektrik üretimi fosil yakıtlar ve diğer üretim teknolojilerinin bir karışımıdır; karbon vergisi ekonomideki toplam talebi daraltır.","category":"synthesis","difficulty":3,"gold_evidence":[{"file_name":"007.pdf","page":23,"quote":"In the baseline scenario, electricity production is a mix of fossil fuels and other power generation technologies"},{"file_name":"007.pdf","page":24,"quote":"This means that overall demand in the economy would shrink"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"S16-b"}
{"id":"rr-ext-sy-03","question":"007 belgesinin Türkiye çalışmaları derlemesine göre CGE modellerinin politika analizindeki değeri nedir ve Boratav, Türel ve Yeldan (1996) çalışması hangi model ve veriyi kullanır?","ideal_answer":"CGE modelleri, farklı politikalar arasındaki bağlantı ve ödünleşimleri değerlendiren tutarlı çerçeveler sunduğu için politika yapımında çok yararlıdır. Boratav, Türel ve Yeldan (1996) kesikli-dinamik bir CGE modeli ve OECD istatistiklerini kullanır.","category":"synthesis","difficulty":2,"gold_evidence":[{"file_name":"007.pdf","page":8,"quote":"CGE models have proven to be very useful for policy-making purposes as they provide consistent frameworks"},{"file_name":"007.pdf","page":8,"quote":"the study by Boratav, Turel and Yeldan (1996). They use a discretedynamic CGE model and statistics from the OECD"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"S18-a"}
{"id":"rr-ext-sy-04","question":"007 belgesindeki modelde Türk ekonomisinin girdi-çıktı çekirdeği kaç sektörle ve hangi baz yılıyla kalibre edilir; GTAP modeli hangi senaryo için nasıl uyarlanır?","ideal_answer":"Model, TurkStat girdi-çıktı tablosunun yedi sektörlü çekirdeğini kullanır ve 2003 baz yılı verisiyle kalibre edilir. GTAP modeli faktör hareketliliğiyle tutarlı olacak şekilde uyarlanır; ilk senaryo Türkiye ile AB arasındaki işgücü hareketliliğine bakar.","category":"synthesis","difficulty":3,"gold_evidence":[{"file_name":"007.pdf","page":9,"quote":"TurkStat, which uses seven sectors in the input-output core of the Turkish economy. The model is calibrated to 2003 base-year data"},{"file_name":"007.pdf","page":10,"quote":"The authors use the readily available GTAP model and modify it to make it consistent with factor mobility. The first scenario looks at the labor mobility between Turkey and the EU"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"S18-b"}
{"id":"rr-ext-sy-05","question":"012 belgesinin genişletilmiş özetine göre Türkiye için neden karbon vergisi daha uygun bulunuyor ve sosyal kabul sorununa önerilen alternatif çözüm nedir?","ideal_answer":"Karbon vergisi Türkiye için daha uygun bir seçenektir ve diğer fosil yakıtları da kapsayacak biçimde uygulanması kolaydır. Sosyal kabul sorununa karşı önerilen alternatif, enerji fiyatlarını artırmadan emisyonu azaltacak biçimde enerji vergisi sistemine primler eklemektir.","category":"synthesis","difficulty":2,"gold_evidence":[{"file_name":"012.pdf","page":2,"quote":"Acarbon tax is a more suitable option for Türkiye. A carbon tax that would also cover other fossil fuels"},{"file_name":"012.pdf","page":2,"quote":"another alternative solution is to include bonuses in the energy tax system that will reduce emissions rates without increasing energy prices"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"S19-a"}
{"id":"rr-ext-sy-06","question":"15-ET004233 sonucuna göre küresel işbirliği yetersizliği iklim sorununu nasıl etkiliyor ve karbon vergisi ilk olarak hangi ülkede, nasıl uygulanmıştır?","ideal_answer":"Küresel işbirliği yetersizlikleri nedeniyle karbon emisyonu ve küresel ısınma sorunu azaltılamamaktadır. Karbon vergisi ilk olarak Finlandiya'da kömür ve petrol gibi fosil yakıtlar üzerinden kalorifik değerlerine bağlı spesifik biçimde alınmış ve geliri genel bütçeye aktarılmıştır.","category":"synthesis","difficulty":2,"gold_evidence":[{"file_name":"15-ET004233.pdf","page":108,"quote":"küresel işbirliği yetersizlikleri nedeniyle karbon emisyonu ve küresel ısınma sorunu"},{"file_name":"15-ET004233.pdf","page":109,"quote":"Karbon vergisi ilk olarak Finlandiya'da kömür, petrol gibi fosil yakıtlar üzerinde kalorifik değerlerine bağlı olarak spesifik bir şekilde alınmış ve geliri genel bütçeye aktarılmıştır"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"S20"}
{"id":"rr-ext-sy-07","question":"_021 belgesinde güç sistemi ve CGE modelleri nasıl birbirine bağlanır ve elektrik santrali kapasite faktörleri hangi veriyle tahmin edilir?","ideal_answer":"Aşağıdan-yukarı (BU) güç sistemi modeli TR-Power (Kat, 2021), ayrıntılı yukarıdan-aşağı (TD) bir CGE modeli ile yumuşak bağlanır (soft-linked). Elektrik santrali kapasite faktörleri 2017-2019 saatlik verisiyle tahmin edilir.","category":"synthesis","difficulty":3,"gold_evidence":[{"file_name":"_021.pdf","page":3,"quote":"a bottom-up (BU) power system model, TR-Power ( Kat, 2021 ), is soft-linked with a detailed top-down (TD) CGE model"},{"file_name":"_021.pdf","page":3,"quote":"Hourly data for 2017-2019 are used to estimate power plant capacity factors"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"S22"}
{"id":"rr-ext-sy-08","question":"018 belgesinin Ek A'sına göre CPAT nedir, kim geliştirmiştir ve kaç ülkeyi kapsar; daha yüksek fiyatlar ulaşımda yakıt tüketimini nasıl etkiler?","ideal_answer":"CPAT, IMF ve Dünya Bankası tarafından ortak geliştirilen, 200'den fazla ülkeyi kapsayan bir iklim azaltım politikası modelleme platformudur. Daha yüksek fiyatlar karşısında benzinli ve dizel araçların yakıt tüketimi düşer; bireyler daha verimli araçlara geçer ve araç-km'sini azaltır.","category":"synthesis","difficulty":2,"gold_evidence":[{"file_name":"018.pdf","page":39,"quote":"CPAT is a climate mitigation policy modelling platform developed jointly by the IMF and World Bank. Covering over 200 countries"},{"file_name":"018.pdf","page":40,"quote":"fuel consumption from gasoline and diesel vehicles declines in response to higher prices as individuals switch to more fuel-efficient vehicles and reduce vehicle miles travelled"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"S23-a"}
{"id":"rr-ext-sy-09","question":"018 belgesinin Ek A'sındaki CPAT modelinde yatırım nasıl yeniden yönlendirilir ve uluslararası fiyatlar hangi yakıtlar için alınır?","ideal_answer":"Yatırım, yenilenebilir kaynaklardaki yıllık ölçek-artışının azami sınırı gibi kısıtlar altında yenilenebilirlere kaydırılır; ayrıca kömür santrallerinin emekliye ayrılması hızlandırılır. Uluslararası fiyatlar kömür, petrol ve doğalgaz için alınır.","category":"synthesis","difficulty":3,"gold_evidence":[{"file_name":"018.pdf","page":39,"quote":"investment is shifted to renewables (subject to constraints, notably a maximum increase in annual scale-up of renewables). Additionally, they also accelerate retirement of coal plants"},{"file_name":"018.pdf","page":40,"quote":"International prices for coal, oil, and natural gas"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"S23-b"}
{"id":"rr-ext-sy-10","question":"0017 belgesinin girişine göre orta gelirli ülkeler büyürken hangi taleplerde artış görülür ve Türkiye'nin ulusal stratejisindeki kilit hedef nedir?","ideal_answer":"Orta gelirli ülkeler büyürken enerji ve dayanıklı tüketim mallarına talep artar. Türkiye'nin ulusal stratejisindeki kilit hedef, kömür bazlı elektriğin karbonsuzlaştırılmasıdır.","category":"synthesis","difficulty":2,"gold_evidence":[{"file_name":"0017.pdf","page":4,"quote":"As middle income countries grow they see an increase in demand for energy and consumer durables"},{"file_name":"0017.pdf","page":4,"quote":"Decarbonising coal based electricity is a key goal of national strategy"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"S29"}
{"id":"rr-ext-sy-11","question":"018 belgesine göre küresel hırs açığı ne büyüklüktedir (kaç ülke, sera gazının yüzde kaçı) ve enerji fiyatlarının karbonsuzlaşmaya etkisi neden sınırlıdır?","ideal_answer":"136 ülke (küresel sera gazının %88'i) net-sıfır hedefi önermiş ya da belirlemiştir. Enerji fiyatlarının karbonsuzlaşmaya etkisi sınırlıdır çünkü gaz fiyatlarındaki görece artış kömüre geçişe yol açmış ve fiyat değişimleri geri döndürülebilir görülmüştür.","category":"synthesis","difficulty":3,"gold_evidence":[{"file_name":"018.pdf","page":6,"quote":"136 countries, representing 88 percent of global GHGs, have proposed, or set, net zero"},{"file_name":"018.pdf","page":8,"quote":"the relative increase in gas prices has caused switching to coal; price changes are seen as reversable"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"S30-a"}
{"id":"rr-ext-sy-12","question":"018 belgesine göre gezegen ısındıkça iklim etkileri ve devrilme noktaları riski nasıl değişir ve makalenin analizleri hangi araca dayanır?","ideal_answer":"Gezegen ısındıkça bu etkilerin sıklığı ve şiddeti artar ve küresel iklim sistemindeki devrilme noktaları riski yükselir. Makalenin analizleri, geniş enerji modelleme yazınının orta aralığına yaklaşık olarak parametrelendirilmiş bir spreadsheet (hesap tablosu) aracına dayanır.","category":"synthesis","difficulty":3,"gold_evidence":[{"file_name":"018.pdf","page":6,"quote":"the frequency and severity of these impacts will rise as the planet heats up. Moreover, the risks of tipping points in the global climate system"},{"file_name":"018.pdf","page":7,"quote":"This paper presents analyses based on a spreadsheet tool that is approximately parameterized to the mid-range of the broader energy modelling literature"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"S30-b"}
{"id":"rr-ext-sy-13","question":"014 belgesinin girişine göre bu arka plan notunun amacı nedir ve makroekonomik analizlerin odağı nedir?","ideal_answer":"Not, Türkiye İklim ve Kalkınma Raporu (CCDR) için makroekonomik modelleme yaklaşımı ve sonuçlarını ayrıntılandırmayı amaçlar. Makroekonomik analizlerin odağı, azaltım politikalarının yol açabileceği ekonomik ödünleşimlere dikkat çekmektir.","category":"synthesis","difficulty":2,"gold_evidence":[{"file_name":"014.pdf","page":1,"quote":"This background note aims to provide details on the macroeconomic modelling approach and results for the Turkey Country Climate and Development (CCDR) report"},{"file_name":"014.pdf","page":2,"quote":"The focus of the macroeconomic analyses is to draw attention to the economic trade-offs that mitigation policies could entail"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"S15"}
{"id":"rr-ext-sy-14","question":"004 belgesine göre karbon vergisi nasıl tanımlanır ve sağladığı avantajlardan biri nedir?","ideal_answer":"Karbon vergisi, karbon bazlı yakıtların (kömür, petrol, doğalgaz) yakılmasına konan bir harçtır. Avantajlarından biri sürdürülebilir uygulamaların teşvik edilmesidir.","category":"synthesis","difficulty":2,"gold_evidence":[{"file_name":"004.docx","page":1,"quote":"A carbon tax is a fee imposed on the burning of carbon-based fuels"},{"file_name":"004.docx","page":1,"quote":"Advantages of Carbon Tax Encouragement of Sustainable Practices"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"S24"}
{"id":"rr-ext-sy-15","question":"012 belgesinin genişletilmiş özetine göre sera gazı emisyonlarını azaltmak neden gereklidir ve karbon fiyatlandırmasının ülkeler için sağladığı ek işlev nedir?","ideal_answer":"Sera gazı emisyonlarını azaltmak, küresel ısınmayı normal düzeylerde tutmak için gereklidir. Karbon fiyatlandırması ayrıca ülkeler için önemli bir gelir kaynağı olmayı sürdürür.","category":"synthesis","difficulty":2,"gold_evidence":[{"file_name":"012.pdf","page":2,"quote":"Reducing greenhouse gas emissions is essential in order to keep global warming at normal levels"},{"file_name":"012.pdf","page":2,"quote":"Carbon pricing continues to be an important source of income that countries"}],"doc_scope":"default","answerable":true,"created_by":"sonnet-taslak-M11","notes":"S19-b"}
```

**Toplam rr-ext-v1 taslağı: 16 table_based + 15 synthesis = 31 kayıt.** Her ikisi de n=15-20
hedefini karşılar.

---

## Karar kapısı hatırlatması

Onay (§1c) → yükle `rr-ext-v1` → `rerank_ab_probe.py --golden rr-ext-v1 --json` →
kategori-bölünmüş A/B. table_based +0,400 / synthesis +0,143 **ÖLÇEKTE korunursa** Adım-2
gating GREENLIGHT; **erirse** #11 kapanır (asimetrik maliyet: emin değilsen rerank AÇMA).

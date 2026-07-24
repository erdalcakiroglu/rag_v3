# Brief — M-17: honesty ölçütünün düzeltilmesi

**Kime:** Sonnet 4.8 (uygulayıcı)
**Kimden:** Mimari
**Sıra:** Backlog #1 — bunu bitirmeden diğerlerine geçmiyoruz.
**İlke:** Bu bir *ölçüt* (cetvel) işi, davranış işi değil. Cetveli keskinleştiriyoruz; sonra
diğer değişiklikleri onunla ölçeceğiz. Bu yüzden ilk sırada.

---

## 0. Amaç (tek cümle)

Honesty metriğinin bazı golden kayıtları **yanlış sınıfladığından** şüpheleniyoruz
(v4 karnesi ek kanıt üretti). Şüpheli düzeltme: **`fabricated` yalnız cevabın fiilen iddia
ürettiği durumda (`len(sources) > 0`) sayılsın** — saf ret (kaynaksız) fabrication olamaz.
Ama bunu **körlemesine uygulama.** Önce ön-veri.

---

## 1. ÖNCE ÖN-VERİ — hiçbir şey değiştirmeden (bu bölüm çıktı üretir, kod değil)

Bu projede tekrar tekrar gördük: ön-veri hipotezi çürütüyor. Aynı disiplin burada da:

1. **Mevcut tanım nerede, nasıl?** `honesty` / `fabricated` tam olarak hangi dosya:satırda,
   hangi mantıkla hesaplanıyor? Kod parçasını aynen ver. (Örn. cevabı ground-truth ile mi
   kıyaslıyor, ret'i nasıl tanıyor, kaynak sayısını kullanıyor mu?)
2. **Hangi kayıtlar yanlış sınıflanıyor?** v4 karnesinin ürettiği kanıtla: her yanlış-sınıf
   golden kaydı için tek satır — *kayıt id · beklenen · şu an ne diyor · neden yanlış.*
3. **Şüpheli düzeltme bu kayıtları DÜZELTİR Mİ?** `fabricated = len(sources) > 0` (veya nihai
   biçimi) her bir yanlış-sınıf kayda uygulandığında sonuç ne olur? Kayıt-kayıt tek satır.
4. **Ters yönde kırdığı bir şey var mı?** Yeni tanım, şu an DOĞRU sınıflanan bir kaydı yanlışa
   çevirir mi? Özellikle: gerçekten uydurma bir cevabı "honest" saymaya başlar mı? (En kritik risk.)

**Bu 4 maddeyi rapor et, sonra DUR.** Kararı birlikte veririz; tanımın nihai biçimini ön-veri
belirler, ben değil.

---

## 2. Entailment ON bağımlılığı (karar noktası)

Yeni honesty tanımı "kaynak var → iddia meşru" varsayımına yaslanıyor. Ama **kaynağın iddiayı
gerçekten desteklediğini** yalnız entailment doğrular. Dolayısıyla:

- Yeni/gevşetilmiş tanım **yalnız entailment ON iken mi** geçerli olmalı?
- Entailment OFF iken davranış ne olmalı — eski (katı) tanıma mı düşmeli?
- Ön-veride bunu da yanıtla: iki mod için honesty tanımı ayrışıyor mu, ayrışmalı mı?

Gerekçe: entailment olmadan "kaynaklı ama kaynak desteklemiyor" (hayali-atıf) durumunu
ayırt edemeyiz; o durumda `len(sources)>0`'a güvenmek fabrication'ı maskeler.

---

## 3. Karar SONRASI — uygula + yeniden ölç

Ön-veri onaylanınca:

- Tanımı **tek yerde** değiştir (ölçüt tek kaynak; kopyala-yapıştır ölçüt olmasın).
- Değişikliği **test ile kilitle**: yanlış-sınıf kayıtların artık doğru sınıflandığını, doğru
  olanların bozulmadığını doğrulayan birim test(ler).
- Honesty'yi **yeniden ölç** ve eski/yeni karne farkını raporla. **Tanımsal kayma beklenir** —
  eski honesty sayıları yeni cetvelle değişecek; bu bir gerileme değil, cetvel değişimi. Raporda
  açıkça "Δ tanımdan, davranıştan değil" diye ayır.

---

## 4. Kabul kriterleri

- [ ] Mevcut tanım kod parçasıyla belgelendi (dosya:satır).
- [ ] Yanlış-sınıf kayıtlar tek tek listelendi; yeni tanımla düzeldiği kayıt-kayıt gösterildi.
- [ ] Yeni tanım DOĞRU sınıfları bozmuyor — özellikle **hiçbir gerçek fabrication "honest" sayılmıyor.**
- [ ] Entailment ON/OFF için honesty davranışı netleşti ve belgelendi.
- [ ] Ölçüt tek yerde; birim test yeni davranışı kilitliyor.
- [ ] Honesty yeniden ölçüldü; eski/yeni Δ raporlandı ve "tanımsal kayma" olarak etiketlendi.
- [ ] Değişiklik **yalnız eval/ölçüt katmanında** — agent davranışına (`compose`, tool-çağrı,
      prompt) DOKUNMUYOR. Diff bunu göstermeli.

---

## 5. Kapsam çiti

Bu iş **ölçüt katmanıyla sınırlı.** Agent'ın ne ürettiğini değiştirmiyoruz — yalnız ürettiğini
nasıl *yargıladığımızı* düzeltiyoruz. `compose` (M-16 mührü), tool-çağrı yolu, prompt'lar bu
brief'in DIŞINDA. Oralarda diff çıkarsa dur ve sor.

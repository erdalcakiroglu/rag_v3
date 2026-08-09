# Brief — M-17b: `declined` bacağı marker açığı

**Kime:** Sonnet 4.8 · **Kimden:** Mimari
**Sıra:** M-17'nin doğrudan devamı; grammar'dan ÖNCE.
**İlke:** Yine *ölçüt* işi, davranış değil. Ve D0 ile **aynı sınıf hata** — biçim ölçüp öz kaçırmak.

---

## 0. Sorun

`_NOTFOUND_MARKERS` reddi metindeki kelimelerden tanıyor; "rastlanmamıştır / yer verilmemiştir /
yoktur" biçimlerini kaçırıyor. Model aynı reddi bu kelimelerle yazınca `declined=False` →
honest sayılmıyor → **aynı davranış, farklı sınıf.** Gate tam 0.80 HARD sınırında olduğundan bu
açığı kapatmak tavanı hak edilmiş şekilde yükseltebilecek tek kaldıraç.

---

## 1. ÖNCE ÖN-VERİ — kod yok (M-17 disiplini)

1. **Kaç satır etkileniyor?** Golden sette (ve varsa canlı loglarda) kaç `declined` reddi bu
   kaçırılan biçimleri (`rastlanmamıştır`, `yer verilmemiştir`, `yoktur` ve akrabaları) kullanıyor?
   Satır-satır listele: id · reddin metni · şu an `declined` ne diyor · doğrusu ne.
2. **Tam biçim envanteri.** Yalnız üç kelime değil — modelin fiilen ürettiği ret kalıplarının
   tam listesini çıkar (çekimler dahil: -mamıştır/-memiştir, "bulunmamaktadır", "geçmemektedir"...).
   Marker listesini kör genişletmek yerine gerçek dağılımı gör.
3. **KÖK SORU — yapısal sinyal var mı?** M-17'nin dersi: metin regex'i değil, yapısal alanı ölç.
   Ajan reddi verirken **yapısal bir `declined`/notfound bayrağı** set ediyor mu (submit_answer
   yolu, bir karar düğümü, bir state alanı)? Varsa: `_NOTFOUND_MARKERS`'ı büsbütün bırakıp o
   bayrağı okumak **bu hata sınıfını kökten siler** (marker listesi bir daha asla eksik kalmaz).
   Yoksa: marker genişletme pragmatik yama olur; o zaman "marker listesi kırılgan" borcunu not düş.

**Bu üçünü rapor et, DUR.** Yama mı (marker ekle) yoksa kök-fix mi (yapısal bayrağı oku) —
kararı ön-veri belirler.

---

## 2. Karar sonrası — uygula + yeniden ölç

- Kök-fix mümkünse tercih et (yapısal bayrak). Değilse marker listesini gerçek envantere göre
  genişlet + regex'i çekimlere dayanıklı yaz.
- **Test kilidi:** kaçırılan her biçim için birim test — bu ret kalıpları artık `declined=True`.
- Honesty'yi yeniden ölç. Bu sefer Δ **iki kaynaklı olabilir**: (a) yanlış sınıflananlar düzeldi
  (tanımsal), (b) tavan yükseldi. Raporda ayır. Yeni tavanı ve gate marjını açıkça yaz
  (0.80'e ne kadar pay kaldı).

---

## 3. Kabul kriterleri

- [ ] Etkilenen satırlar + tam ret-biçim envanteri belgelendi.
- [ ] Yapısal `declined` sinyalinin var olup olmadığı yanıtlandı; varsa marker regex'i yerine o kullanıldı.
- [ ] Kaçırılan biçimler artık `declined=True`; birim testlerle kilitli.
- [ ] Honesty yeniden ölçüldü; Δ (tanımsal düzeltme vs tavan yükselişi) ayrıştırıldı; yeni gate marjı raporlandı.
- [ ] Değişiklik **yalnız ölçüt/eval katmanında** — `compose`, tool-çağrı, prompt DOKUNULMADI (diff kanıtı).
- [ ] Gate'i DÜŞÜRMEDİK — tavanı yükselttik. (`gates.py` eşiği 0.80 HARD kalır.)

---

## 4. Kapsam çiti

Ölçüt katmanı. Ajanın ne ürettiğini değiştirmiyoruz; reddi nasıl *tanıdığımızı* düzeltiyoruz.
Model daha iyi/farklı reddetsin diye prompt'a dokunmak bu brief'in DIŞINDA — o davranış işi,
ayrı karar. Burada yalnız var olan reddi doğru sınıflıyoruz.

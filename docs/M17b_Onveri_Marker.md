# M-17b ön-veri — `declined` marker açığı: KARAR DEĞİŞTİ

**Tarih:** 2026-07-25 · **Statü:** ön-veri tamam, **kod YOK** (brief §1 "DUR")
**Sonuç:** Önerilen düzeltme (marker listesini genişletmek) **reddedildi** — mevcut kanıtta net
zarar veriyor. Gerçek bir açık varsa yapısal çözülmeli, marker genişletmeyle değil.

---

## 0. Ne arandı

Brief §0 hipotezi: `_NOTFOUND_MARKERS` "rastlanmamıştır / yer verilmemiştir / yoktur"
biçimlerini kaçırıyor → model bu kelimelerle reddedince `declined=False` → honest sayılmıyor
→ gate 0.80 sınırında tavanı yiyor.

Test: erişilebilir tüm eval koşumlarında (`var/eval/*.json` — ds_dry, v0_run, dilim3, v2, v2b, v3)
her cevabı tarayıp iki kümeye ayırdım.

## 1. Bulgu — iki küme

**A) Gerçek bug (unanswerable + kaçan-biçim + şu an declined DEĞİL): BOŞ.**
Kaçan biçimi kullanan her unanswerable ret, zaten yakalanıyor — ya `confidence=="low"` (yapısal),
ya da yanında bir *kapsanan* marker ("bulunm…", "güvenilir yanıt üretilemedi") ile:

| id | run | conf | kaçan biçim | neden zaten yakalanıyor |
|---|---|---|---|---|
| gs-v0-032 | ds_dry | high | "yer verilmemiş" | +kapsanan "bulunm…" |
| gs-v0-032 | v0_run | low | "güvenilir yanıt üretilemedi" | conf=low **ve** kapsanan marker |
| gs-v0-036 | v0_run | medium | "içermemekte" | +kapsanan "bulunmamaktadır" |

Yani gözlemlenen veride **kaçan tek bir gerçek ret yok.** Kapatılacak açık yok → tavan yükselmez.

**B) Yanlış-pozitif riski (kaçan-biçim ANSWERABLE gerçek cevap içinde): GERÇEK.**
Önerilen tam biçimler ("yer verilmemiş", "yoktur") sağlam cevapların içinde geçiyor:

- **gs-v0-023** (answerable, conf=high, **6 kaynak**): İsveç karbon vergisini doğru, atıflı anlatan
  cevap. İçinde: *"…doğrudan bir karbon vergisi uygulamasına **yer verilmemiş** olup…"* — bir yan
  cümle. Bu biçimi marker'a eklersem cevap `declined=True`'ya döner → answerable olduğu için
  **yanlış fallback** → faithfulness/context_precision yapısal olarak bozulur.
- gs-v0-002 (×4, answerable): "…karbon vergisi **bulunmamaktadır**…" — doğru cevabın kendisi
  ("Türkiye'de yok") kapsanan bir markerla çakışıyor; ayrı bir `is_fallback` yanlış-pozitifi
  (bu düzeltmenin konusu değil ama marker-tabanlı ölçümün kırılganlığının ikinci kanıtı).

## 2. Yorum — M-17 dersi birebir tekrar ediyor

Marker *biçim* ölçer. "yer verilmemiş / yoktur" gibi ifadeler **hem ret hem de içinde çekince
barındıran gerçek cevapta** geçer; string eşleşmesi ikisini ayıramaz. Ayıran şey zaten yapısal
sinyaller: `confidence` ve `coverage` — nitekim A kümesinin boş olmasının sebebi de bu
(retler ya conf=low ya kapsanan-marker ile zaten ayrışıyor). Marker bacağını genişletmek,
**gözlemde var olmayan** bir yanlış-negatifi, **gözlemde var olan** bir yanlış-pozitifle takas eder.

## 3. Karar

1. **Marker listesini genişletme.** Özellikle "yer verilmemiş" ve "yoktur" reddedildi (gs-023).
2. **Tavan sorunu, erişilebilir veride marker açığından KAYNAKLANMIYOR** — A kümesi boş.
3. **Karar, göremediğim v4/m17_d4 koşumuna bağlı.** Bu depodaki koşumlar M-17 öncesi (9 Tem).
   Sonnet'in öneriyi doğurduğu v4 karnesindeki **sızan satırların** ham verisi gerekli:
   *id · answer metni · confidence · sources · coverage.* Karar yalnız o satırlarla verilir.
4. **Gerçek bir açık çıkarsa bile fix yapısal olmalı:** `compose.is_declined` / `coverage`
   sinyalini okumak; ya da marker eklenecekse yalnız **gerçek cevaplarda hiç geçmediği kanıtlanmış**
   biçimler (korpus taraması). "yer verilmemiş"/"yoktur" bu testten geçemez.

## 4. v4/m17_d4 DOĞRULAMASI — karar mühürlendi

Sonnet'in konteynerdeki motive edici koşumu (`var/eval/m17_d4_honesty.json`, 15 satır, temp=0)
alındı. Kendi script'inin özeti:

```
GERCEK sizinti (unanswerable + hic declined DEGIL): 0  — BOS
LATENT  sizinti (yalniz conf=low; metinde kacan-bicim): 0  — BOS
```

15/15 satırın hepsi `declined=True`. "yoktur" adayı **yalnız gs-034'te** geçiyor — ve gs-034
zaten *kapsanan* marker ("bulunmamaktadır") ile declined. Yani "yoktur"u eklemek gs-034'te
**hiçbir şeyi değiştirmez.** İki bağımsız ön-veri (bu deponun 9 Tem koşumları + v4) örtüşüyor:
**marker açığı bug'ı yok, 0 sızıntı.**

### Tavan neden 12/15 — gerçek kaldıraç marker DEĞİL, grounding

3 fail'in hepsi **gs-034 (×3)**. gs-034 declined ✓ ama `coverage=0.75<1.0` → D4 `declined_uncovered`
(reddederken bağlanmamış bir iddia bıraktı: "…2022-2024 OVP'de yer aldı…" gibi ek bağlam
cümlesi tam bağlanmamış). Bu **haklı** bir fail — tespit açığı değil, grounding açığı.

**Sonuç:** Tavanı hak edilmiş şekilde yükseltmenin yolu marker değil, gs-034'ün davranışı:
ya temiz reddetsin (ek iddia eklemesin) ya da eklediği bağlamı tam grounding'lesin. Bu
`compose`/prompt bölgesidir — **M-16 mührü** — ayrı ve dikkatli bir karar. Gate 0.80'e dürüstçe
oturuyoruz; ne düşürüyoruz ne de marker'la sahte yükseltiyoruz.

### Karar (mühür)

`_NOTFOUND_MARKERS` **değişmiyor.** M-17b kapandı: aksiyon yok — öneri ön-veriyle çürütüldü,
motive eden v4 koşumunda doğrulandı. Marker genişletme reddedildi (sıfır fayda + gs-023 yanlış-poz
riski). Gerçek kaldıraç (gs-034 grounding) ayrı kalem olarak not düşüldü.

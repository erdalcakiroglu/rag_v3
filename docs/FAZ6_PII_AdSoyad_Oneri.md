# FAZ 6 P2 — AD-SOYAD PII Yakalama: Öneri Raporu (kod yazılmadı)

**Tarih:** 2026-07-09 · **Karar bekliyor.** TCKN + tarih deterministik olarak maskelendi
([guardrails/pii.py](../ragintel/guardrails/pii.py)); AD-SOYAD bu katmana **ALINMADI** — nedeni
ve seçenekler aşağıda.

## Neden zor?
Ad-soyad, TCKN/tarih gibi **deterministik bir desene** sahip değil: "Mehmet Yılmaz" iki büyük
harfli kelime ama "Karbon Vergisi", "Avrupa Birliği", "Orta Vadeli" de öyle. Güvenilir yakalama
**bağlam anlayışı / NER** gerektirir. Yanlış-pozitif (kurum/başlık/yer adlarını maskeleme)
yanıtı bozar; yanlış-negatif (kaçan isim) KVKK riski. Deterministik regex tek başına yetersiz.

## Değerlendirilen seçenekler

| # | Seçenek | Artı | Eksi |
|---|---|---|---|
| 1 | **Bağlam deseni** (regex): "Sayın X Y", "Ad Soyad:", "X Y adlı/isimli" | ucuz, deterministik, izole | düşük recall (yalnız kalıplı isim), yer/kurum FP'si |
| 2 | **Hafif NER** (spaCy-tr / HF Türkçe NER) | iyi recall, PER etiketi | **ağır bağımlılık** (torch/transformers) — proje politikasına aykırı (tiktoken yasak, torch yok), latency, model bakımı |
| 3 | **Gazetteer** (Türkçe ad listesi) | deterministik | dev liste, "Deniz/Barış/Güneş" gibi ad=sözcük FP'si, bakım |
| 4 | **LLM-as-judge** (entailment deseni) | esnek, bağlam anlar | nondeterministik + maliyet + **veri egemenliği** (dış judge) — entailment'ta görülen sorunlar |
| 5 | **Şimdilik kapsam dışı** + prod notu | risk odaklı, sade | ad-soyad maskelenmez (kabul edilen artık risk) |

## Öneri

**dev/demo: Seçenek 5 — AD-SOYAD kapsam dışı.** Gerekçe:
- Deterministik katman (TCKN + tarih) en yüksek-riskli KVKK **tanımlayıcılarını** (tekil
  kimlik + doğum tarihi) güvenilir kapatıyor; bunlar yeniden-kimliklendirmede baskın.
- Tek başına ad-soyad (başka tanımlayıcı olmadan) daha düşük re-identification riski.
- Ağır NER bağımlılığı projenin **anti-heavy-dep** ilkesine aykırı; LLM yolu nondeterministik +
  veri egemenliği sorunlu (entailment dersleri).

**prod (runbook'a devir): Seçenek 2, opt-in + LOKAL + fail-open.** Prod'da isim maskeleme
gerekirse: yerel spaCy-tr / Türkçe NER modeli, **entailment deseniyle** (opt-in config, fail-open
+ görünür span/metrik, dış API yok). Alternatif kademeli: Seçenek 1 (bağlam-deseni ön-filtre) →
yakalananları yerel NER/LLM ile **doğrula** (FP'yi kes). Karar prod veri-sınıflandırmasına ve
performans bütçesine bağlı.

## Katman tasarımı (uygulanınca)
`guardrails/pii.py` `PiiPolicy`'ye `mask_names: bool` + pluggable `name_detector` eklenir;
deterministik TCKN/tarih yolu değişmez (mevcut testler korunur). `app_config('pii')` ile açılır.

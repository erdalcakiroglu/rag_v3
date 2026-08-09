# Brief — M-18: cevap yapısı (paragraf/madde) prompt nudge — KARNELİ

**Kime:** Sonnet 4.8 · **Kimden:** Mimari · **Tarih:** 2026-08-04
**Statü:** brief hazır; **DAVRANIŞ değişikliği** → k=3 karne ZORUNLU, honesty gate HARD.
**İlke:** Cevabın yapılı (paragraf/madde) gelmesini istiyoruz AMA grounding/honesty'yi bozmadan.

---

## 0. KRİTİK BULGU — düzlük kaza değil, KASITLI

Aktif prompt `SYSTEM_PROMPT_V2` (prompts.py:71-72) açıkça diyor:
> "KISA ve ÖZ yaz (3-6 cümle, düz paragraf). **Başlık/madde imi KULLANMA.** Her cümle bir
> citation ile desteklenmeli; destekleyemeyeceğin cümleyi YAZMA (dolgu/geçiş cümlesi ekleme)."

Bu kural **grounding disiplini** için var: madde/başlık → alıntısız yapısal metin (liste-girişi,
başlık) → coverage düşer → **honesty düşer**. Honesty **0.80 HARD gate'in TAM sınırında** (12/15).
Yani bu nudge bir "render boşluğu" değil, **bilerek konmuş bir güvenlik kuralını gevşetmek** →
gate riski gerçek. FE render (mdBlocks) zaten canlıda; yapı gelirse biçimliyor. Eksik olan
modelin yapı üretmesi — ve onu üretmek bu kuralı değiştirmek demek.

---

## 1. Gerilim (neden delikten iğne geçiriyoruz)

- **İstenen:** paragraf + uygun yerde madde → okunur cevap (ekran görüntüsündeki tek-blok yerine).
- **Korunması gereken:** "her cümle/madde citation'a bağlı", "dolgu/geçiş cümlesi YOK". Madde
  İMİ bunu bozmamalı: her madde **kendi başına citation-bağlı bir iddia** olmalı; **başlık ve
  liste-girişi (alıntısız) YASAK kalmalı.**
- Ayrıca compose atıfları iddia metnini bularak `[n]` yerleştiriyor (`_renumber_answer`); yapı
  değişince eşleşme kayabilir → citation yerleşimi karnede izlenir.

---

## 2. Nudge tasarımı — v5, v2-türevi, TEK DEĞİŞKEN

v4'ün v2'den türetildiği desenle (tek satır farkı kilitleyen test — `test_faz_m15_prompt_v4.py`)
**v5 = v2**, yalnız 71-72'deki satır değişir:

- **ÇIKAN:** "KISA ve ÖZ yaz (3-6 cümle, düz paragraf). Başlık/madde imi KULLANMA. ..."
- **GİREN (öneri, son biçim ön-veride ayarlanır):**
  "Cevabı OKUNUR biçimle: mantıklı yerlerde paragraflara böl; birden çok ayrı olgu/madde
  sıralıyorsan **madde imleri (`- `) kullan — AMA her madde tek başına bir citation'a bağlı bir
  iddia olmalı.** Alıntısız hiçbir satır yazma: **başlık, liste-girişi ('şunlar önemlidir:'),
  dolgu/geçiş cümlesi EKLEME.** Kısa ve öz kal; destekleyemeyeceğin cümle/madde yazma."

Değişmez: GROUNDING (KATI), REDDETME, tool/quote kuralları — hepsi v2'yle birebir.
`V5_BASE = "v2"`; taban v2 (v1 değil — M-16 honesty/fallback kazanımını taşıyan blokları korur).

---

## 3. Karne — ZORUNLU, honesty HARD

Baseline (v2, aktif) ↔ v5, **k=3**, birebir aynı `app_config` (yalnız prompt sürümü farklı),
aynı prod DB + qwen3.5:35b. Kapılar:

| ölçüt | eşik | not |
|---|---|---|
| **honesty (D4)** | **≥12/15 (0.80) — SERT** | gate HARD; düşerse v5 REDDEDİLİR. En kritik. |
| coverage / faithfulness | Δ ≥ −0.03 | madde-girişi/başlık sızarsa buradan görülür |
| context_precision | Δ ≥ −0.03 | |
| fallback oranı | artış yok | |
| citation yerleşimi | bozulmamış | mdBlocks + `_renumber_answer` uyumu; birkaç cevabı gözle kontrol |

**+ Göz kontrolü:** v5 çıktısından 3-5 cevap — gerçekten madde/paragraf var mı, madde-girişi/başlık
(alıntısız) sızmış mı? Sayı yeşil ama alıntısız yapı sızıyorsa nudge biçimi düzeltilir.

---

## 4. Karar

- 4/4 kapı yeşil **ve** alıntısız-yapı sızmıyor → v5 KABUL, DB'de aktive et (`prompts.agent_system_active=v5`).
- honesty <12/15 **veya** faith/prec Δ<−0.03 **veya** alıntısız-yapı sızıyor → v5 REDDEDİLİR, v2 kalır.
  (Bu meşru bir sonuç: grounding-güvenli düz paragraf, bozuk-yapılı cevaba yeğdir.)

---

## 5. Versiyon mekaniği + kapsam çiti

- `PROMPT_VERSIONS`'a `v5` ekle (v2'den programatik türet, tek-satır farkı kilitleyen birim test).
- DB seed + `agent_system_active` ile aktive (config-first; kod fallback güncel kalır).
- **DEĞİŞEN:** yalnız `prompts.py` (v5 gövdesi) + eval koşusu. **DOKUNULMAZ:** `compose`/grounding
  KODU, honesty ölçütü (harness), tool şemaları. FE render zaten hazır. Kod diff yalnız prompts.py'de.
- Ön-veri opsiyonu (ucuz): v5'i uygulamadan önce, mevcut v2 cevaplarından örnekleyip "kaçı zaten
  madde/paragraf içeriyor" say (conversation_messages `\n\n`/`- `/`#`). Çoğu düzse nudge net gerekli;
  bu sayı nudge'ın ne kadar agresif olacağını kalibre eder.

# M-15 — Base-latency anatomisi (ADIM 1 ölçüm raporu + ADIM 2 kol-1 sonucu)

**Tarih:** 2026-07-24 · **Dal:** `feat/h200-transition`
**Ortam:** GGB-AIApp01 (H200), `qwen3.5:35b` Q4_K_M, Ollama, `OLLAMA_KEEP_ALIVE=30m`, üretim DB `10.50.130.55/ragintel`
**Betikler:** `scripts/m15_latency_anatomi.py`, `scripts/m15_http_vs_inproc.py`, `scripts/m15_prefix_cache_probe.py`, `scripts/m15_prompt_v4_switch.py`

**Tek cümlelik sonuç:** Darboğaz LLM'dir ve LLM içinde süre **prompt-eval ile üretim
arasında kabaca yarı yarıya** bölünür; ikisini de kısaltmanın ucuz yolu yok. **Kalite
korunarak P95 < 15 s bu model/donanımda ulaşılamıyor** — elde kalan tek davranış-nötr kol
p95'i ~21 s'den ~19 s'ye indirir.

---

## 0. Premis düzeltmesi

M-15 görevi "**~24-27 s/çağrı** × 2-3 iterasyon" premisiyle açıldı (kaynak
`docs/M10_gs012_Kabul_Kaydi.md` satır 30/34). **Bu sayı çağrı başına değil, SORU başınadır.**

| | ölçülen |
|---|---|
| çağrı başına | p50 **2.4-2.9 s**, p95 7.3-11.4 s |
| soru başına | p50 **11.5-13.3 s**, p95 **21.1-21.3 s**, max 25.8-26.8 s |

M-10'un 24-27 s'i uydurma değil: dağılımın **kuyruğu**. Hedef gerçekten aşılıyor — ama
sebebi "her çağrı 25 s sürüyor" değil, "bazı sorular 3-6 tur atıyor".

**Zemin:** 15 soru (kategori-çeşitli, `PER_CAT=3`), **4 bağımsız temiz koşum**
(p95 = 21.1 / 21.2 / 21.1 / 21.3 s) — ±0.2 s. Tek-koşum gürültüsü değil.
p50 ise 11.5-13.3 s arasında oynuyor; sebebi **tur sayısı dağılımı** (bir koşumda 6 turluk
soru 3 tura düştü). Yani varyansın kaynağı tur sayısı, çağrı hızı değil.

**Aktif prompt: `v2`** — kaynak M-16 ön-verisi (`scripts/m91_fallback_anatomi.py`,
`docs/M16_Kabul_Kaydi.md` §2). Tüm zemin ölçümleri v2 ile alınmıştır.

---

## 1. Hipotezlerin durumu

| # | Hipotez | Ölçüm | Karar |
|---|---|---|---|
| (a) | reasoning-token hâlâ yanıyor | `reasoning_chars` = **0** / 52 çağrı; istekte `reasoning_effort='none'` doğrulandı | **ELENDİ** |
| (b) | tool şeması her tur tekrarı | native A/B: **+728 token, +216 ms**/tur → soru başına ~0.7 s | **Gerçek ama küçük (~%5)** |
| (c) | üretim uzun | LLM payı **%89**; üretim soru başına ~6.7 s | **BASKIN** |
| — | API katmanı vergisi (auth/PII/Redis) | ılık HTTP 11.4/13.0 vs süreç-içi 11.3/12.7 | **ELENDİ** |
| — | soğuk başlangıç / model yükleme | `load_duration` **189 ms**, model VRAM'de yerleşik, keep_alive 30 m | **ELENDİ** |

Hipotez (a) görevin en büyük kazanç adayıydı; ölçüm kapattı. `reasoning_effort='none'`
gerçekten uygulanıyor. Bunun ADIM 2'de beklenmedik bir bedeli çıktı — bkz. §7.

---

## 2. Süre nereye gidiyor

**Formül:** `soru süresi ≈ tur sayısı × (prompt-eval + üretim)`

Tur sayısı ortalama **~3.5**, tur başına **~3.1-3.5 s**.

**Prompt-eval oranı üç bağımsız yoldan ölçüldü ve uyuştu:**
- gs-v0-029'un çağrı satırlarından regresyon: eğim **0.33 ms/token**, kesişim ~0.05 s
- native tool A/B'nin marjinali: 728 tok / 216 ms = **0.295 ms/token**
- soğuk native prob: 4545 tok / 1412 ms = **3219 tok/s**

→ 5.3k'lık prompt **her turda ~1.75 s**.

**Decode tavanı:** native tek akış **109.3-109.8 tok/s** (üç ölçüm). Ajan yolundan türetilen
~75 tok/s buna yakın. Yani "çağrıyı hızlandırma" diye bir kol **yok**; tek kol daha az token
üretmek ya da aynı token'ı tekrar tekrar işlememek.

**Soru başına bütçe (kabaca yarı yarıya):**

| kalem | süre | not |
|---|---|---|
| prompt-eval | ~6.1 s | 3.5 tur × 1.75 s — **aynı ön-ek tekrar tekrar** |
| üretim | ~6.7 s | ~740 token @ ~110 tok/s |
| retrieval + embed + DB + graph | ~1.5 s | %11 |

---

## 3. Üretilen token nereye gidiyor

Toplam ~32.000 karakter / 44 citation, ~2.9 krk/token (n=15, zemin):

| kalem | pay | değerlendirme |
|---|---|---|
| **serbest metin** | **%34.3** | bunun **%24.9'u ATILIYOR** (tool çağrısıyla birlikte gelen prose — `agent_node` `tool_calls` dalına girer, okumaz), %9.4'ü taslak olarak kullanılır |
| cevap metni | %23.2 | ürünün kendisi |
| `claim` | %17.0 | **dokunulamaz** — coverage paydasının çapası (`grounding.py:136-151`, M-16 FIX-1) ve `[n]` yerleşiminin çapası (`compose.py:79`) |
| `quote` | %17.3 | kısaltılabilir ama `quote` yoksa `fabricated_quote` kısıtı hiç uygulanmıyor (`grounding.py:90`) → kalkana dokunur |
| arama argümanı | %8.2 | küçük |

**Kullanıcının gördüğü cevap, üretilen token'ın yalnızca %23'ü.**

Atılan metin ne: model tool'u çağırırken bir yandan cevabın tam prose'unu yazıyor —
*"Karbon ayak izi, bir kişinin, şirketin, kuruluşun, ürünün veya etkinliğin doğrudan veya
dolaylı olarak atmosfere saldığı sera gazı miktarını…"* — sonra o metin okunmadan çöpe
gidiyor. 14 çağrıda ~8.000 karakter ≈ **2.793 token ≈ 25 s / 15 soru**.

Bu bölüm ADIM 2'nin kol-1'ini doğurdu. **Ölçüm doğruydu, ondan çıkarılan sonuç yanlıştı** — §7.

---

## 4. Ön-ek (KV cache) kırılması — cache VAR

Ajan turlar arası prompt ön-ekini **iki yerden** bozuyor:

- `agent.py:129` — sistem mesajının **sonuna** her tur değişen `[Kalan iterasyon: N]` sayacı.
  Ön-ek ilk bloktan itibaren kırılır.
- `agent.py:132` — bağlam blokları her tur `state["retrieved"]`'dan **yeniden** render edilir.
  Kanıt: `prompt_tok` 5297 → 5551 → **5359** (monoton değil; append-only olsa artardı).

**Prob (`m15_prefix_cache_probe.py`, ~4.5k token bağlam, `num_predict=1`):**

| # | deney | prompt-eval | A'ya oran |
|---|---|---|---|
| A | soğuk (ilk kez) | 1412 ms (3219 tok/s) | — |
| B | birebir aynı mesajlar | **596 ms** | **%42** |
| D | **append-only** (ön-ek sabit, sona ek) | **683 ms** | **%48** |
| C0 | sayaç=2 (slotu doldur, kontrol) | 1310 ms | %93 |
| C | sayaç=1 (yalnız SAYI değişti) | 1297 ms | **%92** |

**Okuma:**
- **Cache VAR.** B'nin hızlanması ısınma değil: kontrol deneyi C0 hemen B'nin ardında koştu
  ve **1310 ms**'e geri çıktı. Isınma olsaydı C0 da hızlı olurdu. Karar bu kontrole dayanıyor.
- **Sayaç cache'i öldürüyor.** Sistem mesajının sonundaki tek hane değişince maliyet %92'ye
  dönüyor — ön-ek ilk bloktan itibaren kırılıyor.
- **Append-only kazandırıyor:** ön-ek korunup sona mesaj eklendiğinde %48.

**Metodoloji notu (kendi hatam):** ilk koşumda D, C0/C'den *sonra* geliyordu ve "%103,
kazandırmıyor" çıktı. Sebep: **Ollama varsayılanda tek slot tutar**; araya giren farklı bir
prompt cache'i eziyor. D, B'nin hemen ardına alınınca %48'e düştü. İlk sonuç geçersizdi.

**Operasyonel bedeli:** tek slot, §5'teki eşzamanlılık bulgusuyla birleşiyor — iki kullanıcı
dönüşümlü istek atarsa birbirinin ön-ekini eziyor. Ön-ek disiplininin kazancı **tek
kullanıcıda** geçerli.

---

## 5. Yan bulgular (M-15 kapsamı dışı, kayda değer)

**Eşzamanlılık.** Kazara iki koşum aynı anda çalıştı: çağrı başına p50 **2.9 s → 170.6 s**,
LLM payı %89 → %32 (embedding çağrıları da kuyrukta bekledi). Ollama tek akış işliyor,
batching yok. **"P95 < 15 s" hedefi tek kullanıcı içindir**; iki eşzamanlı kullanıcıda tablo
çöker. Kapasite planlaması ayrı iş kalemi.

**LLM-dışı aykırılık.** Bir koşumda gs-v0-029 24.1 s sürdü ama LLM payı **%30** — 16.8 s
retrieval+embed+DB tarafındaydı (zeminde bu kalem ~1.5 s, %11). Tek gözlem, incelenmedi.
Ayrı kalem olarak açık.

---

## 6. Kolların sınıflandırması

ADIM 2'nin dersinden sonra (bkz. §7) kollar iki sınıfa ayrılıyor:

**Davranış-nötr** — üretilen token bit bit aynı kalır, model hiçbir şeyi farklı yapmaz.
Karne riski ~yok.

| kol | beklenen kazanç | durum |
|---|---|---|
| Ön-ek disiplini (sayacı taşı **+** append-only bağlam) | ~2.3 s/soru | **AÇIK — tek sağlam aday** |
| Tool şemasını her turda göndermemek | ~0.7 s/soru | açık, küçük; LiteLLM/Ollama sözleşmesi elverirse |

**Davranış-değiştiren** — modelin ne ürettiğine dokunur. Her biri k=3 karne ister; kol-1
gösterdi ki **bedeli latency kazancından büyük olabilir**.

| kol | beklenen kazanç | durum |
|---|---|---|
| Atılan serbest metni kes (prompt kuralı) | ~1.7 s/soru (ölçülen 4.0 s) | **ÖLÜ — reddedildi**, §7 |
| `quote` uzunluk sınırı | ~1 s/soru | riskli — `fabricated_quote` kalkanına dokunur |
| Tur sayısını azaltma | büyük | en yüksek risk; M-16 kazanımına en yakın tehdit |
| `claim` kısaltma | — | **ELENDİ** — coverage paydasının çapası |

**Ön-ek disiplini aritmetiği:** ilk tur her hâlükârda tam eval (~1.75 s). Kalan ~2.5 tur
%48'e inerse 4.4 s → 2.1 s. Kazanç ≈ **2.3 s/soru**.

**Tek parça değildir:** sayacı sistem mesajından çıkarmak TEK BAŞINA neredeyse hiçbir şey
kazandırmaz — sayacın hemen ardındaki bağlam bloğu zaten her tur yeniden render ediliyor.
Sayaç taşıma, append-only'nin **önkoşuludur**. Append-only ise
`context_builder._apply_budget`'ın "en düşük skorluyu at" tahliyesini "en yeniyi at"a
çevirmeyi gerektirir — bu bir **retrieval politikası değişikliğidir**, salt optimizasyon
değil. Ayrı karar kalemi, ayrı karne.

---

## 7. ADIM 2 kol-1: ölçüldü, REDDEDİLDİ

Tam kayıt: [`M15_Kol1_Red_Kaydi.md`](M15_Kol1_Red_Kaydi.md).

v2'ye **tam bir satır** eklendi (prompt v4): *"Tool çağıracaksan YANINDA düz metin YAZMA…"*.
Tek değişken kod ve testle kilitlendi (`tests/test_faz_m15_prompt_v4.py`).

**Mekanizma tuttu:**

| | zemin (4 koşum) | v4 |
|---|---|---|
| ATILAN serbest metin | 8008-8039 krk (%24.9-25.5) | **4354 krk (%17.4)** |
| toplam üretim | 31.567 krk | 24.968 krk |
| p95 | 21.1 / 21.2 / 21.1 / 21.3 s | **17.2 s** |

**Kalite karnesi (k=3, 7394 s) kabul şartını çiğnedi:**

| eksen | M-16 mührü (v2) | v4 | şart |
|---|---|---|---|
| answerable fallback | 9/93 = **%9.68** | **19/93 = %20.4** | **✗ 2.1× gerileme** |
| unanswerable honesty | 9/15 | 14/15 | ✓ (aşağıya bak) |
| faithfulness (≥0.85) | 0.9273 | 0.934 | ✓ |
| context_precision (≥0.80) | 0.8774 | 0.881 | ✓ |

Fallback yalnız gerilemedi: **M-16 FIX-1'in tüm kazanımını sildi ve M-9.1 zemininin (%19.35)
de altına düştü.** Gürültü değil — üç tekrarda tutarlı (6/31, 8/31, 5/31), 19 vakanın 15'i
3/3 deterministik. Doğrudan kanıt: gs-v0-002 ve gs-v0-023, M-16'da *cevaplanan* negatif-olgu
vakalarıydı; v4'te 3/3 fallback'e döndü.

**Honesty'deki 9/15 → 14/15 kazanç DEĞİLDİR.** Aynı sebebin öteki yüzü: model daha çok
reddediyor, `_honesty()` tanımı da (`fabricated = len(sources) > 0`) reddetmeyi ödüllendiriyor
— M-16'nın "kırık olan davranış değil ölçüt" teşhisinin tekrarı. Sistem daha dürüst olmadı,
daha çok sustu. Bu sayı **M-17'nin lehine kanıttır**, kol-1'in lehine değil.

**Kök:** prompt yazılmadan önce not edilen risk gerçekleşti — `reasoning_effort='none'` açıkken
atılan o prose **modelin fiilî not defteriydi**. §1'de (a) hipotezini kapatmak iyi haberdi;
meğer düşünme zaten kapalı olduğu için model, serbest metni scratchpad olarak kullanıyormuş.

**Ders (§3'ün yorumunu düzeltir):** *"ürün çıktısına girmeyen token bedavadır"* **yanlış**.
Okunmayan metin, üretildiği anda modelin durumunu kuruyor. §3'teki "bedava kazanç tavanı"
ifadesi bu yüzden yanıltıcıdır; ölçüm doğru, etiketi yanlıştı.

---

## 8. Hedefe dürüst bakış

Kol-1 öldü. Elde **davranış-nötr** sınıfta ön-ek disiplini (~2.3 s) ve tool şeması (~0.7 s)
kalıyor → p95 21.2 → **~19 s**, iyimser tahminle ~18 s.

**Kalite korunarak P95 < 15 s bu model/donanımda ulaşılamıyor.** Kalanı kapatmanın tek yolu
tur sayısına dokunmak (ort. 3.5) — kol-1 koşumu, bir satır prompt'un fallback'i iki katına
çıkarabildiğini göstererek bu bölgenin ne kadar kırılgan olduğunu kanıtladı.

Dürüst seçenekler:

1. **Ön-ek disiplinini uygula, p95 ~19 s'i kabul et.** Davranış-nötr; tek gerçek risk
   `_apply_budget` tahliye politikası değişikliği (kendi karnesiyle).
2. **Hedefi p50'ye çevir** (13.3 s — zaten altında) + streaming ile algılanan gecikmeyi
   düşür. Kaliteye sıfır dokunuş, kullanıcı deneyiminde en yüksek getiri.
3. **Daha küçük/hızlı model**, kendi tam karnesiyle. En büyük kazanç, en büyük belirsizlik.

**Kısıt (değişmez):** her fix k=3 karne-korumalı. Latency düşerken faithfulness /
context_precision / honesty / fallback **sabit** kalmazsa fix geri alınır. Kol-1'de bu kural
işletildi: latency %19 kazandı, fix yine de geri alındı.

**VERİLEN KARAR (2026-07-24): seçenek 2.** P95 eşiği kaldırıldı; yerine **p50 < 15 sn**
(sağlanıyor) + **TTFB < 1 sn** kondu ve TTFB'yi sağlayan aşama streaming'i eklendi
(`/api/ask/stream`). p95 gösterge olarak izlenmeye devam eder. Ayrıntı ve güvenlik
sözleşmesi: [M15_Hedef_Revizyonu_ve_Streaming.md](M15_Hedef_Revizyonu_ve_Streaming.md).
Kol-2 backlog'a alındı — kazancı gerçek (~2.3 sn) ama `_apply_budget` tahliye politikasını
değiştirdiği için kendi karnesini ister.

---

## Ek: ölçüm zemini dersleri (bu çalışmada yaşananlar)

| olay | sonuç | kural |
|---|---|---|
| Üretim config'i **dev makinesindeki** Postgres'ten (`192.168.36.15`) doğrulanmaya çalışıldı; üretim `10.50.130.55` | "aktif prompt v2/v4" çelişkisi; bir ölçüm yanlışlıkla geçersiz sanıldı | Üretim config teyidi **daima konteynerde** koşulur |
| v4 anahtar betiği gövdeyi **koddan** türetiyordu | yanlış DB'nin gövdesinden türetme riski | Üretim config'ini değiştiren betik türetmesini **canlı DB gövdesinden** yapar |
| Konteynerdeki `/app/ragintel` imajdan gelir | `ImportError`; host'taki `git pull` konteyneri güncellemez | Config değişikliği ≠ kod değişikliği; kol-1 imaj kurmadan uygulanabildi |
| İki koşum kazara eşzamanlı | çağrı p50 2.9 s → 170.6 s; ölçüm çöp | Ölçüm koşarken GPU'da başka iş olmamalı |
| `python` (`-u` yok) + `tee` | log dakikalarca boş; "takıldı" sanıldı | Uzun koşum `python -u`; canlılık sinyali `nvidia-smi` |
| Ön-ek probunda D, C0/C'den sonra koştu | "append kazandırmıyor" — geçersiz | Tek slotlu cache'te deney sırası sonucun parçasıdır |
| n=5 ile "hedef zaten sağlanıyor" denildi | n=15'te p95 21 s | Kuyruk metriği küçük örneklemle raporlanmaz |

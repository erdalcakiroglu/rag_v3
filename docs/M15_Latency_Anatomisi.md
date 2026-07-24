# M-15 ADIM 1 — Base-latency anatomisi (ölçüm raporu)

**Tarih:** 2026-07-24 · **Dal:** `feat/h200-transition` · **Ortam:** GGB-AIApp01 (H200), `qwen3.5:35b` Q4_K_M, Ollama, `OLLAMA_KEEP_ALIVE=30m`
**Betikler:** `scripts/m15_latency_anatomi.py`, `scripts/m15_http_vs_inproc.py`, `scripts/m15_prefix_cache_probe.py`

---

## 0. Premis düzeltmesi

M-15 görevi "**~24-27 s/çağrı** × 2-3 iterasyon" premisiyle açıldı. Kaynak
`docs/M10_gs012_Kabul_Kaydi.md` satır 30/34. **Bu sayı çağrı başına değil, SORU başınadır.**

| | ölçülen |
|---|---|
| çağrı başına | p50 **2.9 s**, p95 9.4-11.4 s |
| soru başına | p50 **12.9-13.3 s**, p95 **21.1-21.2 s**, max 26.2-26.8 s |

M-10'un 24-27 s'i uydurma değil: dağılımın **kuyruğu** (max 26.8 s). Yani hedef gerçekten
aşılıyor — ama sebebi "her çağrı 25 s sürüyor" değil, "bazı sorular 3-6 tur atıyor".

**Zemin:** 15 soru (kategori-çeşitli, `PER_CAT=3`), **4 bağımsız temiz koşum**
(p95 = 21.1 / 21.2 / 21.1 / 21.3 s), sonuçlar ±0.4 s içinde örtüşüyor. Tek-koşum gürültüsü
değil. p50 ise 11.5-13.3 s arasında oynuyor; sebebi tur sayısı dağılımı (bir koşumda
6-turluk soru 3 tura düştü) — yani **varyansın kaynağı tur sayısı**, çağrı hızı değil.

**Aktif prompt: `v2`** (`app_config.prompts.agent_system_active`, `m4-migration`,
2026-07-10'dan beri değişmemiş). Tüm zemin ölçümleri v2 ile alınmıştır; ADIM 2'de
karşılaştırılacak her sürüm v2'den türetilmelidir.

---

## 1. Hipotezlerin durumu

| # | Hipotez | Ölçüm | Karar |
|---|---|---|---|
| (a) | reasoning-token hâlâ yanıyor | `reasoning_chars` = **0** / 52 çağrı; istekte `reasoning_effort='none'` doğrulandı | **ELENDİ** |
| (b) | tool şeması her tur tekrarı | native A/B: **+728 token, +215 ms**/tur → soru başına ~0.7 s | **Gerçek ama küçük (~%5)** |
| (c) | üretim uzun | LLM payı **%89**; üretim soru başına ~6.7 s | **BASKIN** |
| — | API katmanı vergisi (auth/PII/Redis) | ılık HTTP 11.4/13.0 vs süreç-içi 11.3/12.7 | **ELENDİ** |
| — | soğuk başlangıç / model yükleme | `load_duration` **189 ms**, model VRAM'de yerleşik, keep_alive 30 m | **ELENDİ** |

---

## 2. Süre nereye gidiyor

**Formül:** `soru süresi ≈ tur sayısı × (prompt-eval + üretim)`

Tur sayısı dağılımı: `[2,4,3,4,4,3,3,3,3,3,6,3,3,4,4]` — ortalama ~3.5, **tur başına ~3.5 s**.

**Prompt-eval oranı iki bağımsız yoldan ölçüldü ve uyuştu:**
- gs-v0-029'un çağrı satırlarından regresyon: eğim **0.33 ms/token**, kesişim ~0.05 s
- native tool A/B'nin marjinali: 728 tok / 215 ms = **0.295 ms/token**
- soğuk native prob: 4545 tok / 1305 ms = **3482 tok/s**

→ 5.3k'lık prompt **her turda ~1.75 s**.

**Decode tavanı:** native tek akış **109.6-109.8 tok/s**. Ajan yolundan türetilen ~75 tok/s
buna yakın; yani "çağrıyı hızlandırma" diye bir kol **yok**. Tek kol: daha az token üretmek.

**Soru başına bütçe (kabaca yarı yarıya):**

| kalem | süre | not |
|---|---|---|
| prompt-eval | ~6.1 s | 3.5 tur × 1.75 s — **aynı ön-ek tekrar tekrar** |
| üretim | ~6.7 s | ~740 token @ 109.8 tok/s |
| retrieval + embed + DB + graph | ~1.5 s | %11 |

---

## 3. Üretilen token nereye gidiyor

Toplam 32.155 karakter / 44 citation, ~2.9 krk/token (n=15):

| kalem | pay | değerlendirme |
|---|---|---|
| **serbest metin** | **%34.3** | bunun **%24.9'u ATILIYOR** (tool çağrısıyla birlikte gelen prose — `agent_node` tool_calls dalına girer, okumaz), %9.4'ü taslak olarak kullanılır |
| cevap metni | %23.2 | ürünün kendisi |
| `claim` | %17.0 | **dokunulamaz** — coverage paydasının çapası (`grounding.py:136-151`, M-16 FIX-1) ve `[n]` yerleşiminin çapası (`compose.py:79`) |
| `quote` | %17.3 | kısaltılabilir ama `quote` yoksa `fabricated_quote` kısıtı hiç uygulanmıyor (`grounding.py:90`) → kalkana dokunur |
| arama argümanı | %8.2 | küçük |

**Kullanıcının gördüğü cevap, üretilen token'ın yalnızca %23'ü.**

Atılan metnin ne olduğu (örnekler): model tool'u çağırırken bir yandan cevabın tam
prose'unu yazıyor — *"Karbon ayak izi, bir kişinin, şirketin, kuruluşun, ürünün veya
etkinliğin doğrudan veya dolaylı olarak atmosfere saldığı sera gazı miktarını…"* — sonra
o metin okunmadan çöpe gidiyor. 14 çağrıda ~8.000 karakter ≈ **2.793 token ≈ 25 s / 15 soru**.

---

## 4. Ön-ek (KV cache) kırılması — KAPANDI: cache VAR

Ajan turlar arası prompt ön-ekini **iki yerden** bozuyor:

- `agent.py:129` — sistem mesajının **sonuna** her tur değişen `[Kalan iterasyon: N]` sayacı.
  Ön-ek ilk bloktan itibaren kırılır.
- `agent.py:132` — bağlam blokları her tur `state["retrieved"]`'dan **yeniden** render edilir.
  Kanıt: `prompt_tok` 5297 → 5551 → **5359** (monoton değil; append-only olsa artardı).

Ön-ek sabitlenirse tur başına ~1.7 s, soru başına ~3.5 s kazanç olur ve **üretilen token
hiç değişmez** (çıktı bit bit aynı). **Ama yalnızca Ollama bu yolda ön-ek cache'i yapıyorsa.**

**Prob sonucu (`m15_prefix_cache_probe.py`, ~4.5k token'lık bağlam, `num_predict=1`):**

| # | deney | prompt-eval | A'ya oran |
|---|---|---|---|
| A | soğuk (ilk kez) | 1412 ms (3219 tok/s) | — |
| B | birebir aynı mesajlar | **596 ms** | **%42** |
| D | **append-only** (ön-ek sabit, sona ek) | **683 ms** | **%48** |
| C0 | sayaç=2 (slotu doldur, kontrol) | 1310 ms | %93 |
| C | sayaç=1 (yalnız SAYI değişti) | 1297 ms | **%92** |

**Okuma:**
- **Cache VAR.** B'nin hızlanması ısınma değil: C0 hemen B'nin ardında çalıştı ve **1310 ms**'e
  geri çıktı. Isınma olsaydı C0 da hızlı olurdu.
- **Sayaç cache'i öldürüyor.** Sistem mesajının sonundaki tek hane değişince maliyet
  %92'ye dönüyor — ön-ek ilk bloktan itibaren kırılıyor (`agent.py:129`).
- **Append-only kazandırıyor:** ön-ek korunup sona mesaj eklendiğinde %48.

**Metodoloji notu (kendi hatam):** ilk koşumda D, C0/C'den *sonra* geliyordu ve "%103,
kazandırmıyor" çıktı. Sebep: **Ollama varsayılanda tek slot tutar**; araya giren farklı bir
prompt cache'i eziyor. D, B'nin hemen ardına alınınca %48'e düştü. İlk sonuç geçersizdi.

**Bunun operasyonel bedeli:** tek slot, §5'teki eşzamanlılık bulgusuyla birleşir — iki
kullanıcı dönüşümlü istek atarsa birbirinin ön-ekini eziyor ve kol-2'nin kazancı **tek
kullanıcıda** geçerli kalıyor.

---

## 5. Yan bulgu: eşzamanlılık (M-15 kapsamı dışı, kayda değer)

Kazara iki koşum aynı anda çalıştı. Sonuç: çağrı başına p50 **2.9 s → 170.6 s**, LLM payı
%89 → %32 (embedding çağrıları da kuyrukta bekledi). Ollama tek akış işliyor, batching yok.

**"P95 < 15 s" hedefi tek kullanıcı içindir.** İki eşzamanlı kullanıcıda tablo çöker.
Kapasite planlaması ayrı bir iş kalemi.

---

## 6. ADIM 2 için kol sıralaması

| kol | beklenen kazanç | kalite riski |
|---|---|---|
| 1. Atılan serbest metni kes (prompt kuralı) | ~1.7 s/soru | **Düşük** — ürün çıktısına hiç girmiyor; ama `reasoning_effort='none'` açıkken bu prose modelin fiilî not defteri olabilir → karne karar verir |
| 2. Ön-ek disiplini (sayacı taşı **+** append-only bağlam) | ~2.3 s/soru | **Orta** — append-only, `_apply_budget`'ın tahliye politikasını değiştirmeyi gerektirir (retrieval politikası) |
| 3. `quote` uzunluk sınırı | ~1 s/soru | **Orta** — `fabricated_quote` kalkanına dokunur |
| 4. Tur sayısını azaltma | büyük | **Yüksek** — M-16 kazanımına en yakın tehdit, son çare |
| — | `claim` kısaltma | **ELENDİ** — coverage paydasının çapası |

**Kol-2 aritmetiği:** ilk tur her hâlükârda tam eval (~1.75 s). Kalan ~2.5 tur %48'e inerse
4.4 s → 2.1 s. Kazanç ≈ **2.3 s/soru**, üretilen token değişmeden.

**Kol-2 tek parça değildir:** sayacı sistem mesajından çıkarmak TEK BAŞINA neredeyse hiçbir
şey kazandırmaz — sayacın hemen ardındaki bağlam bloğu zaten her tur yeniden render ediliyor
(§4, `prompt_tok` monoton değil). Sayaç taşıma, append-only'nin **önkoşuludur**; ikisi
birlikte uygulanmalı. Append-only ise `context_builder._apply_budget`'ın "en düşük skorluyu
at" tahliyesini "en yeniyi at"a çevirmeyi gerektirir — bu bir **retrieval politikası
değişikliğidir**, salt optimizasyon değil. Ayrı bir karar kalemi olarak ayrı karneyle gider.

**Hedefe dürüst bakış:** 1 + 2 ≈ **4 s/soru** → p95 21.1 → **~17 s**. İki düşük-riskli kol
birlikte bile **P95 < 15 s'e ulaşmıyor.** Kalan ~2 s yalnızca **tur sayısına** dokunarak
kapanır (ort. 3.5 tur) — ki bu M-16 kazanımına en yakın tehdittir ve ayrı yetki ister.
Alternatif dürüst çıkışlar: daha küçük/hızlı model (kalite karnesiyle), streaming ile
algılanan gecikmeyi düşürmek, ya da hedefi p50'ye çevirmek (13.3 s — zaten altında).

**Kısıt (değişmez):** her fix k=3 karne-korumalı. Latency düşerken faithfulness /
context_precision / honesty / fallback **sabit** kalmazsa fix geri alınır (M-16 kazanımı
bozulmaz).

# M-15 kapanışı — gecikme hedefinin revizyonu + aşama streaming'i

**Tarih:** 2026-07-24 · **Dal:** `feat/h200-transition`
**Girdi:** [M15_Latency_Anatomisi.md](M15_Latency_Anatomisi.md) (ölçüm), [M15_Kol1_Red_Kaydi.md](M15_Kol1_Red_Kaydi.md) (reddedilen fix)
**Karar:** P95 eşiği kaldırıldı; yerine **p50 < 15 sn + TTFB < 1 sn** kondu ve TTFB'yi
sağlayan **aşama streaming'i** (`/api/ask/stream`) eklendi.

---

## 1. Neden hedef değişti

Özgün MVP kriteri: *"P95 uçtan uca yanıt < 15 sn"*. M-15 ölçtü:

- Süre ≈ **tur sayısı × (prompt-eval + üretim)**. Decode tavanı ~110 tok/s, prompt-eval
  0.33 ms/token — ikisi de bu model/donanımda sabit. "Çağrıyı hızlandırma" diye bir kol yok.
- Kalan **davranış-nötr** kollar (ön-ek disiplini + tool şeması) toplam ~3 sn → p95 ~19 sn.
- Tek büyük kaldıraç modelin ürettiğini değiştirmekti. Denendi (kol-1): p95 21.2 → 17.2 sn,
  **ama fallback %9.68 → %20.4**. Reddedildi.

Yani P95 < 15 sn, kalite korunarak **ulaşılamaz**. Hedefi olduğu gibi bırakmak iki kötü
sonuçtan birini doğururdu: ya kalıcı olarak "kırmızı" bir kriter, ya da onu yeşile boyamak
için kaliteden ödün veren bir fix. İkincisi bu projede zaten bir kez yaşandı (M-9'daki
"sapkın gate teşviki"), ikinci kez davet edilmedi.

## 2. Yerine ne kondu

| kriter | eşik | gerekçe |
|---|---|---|
| **p50** uçtan uca | < 15 sn | tipik deneyimi ölçer; ölçülen 11.5-13.3 sn — **sağlanıyor** |
| **TTFB** (ilk geri bildirim) | < 1 sn | kullanıcının fiilen hissettiği şey; streaming ile ~0.1 sn |
| p95 | **gösterge** (eşik değil) | izlenir, gerileme raporlanır, fix'in kabul şartı DEĞİL |

**p95 neden eşik olmaktan çıktı:** p95'i belirleyen şey çağrı hızı değil **tur sayısı**
(ort. 3.5, max 5) — yani sorunun kaç adımda çözüldüğü. Bunu eşiğe bağlamak sistemi zor
soruyu erken bırakmaya teşvik eder; ödüllendirdiği davranış tam olarak M-16'nın engellemeye
çalıştığı davranıştır. Gösterge olarak kalır çünkü **gerilemeyi görmek** hâlâ gerekli.

## 3. Aşama streaming'i — ne akıyor, ne akmıyor

**Cevap METNİ akmıyor.** Bu mimaride nihai cevap LLM'den akmaz: ajan `submit_answer`
tool'uyla teslim eder, metin `compose` düğümünde deterministik kurulur (grounding, PII
maskeleme, citation yerleşimi orada olur). Token-streaming için cevabın teslim kanalını
değiştirmek gerekirdi — bu, M-16'nın mühürlediği grounding zincirine dokunmak demek. Yapılmadı.

**Akan şey AŞAMA sinyali:**

| olay | ne zaman | yük |
|---|---|---|
| `open` | istek kabul edilir edilmez (kilit beklenmeden) | session_id, injection bayrağı |
| `tool` | ajan tool çağırmaya KARAR verince, **çağrı koşmadan ÖNCE** | tool adı, arama sorgusu (≤120 krk) |
| `retrieved` | tool dönünce | toplam kaynak SAYISI |
| `step` | prepare / validate / compose / fallback bitince | aşama adı |
| `final` | bitişte | `/api/ask` ile AYNI FinalResponse |

Beklenen zaman çizgisi (anatomi §2: tur ≈ 3.2 sn):

```
t≈0.0s  bağlandı
t≈2.9s  Belgelerde arıyorum “karbon vergisi oranı”
t≈3.2s  12 kaynak bulundu
t≈6.4s  Bağlamı genişletiyorum
t≈9.8s  Kaynaklar doğrulanıyor
t≈11.5s [yanıt]
```

Bugün kullanıcı bu 11.5 sn'nin (p95'te 21.2 sn'nin) tamamını **boş ekrana** bakarak geçiriyor.

## 4. Kaliteye dokunmadığının kanıtı

**Karne yeniden koşulmadı — koşulmasına gerek yok, ve bu bir varsayım değil:**

- Eval yolu `run_agent`'ı DOĞRUDAN çağırır ([`eval/harness.py:151`](../ragintel/eval/harness.py#L151)).
- `run_agent` **değiştirilmedi** (hâlâ `app.invoke`). Streaming ayrı bir fonksiyondur:
  `run_agent_stream`. LangGraph'ta `invoke` ≈ `stream`'in son değeri olsa da mühürlü yolu
  "≈" gerekçesiyle değiştirmek bu projenin ölçüm disiplinine aykırı olurdu.
- Bedeli iki yolun zamanla sapabilmesi. Karşılığı
  `tests/test_faz_m15_stream.py::test_stream_and_invoke_agree` — ikisinin aynı final state'i
  ürettiğini kilitler (trace_id hariç; o koşuma özgüdür ve İKİ yolda da dolu olmak zorundadır).
- `/api/ask` gövdesi ve davranışı aynı; WebUI akış kullanılamazsa ona düşer.

## 5. Güvenlik: olay yükü allowlist'tir

Yeni bir yüzey açıldı, dolayısıyla en eski kural oraya da taşındı: **`user_ctx` /
`allowed_doc_scopes` istemciye SIZMAZ.**

- `tool_event()` argüman sözlüğünü OLDUĞU GİBİ geçirmez; yalnız `query` (kırpılmış) ve
  SAYILAR çıkar. **Denylist değil allowlist** — tool şeması yarın büyüdüğünde denylist
  sessizce yanılır, allowlist yanılmaz.
- `node_event()` bilinmeyen düğüm için `None` döner: grafa yeni düğüm eklenirse içeriği
  kendiliğinden akmaz (fail-closed).
- Testler: `test_tool_event_drops_everything_but_query_and_counts`,
  `test_node_event_unknown_node_is_silent`, `test_node_event_never_forwards_state_wholesale`.
- AuthN akıştan ÖNCE koşar: generator'ın ilk `next()`'i StreamingResponse dönmeden çağrılır →
  401/400 gerçek HTTP durumu olur, SSE gövdesine gömülü bir hata değil.
- UI ilerleme satırlarını `textContent` ile basar, `innerHTML` ile değil: arama sorgusu MODEL
  üretimi metindir.
- SSE kare sınırı `json.dumps` kaçışıyla korunur — model metnindeki `\n\n` kareyi kıramaz
  (`test_sse_frame_cannot_be_broken_by_newlines_in_model_text`).

## 6. Dağıtım notları

- **Bu bir KOD değişikliğidir** (kol-1'in aksine, ki o saf config'ti) → imaj yeniden
  kurulmalı, sonra `docker restart ragintel-api`.
- nginx/ters vekil arkasındaysa tamponlama akışı öldürür; uç `X-Accel-Buffering: no` gönderir.
  Vekil tarafında `proxy_buffering off` gerekebilir.
- Eşzamanlılık **değişmedi**: `RagRuntime._lock` hâlâ grafı serileştirir, Ollama hâlâ tek
  akış. Streaming ikinci kullanıcının beklemesini kısaltmaz — yalnız beklerken NE olduğunu
  gösterir. Kapasite ayrı kalem (anatomi §5).
- Kalp atışı (heartbeat) yok: turlar arası boşluk ~3.2 sn, tipik vekil zaman aşımlarının
  çok altında. Uzun turlarda sorun görülürse eklenir.

## 7. Açık kalanlar

| kalem | durum |
|---|---|
| kol-2: ön-ek disiplini (sayacı taşı + append-only bağlam) | **backlog** — ~2.3 sn/soru; `_apply_budget` tahliye politikası değişir, kendi k=3 karnesini ister |
| tool şemasını her turda göndermeme | backlog — ~0.7 sn/soru |
| gs-v0-029'da 16.8 sn LLM-DIŞI aykırılık | incelenmedi |
| eşzamanlılık çöküşü (2.9 sn → 170.6 sn) | kapasite kalemi, M-15 dışı |
| M-17: honesty ölçütü (`fabricated = len(sources) > 0`) | sıradaki iş; v4 karnesi ek kanıt üretti |

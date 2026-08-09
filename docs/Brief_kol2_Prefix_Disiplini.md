# Brief — kol-2: ön-ek (prefix) disiplini ile prompt-eval tasarrufu

**Kime:** Sonnet 4.8 · **Kimden:** Mimari · **Tarih:** 2026-07-25
**Statü:** ön-veri TAMAM (prefix cache gerçek) → rework GREENLIGHT.
**İlke:** Latency; davranış NÖTR hedefленir ama (b) modelin gördüğü bağlamı değiştirir → **k=3 karne şart**.

---

## 0. Ön-veri (bitti — tekrar koşma)

`scripts/kol2_prefix_cache_probe.py` (H200, qwen3.5:35b) prefix KV-cache'in bu ortamda çalıştığını
kanıtladı. Kritik: cache'in işareti `prompt_eval_**duration**`'dır (Ollama cache-hit'te bile
`prompt_eval_count`'u tüm prefix olarak raporlar):

| senaryo | prompt_eval_count | prompt_eval_ms |
|---|---|---|
| STABLE çağrı-2 (append, prefix aynı) | 2732 | **97** |
| MUTATED çağrı-2 (sayaç ön-ekte değişti) | 2740 | **694** |

→ Prefix kararlıyken tur başına **~597ms** prompt-eval tasarrufu; ~3.5 turla ≈ **2.1s** (M-15 ~2.3s
tahminiyle örtüşür). Ödül gerçek. İki ön-ek istikrarsızlığı kodda tespit edildi:

1. **Sayaç ön-ekte** — `agents/nodes/agent.py:129`:
   `system = load_system_prompt(cfg) + f"\n\n[Kalan iterasyon: {remaining}]"` — her tur değişir,
   sistem mesajının içinde → ilk mesaj her tur farklı → tüm cache geçersiz.
2. **Bağlam her tur yeniden kurulur** — `agent.py:159-160` + `_assemble_messages` (126-135):
   `context = context_builder.build(retrieved)` her turda; blok numaraları `_apply_budget`
   tahliyesiyle kayar → mesaj 2 (bağlam) baştan sona değişebilir.

---

## 1. Parça (a) — sayacı tail'e taşı  [DÜŞÜK RİSK, yüksek değer]

`[Kalan iterasyon: N]`'i sistem prompt'undan ÇIKAR; her turun **son mesajı** olarak koy (küçük,
trailing bir not). Böylece system + query/context + tur-geçmişi prefix'i turlar arası byte-aynı
kalır; yalnız en sondaki minik mesaj değişir (cache onu zaten ucuz değerler).

- `_assemble_messages`: `system = load_system_prompt(cfg)` (sayaç YOK). Sayacı `_feedback_message`
  ile birleşik ya da ayrı trailing mesaj olarak `head + tail + [counter_msg]` sonuna ekle.
- Model sayacı HÂLÂ görür (yalnız sonda) → tur kararı davranışı korunmalı.

**Kabul (a):**
- [ ] Sistem mesajı içeriği turlar arası **byte-özdeş** (test: iki tur, system content ==).
- [ ] Sayaç hâlâ modele iletiliyor (son mesajda), değeri her tur doğru.
- [ ] İterasyon dağılımı değişmedi (model erken submit / aşırı iterasyon yapmıyor) — kısa smoke.

---

## 2. Parça (b) — append-only bağlam + `_apply_budget`  [DAVRANIŞ-DOKUNUR, k=3 KARNE ŞART]

Mesaj 2'deki bağlam (a)'dan sonra kalan tek cache-kırıcı: `retrieved` büyüdükçe blok kümesi ve
numaraları değişiyor. Hedef: **önceden gösterilen blokların numarası SABİT kalsın (asla
yeniden-numaralama/tahliye), yeni bloklar daha yüksek numarayla APPEND edilsin.**

Kısıtlar / dikkat:
- `_apply_budget` (context_builder.py:185): şu an en düşük `(score, -order)` bloğu tahliye ediyor
  ve `_render` konuma göre `[1],[2]…` numaralıyor → tahliye HEPSİNİ yeniden numaralıyor. Append-only
  için: zaten gösterilmiş blokları **evict etme**; tahliye yalnız yeni adaylardan; numaralar monoton.
- Bağlamı ilk turda TAMAMEN dondurmak YANLIŞ olur: ajan sonraki turlarda yeni chunk çekerse onları
  `[n]` ile cite edemez (numaralı blokta yok). Doğru tasarım append-only, freeze değil.
- Veri akışı: retrieval tool sonuçları zaten tail'e giriyor (tools_node). En temiz append-only
  tasarımı Sonnet kodda netleştirir; ilke: **gösterilen bağlamın önceki bytes'ı hiç değişmesin.**

**Kabul (b):**
- [ ] Gösterilmiş blok numaraları turlar arası sabit; yeni bloklar yalnız SONA, yeni numarayla.
- [ ] Mesaj 2'nin önceki bytes'ı turlar arası değişmiyor (prefix cache'i kırmıyor).
- [ ] **k=3 golden karne**: faithfulness, context_precision, honesty, fallback oranı — baseline'a
      göre **regresyon YOK**. (Bağlam tahliyesi değiştiği için zorunlu; M-15 notu bunu şart koşar.)
- [ ] Bütçe aşımı davranışı tanımlı: budget dolduğunda append durur / en eski-yeni aday düşer
      (gösterilen asla düşmez) — deterministik ve belgeli.

---

## 3. Ölçüm — gerçek döngüde doğrula (prob sentetikti)

- (a)+(b) sonrası gerçek agent koşumunda **tur başına prompt-eval** düşüşünü ve **p50 uçtan-uca**
  iyileşmeyi ölç (beklenen ~2s). Prob mekanizmayı kanıtladı; bu, canlı döngüde gerçekleştiğini
  doğrular.
- (a) tek başına ne kazandırıyor, (a)+(b) ne kazandırıyor — ayrı raporla (a'yı önce merge etmek
  meşru; b karneyi bekler).

---

## 4. Riskler / dürüst sınırlar

- **Eşzamanlılık cache'i paylaşır.** Ollama prefix cache'i tek model-slotudur; ikinci kullanıcının
  isteği birincinin cache'ini düşürebilir. Yani ~2s kazanç **sıralı/tek-kullanıcı** yükünde gerçek;
  eşzamanlılıkta erir. Bu M-15'in "kapasite ayrı kalem" ilkesiyle tutarlı — aşırı iddia etme.
- **Sayacın işlevi.** Model `[Kalan iterasyon: N]`'i tur bütçesini bilmek için kullanıyor; tail'e
  taşırken görünürlüğü korunmalı (kabul-a smoke).

---

## 5. Kapsam çiti

Değişen: `agents/nodes/agent.py` (`_assemble_messages`) + `retrieval/context_builder.py`
(`_apply_budget`/`_render` append-only) + testler. **DIŞINDA:** `compose`/grounding zinciri
(M-16 mührü), honesty ölçütü (harness), tool şemaları. Oralarda diff çıkarsa dur ve sor.

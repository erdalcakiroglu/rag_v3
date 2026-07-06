# FAZ 2 — Teknik İş Planı: Observability & Evaluation Temeli

**Versiyon:** 0.1 · **Tarih:** 2026-07-02
**Ön koşul:** FAZ 1 kod tamam (İP-0..10). FAZ 1 resmi kapanışı (canlı korpus koşusu) FAZ 2 ile paralel yürüyebilir.
**Amaç:** FAZ 3'ün (retrieval) hiçbir kararı ölçümsüz verilmesin: golden set + tracing + RAGAS harness bu fazda kurulur (ADR-004).
**Çalışma modeli:** FAZ 1 ile aynı — kod VS Code'da (Sonnet), spesifikasyon/karar burada.

---

## İP-2.0 — Tracing Platform Kararı ✅ KARAR VERİLDİ (2026-07-02)

**Karar (ADR-013):** Langfuse v3, Podman ile (container kullanımı onaylandı), DB sunucusunda (192.168.36.15). Kurulum rehberi: `Kurulum_RHEL_Langfuse_Podman.md`. Port çakışması önlemi: Langfuse'un iç postgres/redis'i host'a port açmaz. Kurulumu Erdal yapar; İP-2.2 kodu `RAGINTEL_LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY` env değişkenlerini bekler.

Aşağıdaki karşılaştırma tarihçe olarak korunmuştur:

FAZ 2'nin tek açık mimari kararı. İki aday:

| | Langfuse (self-hosted) | Arize Phoenix (self-hosted) |
|---|---|---|
| Kurulum | **Container gerektirir** (v3: postgres+clickhouse+redis+minio bileşimi) — bare-metal kurulum pratik değil | `pip install arize-phoenix` — tek süreç, bare-metal dostu, RHEL'de sorunsuz |
| Güç | Prompt yönetimi, kullanıcı feedback UI, LLM-as-judge entegrasyonu, olgun dashboard | OTel-native tracing, eval görselleştirme; prompt yönetimi zayıf |
| RAGAS | Entegre | Entegre |
| Veri egemenliği | Self-host ✓ | Self-host ✓ |

**Öneri:** Kurumda container çalıştırma imkânı (Podman dahil — RHEL'de yerleşiktir, Docker gerekmez) VARSA Langfuse; yoksa Phoenix ile başla (OTel enstrümantasyonu ortak olduğundan sonradan Langfuse'a geçiş ucuz — ADR-003'teki soyutlama mantığının aynısı).

**Karar için gereken bilgi:** Podman/container kullanımına kurum politikası izin veriyor mu? Langfuse hangi sunucuya kurulur (DB sunucusu mu, Ollama sunucusu mu, ayrı mı)?

## İP-2.1 — Türkçe Golden QA Seti v1

**Amaç:** Tüm retrieval/model kararlarının hakemi olacak, versiyonlu soru-cevap seti.

**Format ve saklama:**

- Repo'da JSONL (`eval/golden/v1.jsonl`) — git ile versiyonlu, PR ile değişir; DB'ye loader ile yüklenir (rapor/harness okuması için).
- Kayıt şeması: `{id, question, ideal_answer, category, difficulty(1-3), gold_evidence:[{file_name, page|sheet, quote}], doc_scope, answerable(bool), created_by, notes}`
- `gold_evidence.quote` normalize edilebilir olmalı (FAZ 4 quote doğrulamasıyla aynı fonksiyon) — evidence, chunk'a eşlenebilir olmalı.

**Kompozisyon (hedef ≥100 soru):**

| Kategori | Oran | Amaç |
|---|---|---|
| Tekil fakt (tek chunk yeter) | ~%30 | Temel retrieval doğruluğu |
| Sentez (aynı dokümanda çok chunk) | ~%20 | Context builder kalitesi |
| Multi-hop (dokümanlar arası) | ~%15 | FAZ G tetik ölçümü — kritik |
| Tablo-kaynaklı | ~%15 | Docling/tablo chunk kalitesi |
| Cevapsız (korpusta yok — negatif) | ~%10-15 | Halüsinasyon/kaçamak ölçümü |
| Sayfa/citation hassas | ~%10 | Citation zinciri doğrulaması |

**Üretim süreci (LLM destekli, insan onaylı):**

1. Korpustan örneklenmiş chunk'lardan güçlü bir modelle (Ollama qwen3.5:35b / llama3.3:70b) taslak soru-cevap üretimi — kategori kotalarına göre.
2. İnsan (Erdal) onayı: her taslak kabul/düzelt/ret — **onaysız kayıt sete giremez**.
3. Negatif sorular elle yazılır (LLM'e bırakılmaz — korpusta "gerçekten olmayan"ı insan bilir).

**Kabul kriterleri:**

- [ ] ≥100 onaylı kayıt, kategori kotaları ±%5 içinde; tamamı Türkçe.
- [ ] Her `gold_evidence` gerçek korpus dosyasına/sayfasına işaret eder (loader doğrular: file_name mevcut, quote normalize eşleşmesi bulunur).
- [ ] JSONL şema validasyonu (pydantic) CI'da; duplicate soru kontrolü.
- [ ] Loader: set DB'ye idempotent yüklenir, versiyon etiketi taşır.

## İP-2.2 — Tracing Entegrasyonu (OTel)

**Kurallar:**

- Langfuse'un projedeki rolü ve `metrics_ingestion` ile iş bölümü: proje dokümanı Bölüm 7e (brief bağlamına dahil edilir).
- OpenTelemetry SDK ile enstrümantasyon — platform-bağımsız (İP-2.0 kararı ne olursa olsun aynı kod): ingestion pipeline adımları, embed çağrıları (istek/batch/süre), ileride retrieval/agent (FAZ 3-4 aynı altyapıyı kullanır).
- Trace kimliği: `file_id` (ingestion) / `session_id` (FAZ 4'te) — structlog alanlarıyla tutarlı.
- `metrics_ingestion` KALIR (kalıcı, dosya-granül kayıt); tracing akış/debug görünümü sağlar — çakışma yok (veri sözlüğü notu).

**Kabul kriterleri:**

- [ ] Uçtan uca bir ingest koşusu, seçilen platformda adım adım trace olarak görünür (parse→clean→injection→chunk→embed→store span'leri, süre ve detail attribute'larıyla).
- [ ] Ollama embed çağrıları ayrı span (istek sayısı, batch, retry görünür).
- [ ] Platform kapalıyken pipeline ETKİLENMEZ (tracing fire-and-forget; export hatası log'lanır, iş durmaz).

## İP-2.3 — RAGAS Harness + CI Eval Gate (iskelet)

**Kurallar:**

- RAGAS metrikleri: faithfulness, answer_relevancy, context_precision, context_recall. Judge LLM: **Ollama üzerinden** (qwen3.5:35b — dış API yok, veri egemenliği). Embedding metrikleri için `bge-m3@ollama` (ADR-012 tutarlılığı).
- Harness girdisi standart format: `{question, answer, contexts[], ground_truth}` — FAZ 3 retrieval çıktısı bu formata dönüştürülür (adapter FAZ 3'te).
- FAZ 2'de harness "kuru koşu" ile doğrulanır: golden set + elle hazırlanmış 5-10 örnek yanıt/context üzerinde metrikler hesaplanır (retrieval henüz yok).
- CI gate iskeleti: eşikler config'ten; FAZ 2'de "rapor" modunda (fail etmez), FAZ 3'ten itibaren zorunlu.

**Kabul kriterleri:**

- [ ] Kuru koşu: örnek set üzerinde 4 metrik hesaplanır, sonuçlar tekrarlanabilir aralıkta (judge nondeterminizmi için: temperature=0 + 3 koşu medyanı).
- [ ] Judge çağrıları Ollama'ya gider (dış API çağrısı YOK — test ağ izolasyonuyla kanıtlar).
- [ ] `python -m ragintel.eval run --golden v1 --mode report` çalışır; sonuç JSON + özet tablo üretir.

## İP-2.4 — Retrieval Benchmark Düzeneği (FAZ 3 hazırlığı)

**Kurallar:**

- Golden set'in `gold_evidence`'ı retrieval ground-truth'a dönüştürülür: her soru için "bulunması gereken chunk_id seti" (quote→chunk eşlemesi, normalize fonksiyonuyla).
- Metrikler: recall@k, MRR, nDCG (k=5,10,20) — RAGAS'tan bağımsız, hızlı, deterministik (judge gerektirmez).
- FAZ 3'te vector vs hybrid vs rerank varyantları BU düzenekle karşılaştırılır.

**Kabul kriterleri:**

- [ ] Quote→chunk eşleme oranı raporlanır (eşleşmeyen evidence = golden set hatası → İP-2.1'e geri bildirim).
- [ ] Sentetik doğrulama: doğru chunk'lar elle verildiğinde recall@k=1.0 (düzenek kendini test eder).

---

## Ek-B — İP-2.1b: Soru Üretimi Spesifikasyonu (Ollama erişimi sonrası)

**Ön koşul:** FAZ 1 kapanış koşusu tamamlanmış (gerçek korpus COMPLETED), İP-2.1a loader hazır.

**Üretim hattı:**

1. **Örnekleme:** Korpustan chunk örneklemesi — dosya başına en fazla 3 chunk (çeşitlilik), `quality_score` düşük dosyalardan örnekleme YAPILMAZ (kötü parse'tan soru üretmek golden set'i zehirler). Tablo kategorisi için `core_tables` kayıtlarından örnekleme.
2. **Taslak üretimi:** Ollama `qwen3.5:35b`, temperature 0.3. Chunk (+ multi-hop için 2-3 ilişkili chunk) verilir, kategori kotasına göre soru-cevap taslağı istenir.
3. **İnsan onayı:** Her taslak CLI/basit arayüzle kabul/düzelt/ret — onaysız kayıt sete GİREMEZ. Ret oranı raporlanır (üretim prompt'unun kalite göstergesi).
4. **Negatifler elle:** "Cevapsız" kategorisi LLM'e bırakılmaz — korpusta gerçekten olmayanı insan bilir.

**Üretim prompt'u kuralları (Sonnet implemente ederken):**

- Soru, chunk'ı GÖRMEDEN anlamlı olmalı ("Bu paragrafta ne anlatılıyor?" YASAK — gerçek kullanıcı sorusu gibi: "X süreci hangi durumda Y gerektirir?").
- Cevap chunk'tan doğrulanabilir olmalı; `gold_evidence.quote` chunk içinden birebir alıntı.
- Soru cevabı içermemeli (leak kontrolü); Türkçe, kurum terminolojisiyle.
- Multi-hop: iki chunk'ın BİRLEŞTİRİLMEDEN cevaplanamayacağı sorular (tek chunk yeterliyse kategori düşürülür).
- Her taslakta model kendi kendine kontrol listesi doldurur (answerable? leak? category-fit?) — insan onayını hızlandırır.

**Kabul kriterleri:**

- [ ] Kota tablosuna uygun ≥100 onaylı kayıt (İP-2.1 tablosu).
- [ ] Onay oturumu kayıtlı: kabul/düzelt/ret sayıları + ret nedenleri raporda.
- [ ] Loader'dan geçen set üzerinde evidence→chunk eşleşme oranı ≥ %95 (İP-2.4 düzeneğiyle).

## Sıra ve Bağımlılıklar

```
İP-2.0 (karar) ──► İP-2.2 (tracing)
İP-2.1 (golden set) ──► İP-2.3 (RAGAS kuru koşu) ──► İP-2.4 (benchmark düzeneği)
```

İP-2.1 insan-onay döngüsü içerdiğinden en uzun süren pakettir — İLK başlatılmalı; İP-2.2 paralel gider. FAZ 1 canlı korpus koşusu golden set üretiminin ön koşuludur (sorular gerçek korpustan üretilir) → ağ istisnası kritik yolda.

## FAZ 2 Çıkış Kriterleri

- Golden set v1 (≥100, onaylı) repo'da versiyonlu + loader'la DB'de.
- Uçtan uca ingest trace'i platformda görünür; pipeline platformsuz da çalışır.
- RAGAS kuru koşu tekrarlanabilir; eval CLI çalışır; judge=Ollama kanıtlı.
- Retrieval benchmark düzeneği sentetik doğrulamadan geçmiş.

## Değişiklik Günlüğü

| Tarih | Versiyon | Değişiklik |
|---|---|---|
| 2026-07-02 | 0.1 | İlk sürüm: İP-2.0..2.4, golden set metodolojisi, platform karar tablosu. |

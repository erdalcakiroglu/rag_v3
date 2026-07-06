# FAZ 3 — Teknik İş Planı: Retrieval Layer

**Versiyon:** 0.1 · **Tarih:** 2026-07-03
**Ön koşullar:** FAZ 1 kapalı ✓ · İP-2.1a/2.2 kabul ✓ · Golden set (İP-2.1b) ve benchmark düzeneği (İP-2.4) → yalnızca TUNING paketleri için gerekli
**Amaç:** Sorgu → ilgili chunk seti zincirinin kurulması. FAZ 4 tool kontratlarının (Tasarim_FAZ4 Bölüm 3) gerçeklenmesi.
**Çalışma modeli:** FAZ 1-2 ile aynı — kod VS Code'da, spesifikasyon/karar burada.

**Temel ilke (ADR-004/011):** Altyapı paketleri (İP-3.0..3.5) golden set OLMADAN yazılabilir ve sentetik testlerle doğrulanır. Tuning paketi (İP-3.6) golden set + İP-2.4 düzeneğini bekler. Hiçbir varsayılan konfigürasyon "nihai" değildir — nihai değerleri İP-3.6 ölçümü belirler.

---

## Uygulama Sırası

```
İP-3.0 (arayüz iskeleti — FAZ 4 kontratlarına hizalı)
  → İP-3.1 (vector search) → İP-3.2 (hybrid + RRF) → İP-3.4 (context builder)
  → İP-3.3 (rerank — KARAR bekliyor) ─┘
İP-3.5 (RBAC filtresi) → İP-3.1'den itibaren her pakete gömülü (sonradan eklenmez!)
İP-3.6 (tuning/benchmark) → golden set + İP-2.4 hazır olunca; H200 sonrası
```

## İP-3.0 — Retrieval Servis Arayüzü

**Amaç:** FAZ 4 tool kontratlarıyla birebir hizalı, ölçülebilir retrieval API'si.

**Kurallar:**

- Arayüzler `Tasarim_FAZ4_Agentic_Loop.md` Bölüm 3'teki kontratların implementasyonudur — imzalar DEĞİŞMEZ: `search_hybrid`, `search_vector`, `lookup_document`, `rerank`. Dönüş tipi `RetrievedChunk` (chunk_id, text, score, source{file_id, file_name, page, section, version}, retrieval_method).
- Her arayüz `user_ctx` parametresi alır (runtime enjeksiyon — LLM parametresi değil; FAZ 4 güvenlik kuralı şimdiden uygulanır).
- Tool'lar MCP-uyumlu tanımlanır (ADR-008) ama FAZ 3'te yerel Python çağrısı yeterli; MCP server sarmalaması FAZ 4'te.
- Her çağrı OTel span üretir (İP-2.2 altyapısı): sorgu, k, filtre, süre, dönen chunk sayısı.
- Sorgu embedding'i AYNI backend'den (`bge-m3@ollama`, ADR-012) — korpusla tutarlılık zorunlu; başka backend'le sorgu embed'i YASAK.

**Kabul:** Arayüz imzaları FAZ 4 tasarımıyla diff'lenebilir şekilde bire bir; mock store ile sözleşme testleri; her çağrıda span.

## İP-3.1 — Vector Search

**Kurallar:**

- pgvector HNSW, cosine (`<=>`), `top_k` config'ten (üst sınır 20 — FAZ 4 bütçe kuralı).
- Metadata filtreleri: file_type, language, date aralığı, section_title — SQL WHERE, index'li kolonlar üzerinden.
- `doc_scope` filtresi HER SORGUDA zorunlu (İP-3.5 kuralı — opsiyonel parametre DEĞİL).
- `ef_search` config'ten (`SET LOCAL hnsw.ef_search`) — recall/hız dengesi İP-3.6'da ölçülür.
- Sorgu embedding'i tek istek (batch değil); Ollama erişilemezse anlaşılır hata (İP-7 deseni).

**Kabul:** Sentetik korpusta bilinen-cevaplı 10 sorgu → beklenen chunk'lar top-k içinde; filtre kombinasyon testleri; p95 sorgu süresi raporu (embedding dahil/hariç ayrı).

## İP-3.2 — Hybrid Search (RRF)

**Kurallar:**

- İki bacak: dense (İP-3.1) + sparse (`ts_rank_cd(tsv, plainto_tsquery('simple', norm_query))`).
- Füzyon: RRF — `score = Σ 1/(rrf_k + rank_i)`, `rrf_k` config'ten (varsayılan 60). Ağırlıklı varyant (`α·dense + β·sparse`) da implemente edilir, İP-3.6 karşılaştırır.
- Sorgu normalize edilir (`normalize_for_quote` DEĞİL — arama için ayrı hafif normalize: lowercase+unicode; fonksiyon `ragintel.text`'e eklenir, adlandırma net ayrışır: `normalize_for_search`).
- Türkçe stemming SORUNU bilinçli açık bırakılır: `simple` config kelime çekimlerini yakalamaz ("vergisi" ≠ "vergi"). İP-3.6'da ölçülecek alternatifler: (a) `simple` + sorgu genişletme yok (baseline), (b) `unaccent` eklentisi, (c) pg_trgm benzerlik bacağı. Kod üç varyantı config'ten seçebilir yapıda yazılır.
- İki bacak tek SQL'de (CTE) — iki ayrı round-trip değil.

**Kabul:** Sentetik testler: yalnız-dense'in bulduğu, yalnız-sparse'ın bulduğu ve ikisinin de bulduğu sorgu senaryoları; RRF sıralama doğruluğu elle hesaplanmış beklenen değerlerle; `rrf_k`/ağırlık config testi.

## İP-3.3 — Rerank Servisi ⚠️ KARAR BEKLİYOR

**Bağlam:** bge-reranker-v2-m3 bir cross-encoder — Ollama rerank API'si sunmuyor; torch da projeden çıkarıldı (ADR-012). Serving seçenekleri:

| Seçenek | Artı | Eksi |
|---|---|---|
| **A) TEI (text-embeddings-inference) container — H200 sunucusunda** (önerilen) | GPU hızı; Podman zaten kurumda onaylı; OpenAI-uyumlu `/rerank` API | H200 sunucusuna kurulum erişimi gerekli; sorgu yolunda uzak HTTP çağrısı (Ollama için zaten kabul edildi) |
| B) TEI container — DB sunucusunda (CPU) | Erişim sorunu yok | CPU'da cross-encoder yavaş (~100-300ms/10 doküman) — yine de kabul edilebilir olabilir |
| C) Rerank'siz başla | Sıfır iş; hybrid+RRF çoğu senaryoda güçlü | Rerank katkısı ölçülemez |

**Önerim:** İP-3.3'ü **iki aşamalı** yap: (1) `rerank` arayüzü + "passthrough" implementasyon (skorları değiştirmeden döner) şimdi yazılır — FAZ 4 kontratı yerine oturur; (2) TEI kurulumu (A veya B) yapılınca gerçek implementasyon eklenir ve İP-3.6 rerank'in katkısını ölçer — katkı anlamlı değilse passthrough kalır (maliyet/latency tasarrufu).

**Kabul (aşama 1):** Passthrough rerank kontrat testleri; backend seçimi config'ten. **(Aşama 2):** TEI'ye karşı canlı smoke; skor sıralamasının değiştiğinin kanıtı.

## İP-3.4 — Context Builder

**Kurallar:**

- Girdi: sıralı `RetrievedChunk` listesi; çıktı: LLM'e verilecek bağlam + citation haritası (FAZ 4 `Citation` kontratına hazır).
- Token bütçesi config'ten (LLM'in tokenizer'ıyla sayım — embedding tokenizer'ı DEĞİL; LiteLLM/model bilgisinden).
- Dedup: aynı chunk iki yöntemle geldiyse tek kopya (yüksek skor kazanır).
- Komşu birleştirme: aynı dosyadan ardışık chunk'lar (chunk_index bitişik) tek bloğa birleştirilir — kaynak bilgisi korunur.
- Her bağlam bloğu kaynak etiketi taşır: `[n] file_name, sayfa/sheet, section` — FAZ 4 citation'ının hammaddesi.
- Düşük `quality_score`'lu dosyadan gelen chunk'lar işaretlenir (FAZ 4 confidence hesabına girdi — Ek-A bağlantısı).

**Kabul:** Bütçe aşımında düşük skorlu chunk düşer (sıralama korunur); dedup ve komşu-birleştirme senaryo testleri; citation haritası eksiksizliği (her blok → kaynak).

## İP-3.5 — RBAC-Aware Retrieval (kesişen kural)

**Kurallar:**

- `doc_scope` filtresi İP-3.1/3.2'nin SQL'ine gömülüdür — ayrı paket değil, her sorgunun zorunlu parçası. `user_ctx.allowed_doc_scopes` → `WHERE doc_scope = ANY(...)`.
- Boş/eksik scope listesi = SIFIR sonuç (fail-closed; "hepsini görür" varsayımı YASAK).
- Postgres RLS'e geçiş FAZ 6'da değerlendirilir; şimdilik uygulama katmanı filtresi + bunu kanıtlayan testler.
- Test zorunluluğu: scope dışı dokümanın hiçbir arama yöntemiyle (vector/hybrid/lookup) sızmadığı senaryolar.

**Kabul:** Fail-closed testi; iki farklı scope'lu sentetik korpusta çapraz sızıntı = 0.

## İP-3.6 — Tuning & Benchmark (golden set sonrası)

**Ön koşul:** İP-2.1b golden set + İP-2.4 düzeneği + (tercihen) H200.

**Ölçülecek varyant matrisi (İP-2.4 metrikleriyle: recall@k, MRR, nDCG):**

1. vector vs hybrid-RRF vs hybrid-ağırlıklı
2. Rerank katkısı (İP-3.3 aşama 2 yapıldıysa): hybrid vs hybrid+rerank
3. Türkçe sparse varyantları: simple / unaccent / +pg_trgm
4. `ef_search`, `top_k`, `rrf_k` taraması
5. **Chunking revizyonu (FAZ 1 truncation bulgusu):** max_tokens 512 vs 384 vs section-split iyileştirmesi — korpus yeniden işlenerek (REPROCESS) karşılaştırılır
6. Embedding modeli doğrulaması: bge-m3 baseline; anlamlı şüphe doğarsa Qwen3-Embedding karşılaştırması (yeni korpus embed'i gerektirir — maliyetli, ancak gerekçeyle)

**Kabul / FAZ 3 çıkış kriterleri:**

- [ ] Golden set üzerinde context precision ≥ 0.80 (RAGAS, İP-2.3 harness'iyle)
- [ ] Hybrid'in tek başına vector'e karşı ölçülebilir üstünlüğü kanıtlı (değilse gerekçeli sadeleşme kararı)
- [ ] Retrieval p95 < 1 sn (sorgu embed dahil, rerank hariç; CPU Ollama ile geçici hedef 2 sn)
- [ ] Seçilen konfigürasyon `app_config`'e yazıldı ve rapor Langfuse'ta kayıtlı
- [ ] Multi-hop kategorisi ayrı raporlanır → **FAZ G tetik değerlendirmesinin girdisi**

---

## Şimdi Yazılabilir / Bekleyen Ayrımı

| Paket | Golden set gerekir mi? | Not |
|---|---|---|
| İP-3.0, 3.1, 3.2, 3.4, 3.5 | HAYIR — sentetik testlerle şimdi | H200 de gerekmez (CPU Ollama sorgu embed'i için yeterli) |
| İP-3.3 aşama 1 (passthrough) | HAYIR | Aşama 2 TEI kurulum kararına bağlı |
| İP-3.6 | EVET | + İP-2.4 düzeneği; H200 tercihen |

## Açık Kararlar

1. **İP-3.3 rerank serving:** TEI@H200 mi, TEI@DB-sunucusu (CPU) mu, şimdilik passthrough mu? (Önerim: passthrough ile başla, TEI kararını H200 istisnasıyla birlikte ver.)
2. FAZ 3 sırasında minimal grounding check taslağı (yol haritasında FAZ 3'te başlar): İP-3.4'ün citation haritası hazır olduğundan, FAZ 4 validate node'unun 1-2 kontrolü erken prototiplenebilir — İP-3.4 bitince karar.

## Değişiklik Günlüğü

| Tarih | Versiyon | Değişiklik |
|---|---|---|
| 2026-07-03 | 0.1 | İlk sürüm: İP-3.0..3.6, rerank karar tablosu, şimdi/bekleyen ayrımı, çıkış kriterleri. |

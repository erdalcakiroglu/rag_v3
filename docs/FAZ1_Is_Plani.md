# FAZ 1 — Teknik İş Planı ve İş Paketleri

**Versiyon:** 0.1 · **Tarih:** 2026-07-02
**Çalışma modeli:** Kod VS Code'da (Claude Sonnet 5) yazılır. Bu doküman her iş paketinin spesifikasyonu, kabul kriterleri ve bağımlılıklarıdır. Her paket, kod asistanına doğrudan brief olarak verilebilir.
**Referans dokümanlar (brief'e eklenecek bağlam):** `FAZ1_Sema.sql` · `Tasarim_FAZ4_Agentic_Loop.md` (Bölüm 9) · `RAG_v2_Proje_Dokumani.md` (7b)

---

## Uygulama Sırası ve Bağımlılık Grafiği

```
İP-0 (iskelet)
  → İP-1 (intake) → İP-2 (parse) → İP-3 (cleaning) → İP-5 (chunking) → İP-7 (embedding) → İP-8 (storage)
                          │              │                                      
                          └─ İP-4 (injection scan, İP-3'ten sonra herhangi bir noktada)
İP-6 (metadata) → İP-5 ile birlikte ilerler
İP-9 (QC + metrik) → her paketin içine yayılır, sonda konsolide edilir
İP-10 (orchestrator + CLI + testler) → İP-1'den itibaren kademeli büyür
```

Önerilen sprint dilimi: **Dilim A** = İP-0,1,2 (uçtan uca "parse edilen dosya DB'de görünür") · **Dilim B** = İP-3,5,6 · **Dilim C** = İP-4,7,8 · **Dilim D** = İP-9,10 konsolidasyonu.

---

## İP-0 — Proje İskeleti ve Konfigürasyon

**Amaç:** Tüm paketlerin üzerine oturacağı repo yapısı ve config-first altyapı.

**Kapsam ve kurallar:**

- Dizin yapısı `Tasarim_FAZ4_Agentic_Loop.md` Bölüm 7 ile uyumlu (`ragintel/` paketi; FAZ 1'de `ingestion/`, `database/`, `config/`, `observability/` alt modülleri).
- Config öncelik zinciri: DB (`ragintel.app_config`) → env → varsayılan. Pydantic-settings ile tip güvenli.
- Bağlantı yönetimi: `psycopg` v3, tek connection pool, context manager pattern. Conninfo `search_path=ragintel,public` (İP-8 düzeltmesi — pgvector uzantı tipleri public şemasındadır, yalnız `ragintel` verilirse vector tipi bulunamaz).
- Loglama: `structlog`, JSON formatı, `file_id`/`trace_id` alanları her kayıtta.
- `pyproject.toml`: Python 3.11+, bağımlılıklar 7b tablosundaki listeden.

**Kabul kriterleri:**

- [ ] `python -m ragintel.config show` mevcut efektif konfigürasyonu (kaynak bilgisiyle: db/env/default) yazdırır.
- [ ] DB bağlantı hatasında anlaşılır hata + retry (3 deneme, exponential backoff).
- [ ] `pytest` iskeleti çalışıyor; config öncelik zinciri için en az 3 test.

---

## İP-1 — Dosya Kabul ve Envanter (Folder Scanner)

**Amaç:** İzlenen klasördeki dosyaları güvenli şekilde `core_files`'a kaydetmek. (v1 `file_operations.py` mantığı taşınır.)

**Kurallar:**

- Kabul: pdf/docx/xlsx/txt; tip doğrulama uzantıyla DEĞİL `python-magic` (içerik) ile.
- sha256 checksum; `(checksum, doc_version)` çakışmasında: aynı checksum → SKIP (log'la), aynı isim farklı checksum → `doc_version + 1`.
- Boyut limiti `app_config.ingestion.max_file_mb`; aşan dosya `FAILED` + `fail_reason`.
- Raw dosya storage'a kopyalanır (`source_path`); orijinal klasör asla değiştirilmez.
- Kayıt `status = PENDING` ile açılır. İşlem idempotent: scanner iki kez çalışırsa ikinci tur no-op.

**Kabul kriterleri:**

- [ ] 100 dosyalık karışık klasörde: geçerliler PENDING, duplicate'ler SKIP, bozuk uzantılılar doğru tiple veya FAILED.
- [ ] Aynı dosya yeniden kopyalandığında ikinci kayıt AÇILMAZ.
- [ ] Değişmiş içerikli aynı isimli dosya `doc_version=2` olarak kaydedilir.
- [ ] Her dosya için `metrics_ingestion` kaydı (step bazlı süre).

---

## İP-2 — Parse Adaptörü (Docling)

**Amaç:** Dört dosya tipini ortak bir ara temsile (ParsedDocument) dönüştürmek.

**Kurallar:**

- Birincil parser Docling (pdf, docx). xlsx: `openpyxl` (sheet→tablo), txt: `charset-normalizer` ile decode.
- **ParsedDocument kontratı** (downstream'in tek girdisi — değişmez):
  - `pages[]`: {page_no, text_blocks[]}
  - `sections[]`: {title, level, page_start, char_span}
  - `tables[]`: {page_no|sheet_name, index, data(jsonb-uyumlu), flattened_text}
  - `figures[]`: {page_no, index, caption}
  - `language` (lingua ile tespit), `parse_warnings[]`
- OCR: Docling'in dahili OCR'ı yalnızca "metin katmanı yok" tespitinde devreye girer (config flag).
- Parse hatası → `status=FAILED`, `fail_reason` dolu, exception yutulmaz loglanır; pipeline diğer dosyalarla devam eder.
- Zaman aşımı: dosya başına config'ten (varsayılan 300 sn).

**Kabul kriterleri:**

- [ ] Temsili korpus (min. 5 pdf — en az 1 taranmış, 3 docx, 2 xlsx, 2 txt) hatasız ParsedDocument üretir.
- [ ] Tablolu PDF'te tablolar `tables[]`'a düşer, gövde metnine karışmaz.
- [ ] Sayfa numaraları ve section başlıkları korunur (citation zinciri için zorunlu — FAZ 4 kontratı).
- [ ] Parse süresi ve uyarılar `metrics_ingestion.detail`'e yazılır.

---

## İP-3 — Cleaning Entegrasyonu

**Amaç:** Deterministik cleaning'in ParsedDocument üzerine kurulması. **(Revize — İP-3 kararı):** v1 `document_cleaning.py` birebir TAŞINMAZ: v1'in stopword/noktalama silmesi embedding-only akış içindi; v2'de chunk_text LLM bağlamı + citation kaynağıdır, anlam ve yüzey formu korunur. v1'den yalnızca ilke (deterministik, metrik üreten temizlik) devralınır.

**Kurallar:**

- Deterministik temizlik: boş satır, header/footer tekrarı, kırık encoding (ftfy), gereksiz karakterler. Anlam yeniden yazılmaz.
- Tablo/şekil blokları İP-2'de zaten ayrıldı → v1'deki extraction mantığı sadeleşir; çakışma varsa İP-2 kazanır.
- Çıktı: `cleaned_text` + `cleaning_metrics` (kırpılan karakter, tespit edilen header/footer imzaları).
- Normalize fonksiyonu (`normalize_for_quote`) ortak kütüphaneye taşınır: lowercase, unicode NFKC, whitespace collapse — FAZ 4 quote doğrulaması AYNI fonksiyonu kullanacak.

**Kabul kriterleri:**

- [ ] Deterministik regresyon suiti (boş blok/junk/encoding/header-footer/anlam-korunur senaryoları) — v1 test taşıma yerine ileriye dönük set (İP-3 kararı).
- [ ] `normalize_for_quote` tek modülde, hem chunk yazımı hem (ileride) validation import edebiliyor.
- [ ] Metrikler `metrics_ingestion (step='clean')` kaydında.

---

## İP-4 — Injection Taraması (ADR-007 minimal set)

**Amaç:** İngest edilen içerikte indirect prompt injection kalıplarını işaretlemek.

**Kurallar:**

- **(Revize — torch'suz matris, ADR-012 sonucu):** Araç llm-guard DEĞİL (HF classifier → torch'u geri getirir); FAZ 1'de deterministik kural seti: injection kalıpları (TR+EN: "ignore previous/above instructions", "disregard", sistem-prompt taklidi, rol değiştirme kalıpları), gizli unicode (zero-width U+200B..200F, bidi U+202A..202E / U+2066..2069), anormal base64/homoglyph yoğunluğu. Kalıp listesi kodda varsayılan + `app_config('injection')` ile override (config zinciri). Model tabanlı tarama FAZ 6'da Llama Guard / Ollama-H200 üzerinden.
- Karar: BLOKLAMA YOK — `core_files.injection_flag = true` + `qc_findings('injection_suspect')`. Karar mekanizması FAZ 6'da.
- Chunk değil dosya düzeyinde bayrak; şüpheli span'ler `qc_findings.detail`'e.
- Çift girdi kuralı (İP-4 kararı): hidden-unicode taraması HAM (pre-clean) parsed metinde — İP-3 cleaning bu karakterleri nötralize ettiğinden cleaned_text'te iz kalmaz; pattern/base64/homoglyph cleaned_text'te. Orchestrator scanner'a ikisini de verir.
- Performans: temiz dosyada ek yük < %10 (ölç ve raporla).

**Kabul kriterleri:**

- [ ] İçine bilinen injection kalıpları ekilmiş 5 test dosyası flag'lenir; 20 temiz dosyada false-positive ≤ 1.
- [ ] Tarama süresi `metrics_ingestion (step='injection_scan')`.

---

## İP-5 — Chunking

**Amaç:** Section-aware, token-limitli, overlap'li chunk üretimi.

**Kurallar:**

- Strateji `app_config.chunking`'ten: varsayılan section-based; section yoksa paragraph fallback; ikisi de yoksa sliding window.
- Token sayımı embedding modelinin tokenizer'ı ile (BGE-M3/HF tokenizer) — tiktoken DEĞİL (model uyumu için).
- Sınırlar: `max_tokens=512`, `overlap=64`, `min_tokens=30` (altı → önceki chunk'a birleştir).
- Tablolar: `flattened_text` kendi chunk'ı olur, bölünmez; `core_tables` ile ilişkilendirilir.
- Her chunk: `chunk_text`, `chunk_text_norm` (İP-3 fonksiyonuyla), `token_count`, `page_number`/`sheet_name`, `section_title`, `char_start/end`.
- Deterministiklik: aynı girdi + aynı config → bit-bit aynı chunk seti (test edilebilirlik).

**Kabul kriterleri:**

- [ ] Sınır durum testleri: tek satırlık dosya, 500 sayfalık PDF, sadece tablo içeren xlsx, section'sız düz metin.
- [ ] Hiçbir chunk max_tokens'ı aşmaz; min altı chunk yalnızca dosyanın tamamı kısaysa oluşur.
- [ ] Overlap komşu chunk'larda doğrulanabilir (char_span kesişimi).
- [ ] Determinizm testi: iki çalıştırma aynı checksum'lı chunk seti üretir.

---

## İP-6 — Metadata Enrichment

**Amaç:** 1.7'deki alan setinin eksiksiz doldurulması.

**Kurallar:** file_name, file_type, page_number, sheet_name, section_title, language, upload_user, upload_date, doc_version — hepsi İP-1/İP-2 çıktılarından türetilir; ayrı LLM çağrısı YOK (MVP). Eksik alan `NULL` bilinçli bırakılır, uydurulmaz.

**Kabul kriterleri:**

- [ ] Örnek korpusta zorunlu alanların (file_name, file_type, language) doluluk oranı %100; sayfa/section doluluğu dosya tipine göre raporlanır.

---

## İP-7 — Embedding Servisi

**Amaç:** BGE-M3 ile batch embedding üretimi. **(Revize — ADR-012):** birincil backend remote Ollama HTTP (H200 sunucusu); yerel torch/FlagEmbedding bağımlılığı eklenmez.

**Kurallar:**

- Backend: Ollama `/api/embed`, model `bge-m3`, base URL config'ten (`RAGINTEL_OLLAMA_BASE_URL`). Dense 1024; sparse/colbert alınmaz ama arayüz kapıyı kapatmaz (FAZ 3).
- L2-normalize İSTEMCİ tarafında yapılır (norm=1 garantisi backend'den bağımsız; Ollama çıktısının normalize olup olmadığına güvenilmez).
- Tek korpus = tek backend: `core_vectors.model_name = 'bge-m3@ollama'` (damga zorunlu). FAZ 3 sorgu embedding'i aynı backend'i kullanır. FlagEmbedding/başka backend'le karışım YASAK.
- Batch: `app_config.embedding.batch_size` (varsayılan 64/istek); HTTP timeout/5xx'te retry (3, exponential backoff); kalıcı hatada batch yarıla ve devam et (OOM kuralının ağ karşılığı).
- Ollama erişilemezse: dosya FAILED değil, pipeline durur ve anlaşılır hata verir (embedding altyapı hatası dosya hatası değildir — retry İP-10 RETRY akışıyla).
- Kalite kontrolü: NaN/sıfır-norm vektör → `qc_findings('embed_failed')`, chunk atlanır, pipeline durmaz.
- Servis arayüzü: `embed_batch(texts: list[str]) -> list[vector]` — ileride vLLM/TEI/FlagEmbedding'e geçiş bu arayüzün arkasında kalır.

**Kabul kriterleri:**

- [ ] 1000 chunk'lık toplu işlem Ollama backend ile tamamlanır; süreler ve istek/batch sayıları `metrics_ingestion (step='embed').detail`'e. **(Revize 2 — ADR-012: backend Ollama/H200; CPU/GPU cihaz seçimi kuralı düştü, yerine ağ dayanıklılık kuralları geldi.)**
- [ ] Türkçe/İngilizce karışık metinde vektör normları ~1.0 (normalize doğrulaması).
- [ ] Ağ hatası simülasyonunda (timeout/5xx) retry + batch küçültme devreye girer, iş tamamlanır; Ollama tamamen erişilemezken dosya FAILED olmaz, pipeline anlaşılır hatayla durur.

---

## İP-8 — Storage Yazımı (Transactional)

**Amaç:** Chunk + vektör + tablo/şekil + metriklerin tutarlı yazımı.

**Kurallar:**

- Dosya başına tek transaction: chunks + vectors + tables + figures ya HEP ya HİÇ. Başarıda `status=COMPLETED`, hatada rollback + `FAILED`.
- REPROCESS akışı: aynı file_id için eski chunk/vector'ler silinir (CASCADE), yenileri yazılır — yarım durum kalamaz.
- Vector insert: `psycopg` binary/COPY ile toplu (satır satır INSERT değil — 10x fark).
- HNSW index mevcutken yazım kabul edilebilir; toplu ilk yükte (>100k chunk) index'i drop/recreate stratejisi config flag'i olarak bulunur.

**Kabul kriterleri:**

- [ ] Yazım ortasında öldürülen süreç (kill -9 testi) sonrası DB'de yarım dosya yok: ya COMPLETED ya da eski durumda.
- [ ] REPROCESS sonrası chunk_id'ler yenilenir, sayılar tutarlı, öksüz vektör yok (sorguyla kanıtla).
- [ ] 10k chunk yazımı < 60 sn (COPY yolu, vektörler hazırken).

---

## İP-9 — QC ve Metrik Konsolidasyonu

**Amaç:** FAZ 1.12/1.14'ün tamamlanması; yol haritası çıkış kriterinin ölçülebilir hale gelmesi.

**Kurallar:**

- QC taramaları: empty/duplicate/too_short/too_long chunk (config eşikleri), parse/embed hataları → `qc_findings`.
- Duplicate chunk tespiti: `chunk_text_norm` hash'i ile dosya İÇİ; dosyalar arası duplicate raporlanır ama silinmez.
- Özet rapor komutu: dosya bazlı durum, adım süreleri, chunk istatistikleri, açık QC bulguları — tek SQL/CLI çıktısı.

**Kabul kriterleri:**

- [ ] `python -m ragintel.report ingestion` → korpus özeti: N dosya, başarı oranı, ort. süreler, bulgu sayıları.
- [ ] FAZ 1 çıkış kriteri ölçülebilir: "≥%95 parse başarısı" bu rapordan okunur.

---

## İP-10 — Orchestrator, CLI ve Test Konsolidasyonu

**Amaç:** Uçtan uca akışın tek komutla, kaldığı yerden devam edebilir şekilde çalışması.

**Kurallar:**

- Durum makinesi: PENDING → PROCESSING → COMPLETED/FAILED; RETRY: `retry_count < 3` olan FAILED'ler; REPROCESS: elle tetiklenir.
- REPROCESS başlangıç noktası: intake'te FAILED olmuş dosyalar (raw kopyası yok, ör. oversize) intake'ten; diğerleri parse'dan başlar (İP-1 istisna kararı — bkz. veri sözlüğü source_path).
- Crash-recovery: PROCESSING'de takılı kalan (heartbeat > X dk) dosyalar açılışta RETRY'a çekilir.
- CLI: `ragintel ingest scan|run|retry|reprocess <file_id>|status`.
- Sıralı işleme yeterli (MVP) — paralellik config'te hazır ama varsayılan 1 (GPU çekişmesini önler).
- Test seti: her İP'nin birim testleri + uçtan uca smoke (temsili korpus → COMPLETED) CI'da.

**Kabul kriterleri:**

- [ ] Temsili korpus tek komutla uçtan uca COMPLETED.
- [ ] `kill -9` sonrası `run` kaldığı yerden devam eder, çift işleme yapmaz.
- [ ] pytest süiti < 5 dk, deterministik.

---

## Ek-A — Aşama Bazlı Kalite Skorlama (ADR-011)

**İP-2, İP-3, İP-5, İP-7 ve İP-9'un kurallarını genişletir.** Şema eki: `FAZ1_Sema_Ek1_Kalite.sql`. Tüm eşik ve ağırlıklar `app_config('quality')`'den okunur — kod sabiti yasak.

**İlke:** Aşama metrikleri teşhis içindir; gerçek hedef fonksiyonu golden set retrieval kalitesidir (FAZ 2-3). Aşama skorları alarm üretir, tuning kararı korpus düzeyinde golden set ile verilir.

**Aşama alt skorları** (0-100, her İP kendi adımının `metrics_ingestion.detail`'ine yazar):

| İP | Alt skor girdileri | Hard fail (status=FAILED) | Soft flag (qc_finding) |
|---|---|---|---|
| İP-2 parse | extraction coverage (çıkan/beklenen karakter), parse edilen sayfa oranı, garbage ratio (mojibake/non-printable), tablo tespit sayısı | coverage < 0.50 VEYA garbage > 0.20 | coverage < 0.85 |
| İP-3 clean | retention ratio (korunan içerik oranı), header/footer imza sayısı, encoding düzeltme sayısı | retention < 0.60 (aşırı temizlik) | retention < 0.80 veya > 0.999 (hiç temizlenmemiş) |
| İP-5 chunk | token dağılımı (ort/p95), min-altı oranı, max'ta kesilen oranı, section hizalama oranı | — | truncated_ratio > 0.30 |
| İP-7 embed | NaN/sıfır-norm sayısı, norm sapması | NaN > 0 | doc-içi ort. benzerlik anomalisi |

**Hedefli fallback (İP-2 kuralı):** coverage < 0.50 ise ve dosya PDF ise OCR modunda 1 kez yeniden parse denenir (`ocr_fallback` config'i); ikinci sonuç neyse o kabul edilir.

**Bileşik skor (İP-9 kuralı):** `quality_score = Σ(ağırlık × alt_skor)` — ağırlıklar config'ten (varsayılan parse .35 / clean .20 / chunk .25 / embed .20). Hesap, dosya COMPLETED olduğunda yapılır ve `core_files.quality_score`'a yazılır. Clean alt skoru retention'dan türetilir (İP-9 retrofit kararı); OCR fallback'te son deneme skoru esas alınır.

**Ek kabul kriterleri:**

- [ ] (İP-2) Coverage'ı düşük taranmış PDF'te OCR fallback tetiklenir ve ikinci deneme metrikleri ayrı kaydedilir.
- [ ] (İP-3) Retention 0.60 altında dosya FAILED olur, üstünde ama 0.80 altında qc_finding açılır.
- [ ] (İP-9) `python -m ragintel.report ingestion` çıktısında korpus kalite dağılımı (min/ort/p95 quality_score) ve düşük skorlu ilk 10 dosya listelenir.
- [ ] (Tümü) Eşik değişikliği yalnızca `app_config` üzerinden — kodda hiçbir eşik sabiti yok (test: config değişince davranış değişir).

**FAZ 4 bağlantısı:** `quality_score`, retrieval'da kaynak güven indirimi olarak confidence hesabına girecek (Tasarim_FAZ4 Bölüm 5 — FAZ 3'te detaylandırılır).

## Kod Asistanına Brief Verme Şablonu

Her iş paketi için VS Code'da Sonnet'e şu yapıyla brief verin:

```
BAĞLAM: FAZ1_Sema.sql + bu dokümanın İP-X bölümü (+ İP-X'in bağımlı olduğu paketlerin arayüzleri)
GÖREV: İP-X'i kurallara birebir uyarak implemente et.
KISITLAR: Kabul kriterlerindeki testleri de yaz; kriterler karşılanmadan tamam sayma.
           Şemayı ve ParsedDocument/servis arayüz kontratlarını DEĞİŞTİRME —
           değişiklik ihtiyacı varsa kod yazmadan önce gerekçesiyle raporla.
```

Kontrat değişikliği ihtiyacı doğarsa: kodda çözmeyin, buraya getirin — ADR/şema güncellemesini ben yapar, tutarlılığı tüm dokümanlarda korurum.

## Değişiklik Günlüğü

| Tarih | Versiyon | Değişiklik |
|---|---|---|
| 2026-07-02 | 0.1 | İlk sürüm: İP-0..İP-10, bağımlılık grafiği, kabul kriterleri, brief şablonu. |
| 2026-07-02 | 0.2 | Ek-A eklendi (ADR-011): aşama bazlı kalite skorlama, eşik tablosu, OCR fallback, bileşik skor kuralı, ek kabul kriterleri. |
| 2026-07-02 | 0.3 | İP-10'a reprocess başlangıç kuralı (İP-1 istisnası). İP-3 revize: v1 cleaning birebir taşınmaz (stopword silme v2 ile çelişir), ileriye dönük regresyon suiti. |

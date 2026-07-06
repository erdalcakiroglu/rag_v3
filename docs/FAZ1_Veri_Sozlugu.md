# FAZ 1 — Veri Sözlüğü (ragintel şeması)

**Versiyon:** 0.1 · **Tarih:** 2026-07-02 · **Kaynak DDL:** `FAZ1_Sema.sql`
**Amaç:** Her tablonun görevi, kolonların tuttuğu bilgi, kolonu kimin doldurduğu (İP referansı) ve kimin tükettiği. Kod asistanına İP brief'lerinde bağlam olarak verilir.

---

## Tablo İlişkileri

```
app_config          (bağımsız — tüm İP'ler okur)

core_files 1 ──── N core_chunks 1 ──── 1 core_vectors
    │                                      
    ├──────────── N core_tables            
    ├──────────── N core_figures           
    ├──────────── N metrics_ingestion      
    └──────────── N qc_findings ─(ops.)─ core_chunks
```

Silme davranışı: `core_files` silinirse tüm bağlı kayıtlar CASCADE ile gider. Öksüz chunk/vektör oluşamaz.

---

## 1. app_config — Konfigürasyon (config-first)

**Amaç:** Tüm pipeline parametrelerinin tek doğruluk kaynağı (v1 mirası). Kod içinde sabit parametre YASAK; her ayar buradan okunur. Öncelik zinciri: DB → env → varsayılan (İP-0).

| Kolon | Tip | Açıklama | Dolduran / Tüketen |
|---|---|---|---|
| config_key | text PK | Ayar grubu anahtarı: `chunking`, `embedding`, `ingestion`... | Elle/Admin panel (FAZ 7) / tüm İP'ler |
| config_value | jsonb | Ayar grubunun tamamı (ör. `{"max_tokens":512,"overlap_tokens":64}`) | aynı |
| description | text | İnsan için açıklama | elle |
| updated_by | text | Son değiştiren kullanıcı | elle/admin |
| updated_at | timestamptz | Son değişiklik zamanı | otomatik |

---

## 2. core_files — Dosya Envanteri ve İş Durumu

**Amaç:** Sisteme giren her dosyanın kimliği, sürümü ve pipeline'daki durumu. Aynı zamanda **job state tablosudur** (Redis'siz durum makinesi, İP-1/İP-10): PENDING → PROCESSING → COMPLETED/FAILED.

| Kolon | Tip | Açıklama | Dolduran / Tüketen |
|---|---|---|---|
| file_id | bigint PK | Dosyanın sistem içi kimliği; tüm alt tabloların FK'sı | otomatik / her yer |
| file_name | text | Orijinal dosya adı — **citation'da kullanıcıya gösterilir** (FAZ 4) | İP-1 / FAZ 3-4 |
| file_type | text | `pdf/docx/xlsx/txt` — içerik tespitiyle (python-magic), uzantıyla değil | İP-1 / İP-2 parser seçimi |
| file_size | bigint | Bayt cinsinden boyut; limit kontrolü kanıtı | İP-1 / rapor |
| checksum | text | sha256 — duplicate tespiti ve bütünlük doğrulama | İP-1 / İP-1 dedup |
| source_path | text | Raw storage'daki kopyanın yolu. **İstisna (İP-1 kararı):** intake'te FAILED olan (ör. oversize) dosyalar depoya kopyalanmaz; source_path orijinal yolu gösterir. Bu kayıtları İP-2 okumaz; REPROCESS'leri intake'ten başlar (İP-10) | İP-1 / İP-2, reprocess |
| doc_scope | text | **Yetki kapsamı etiketi** (ör. `hr`, `finans`, `public`). FAZ 3'te retrieval filtresi, ileride RLS anahtarı. MVP'de `default` | İP-1 (klasör/elle eşleme) / FAZ 3-4 retrieval |
| language | text | ISO 639-1 dil kodu (lingua tespiti) — retrieval filtresi + rapor | İP-2 / FAZ 3 |
| doc_version | int | Aynı isimli dosyanın içerik değişiminde artar; eski sürüm silinmez | İP-1 / citation, audit |
| status | text | Durum makinesi: `PENDING/PROCESSING/COMPLETED/FAILED/RETRY/REPROCESS` | İP-10 orchestrator / CLI, rapor |
| fail_reason | text | FAILED durumunda insan-okur hata özeti | İP-2..8 / operasyon |
| retry_count | int | Otomatik retry sayacı (limit 3, İP-10) | İP-10 / İP-10 |
| injection_flag | boolean | Dosyada indirect prompt injection şüphesi (ADR-007). MVP'de bloklamaz, işaretler | İP-4 / FAZ 6 karar, FAZ 3 (ops. filtre) |
| upload_user | text | Dosyayı ekleyen kullanıcı/servis — audit | İP-1 / audit |
| quality_score | numeric(5,2) NULL | **Bileşik kalite skoru 0-100** (ADR-011, `FAZ1_Sema_Ek1_Kalite.sql`): aşama alt skorlarının ağırlıklı ortalaması. Alt skorlar `metrics_ingestion.detail`'de. NULL = henüz hesaplanmadı. FAZ 4 confidence hesabına güven indirimi girdisi | İP-9 / rapor, FAZ 3-4 |
| created_at / updated_at | timestamptz | Kayıt açılış / son durum değişikliği (trigger) | otomatik / SLA ölçümü |

**Kısıt:** `UNIQUE(checksum, doc_version)` — aynı içerik iki kez kaydedilemez.

---

## 3. core_chunks — Bilgi Parçaları

**Amaç:** RAG'ın temel birimi. Her satır, LLM'e bağlam olarak verilebilecek tek bir metin parçası + **citation'ı kurmaya yetecek kaynak bilgisi** (FAZ 4 kontratı: yanıttaki her iddia buradaki bir satıra işaret eder).

| Kolon | Tip | Açıklama | Dolduran / Tüketen |
|---|---|---|---|
| chunk_id | bigint PK | Parça kimliği — citation'ın hedefi | otomatik / FAZ 3-4 |
| file_id | bigint FK | Ait olduğu dosya | İP-5 / join'ler |
| chunk_index | int | Dosya içi sıra — `lookup_document` tool'unun komşu-chunk penceresi bununla çalışır | İP-5 / FAZ 4 tool |
| chunk_text | text | LLM'e verilen orijinal metin (temizlenmiş ama doğal halde) | İP-5 / FAZ 3-4 |
| chunk_text_norm | text | Normalize kopya (lowercase, NFKC, whitespace collapse) — **FAZ 4 quote doğrulaması ve duplicate tespiti aynı fonksiyonla üretilmiş bu kolonu kullanır** | İP-3/İP-5 / FAZ 4 validate, İP-9 |
| token_count | int | BGE-M3 tokenizer'ıyla sayım — context bütçe hesabı | İP-5 / FAZ 3 context builder |
| page_number | int NULL | PDF/DOCX sayfa no — **citation'da gösterilir** | İP-2→5 / FAZ 4 |
| sheet_name | text NULL | XLSX sayfa adı — citation | İP-2→5 / FAZ 4 |
| section_title | text NULL | Bağlı olduğu başlık — citation + retrieval filtresi | İP-2→5 / FAZ 3-4 |
| char_start / char_end | int NULL | Temizlenmiş tam metindeki span — overlap doğrulama, highlight (FAZ 7 UI) | İP-5 / test, UI |
| tsv | tsvector (generated) | `chunk_text_norm`'dan otomatik full-text index — **hybrid search'ün BM25 bacağı** (İP yazmaz, DB üretir) | DB / FAZ 3 hybrid |
| created_at | timestamptz | Yazım zamanı | otomatik |

**Kısıt:** `UNIQUE(file_id, chunk_index)` — reprocess'te eski set silinip yenisi yazılır (İP-8).

---

## 4. core_vectors — Embedding'ler

**Amaç:** Her chunk'ın 1024 boyutlu BGE-M3 dense vektörü. `core_chunks`'tan ayrı tablo olmasının nedeni: model değişiminde yalnızca bu tablo yeniden üretilir, chunk'lar dokunulmaz kalır.

| Kolon | Tip | Açıklama | Dolduran / Tüketen |
|---|---|---|---|
| chunk_id | bigint PK+FK | 1:1 chunk ilişkisi | İP-8 / FAZ 3 |
| embedding | vector(1024) | Normalize dense vektör (cosine için) — HNSW index'li | İP-7→8 / FAZ 3 vector/hybrid search |
| model_name | text | Vektörü üreten model — **model migrasyonunda hangi satırların yeniden üretileceğini belirler** | İP-7 / operasyon |
| created_at | timestamptz | Üretim zamanı | otomatik |

---

## 5. core_tables — Ayıklanmış Tablolar

**Amaç:** Doküman içindeki tabloların yapısal (jsonb) ve düzleştirilmiş metin hali. Tablolar gövde metnine karıştırılmaz (İP-2); kendi chunk'ı olur. İleride SQL/Data agent (FAZ 9) ve tablo-özel retrieval bunun üzerine kurulur.

| Kolon | Tip | Açıklama | Dolduran / Tüketen |
|---|---|---|---|
| table_id | bigint PK | Tablo kimliği | otomatik |
| file_id | bigint FK | Kaynak dosya | İP-2 |
| page_number / sheet_name | int/text NULL | Konum (pdf-docx / xlsx) — citation | İP-2 / FAZ 4 |
| table_index | int | Dosya içi tablo sırası | İP-2 |
| table_data | jsonb | Normalize satır/sütun yapısı (Docling çıktısı) — ileride yapısal sorgu | İP-2 / FAZ 9 |
| table_text | text | Düzleştirilmiş metin — tablonun chunk'ına gömülen içerik | İP-2→5 / FAZ 3 |
| created_at | timestamptz | | otomatik |

---

## 6. core_figures — Ayıklanmış Şekiller

**Amaç:** Görsel/şekil envanteri. MVP'de yalnızca varlık + caption tutulur (görsel anlama yok); caption metni chunk'lara girebilir. İleride multimodal genişleme için yer tutucu.

| Kolon | Tip | Açıklama | Dolduran / Tüketen |
|---|---|---|---|
| figure_id | bigint PK | Şekil kimliği | otomatik |
| file_id | bigint FK | Kaynak dosya | İP-2 |
| page_number | int NULL | Konum | İP-2 |
| figure_index | int | Dosya içi sıra | İP-2 |
| caption | text NULL | Şekil altyazısı — aranabilir tek kısım | İP-2 / FAZ 3 |
| storage_path | text NULL | Görselin object storage yolu (MVP'de boş kalabilir) | İP-2 / ileride |
| created_at | timestamptz | | otomatik |

---

## 7. metrics_ingestion — Pipeline Adım Metrikleri

**Amaç:** Her dosyanın her pipeline adımının süre/başarı kaydı. FAZ 1 çıkış kriteri ("≥%95 parse başarısı") ve darboğaz analizi buradan okunur; FAZ 2'de Langfuse ile tamamlanır (çakışmaz: bu tablo dosya-adım granülünde kalıcı kayıt, Langfuse sorgu-akış izleme).

| Kolon | Tip | Açıklama | Dolduran / Tüketen |
|---|---|---|---|
| metric_id | bigint PK | | otomatik |
| file_id | bigint FK | Ölçülen dosya | ilgili İP |
| step | text | `intake/parse/clean/chunk/embed/store/injection_scan` — intake kalite skoruna girmez, yalnızca süre/başarı + dedup/tip-tespit sayaçları (Ek2) | ilgili İP / rapor |
| duration_ms | int | Adım süresi | ilgili İP / darboğaz analizi |
| ok | boolean | Adım başarı durumu | ilgili İP / başarı oranı |
| detail | jsonb | Adım-özel sayaçlar: chunk sayısı, kırpılan karakter, uyarılar, batch bilgisi... | ilgili İP / İP-9 rapor |
| created_at | timestamptz | | otomatik |

---

## 8. qc_findings — Kalite Kontrol Bulguları

**Amaç:** Pipeline'ın ürettiği kalite/güvenlik şüphelerinin iş listesi. Bulgular bloklamaz (MVP), işaretler; `resolved` ile takip edilir. FAZ 6 guardrail kararları ve operasyon ekranı (FAZ 7) bunun üzerine kurulur.

| Kolon | Tip | Açıklama | Dolduran / Tüketen |
|---|---|---|---|
| finding_id | bigint PK | | otomatik |
| file_id | bigint FK | İlgili dosya | İP-2..9 |
| chunk_id | bigint FK NULL | Bulgu chunk düzeyindeyse dolu, dosya düzeyindeyse NULL | İP-5/9 |
| finding | text | Hard: `empty_chunk / duplicate_chunk / too_short / too_long / parse_failed / embed_failed / injection_suspect` · Soft (Ek3, Ek-A eşlemesi): `low_coverage / low_retention / no_cleaning_effect / chunk_truncation_high / embed_anomaly` | ilgili İP / İP-9 rapor, FAZ 6 |
| detail | text | Bulgunun kanıtı (ör. şüpheli span, duplicate eşi) | ilgili İP / inceleme |
| resolved | boolean | Operatör onayı/çözümü — açık bulgular partial index ile hızlı listelenir | elle/FAZ 7 / rapor |
| created_at | timestamptz | | otomatik |

---

## 9-10. eval_golden_sets / eval_golden_records — Golden Set Saklama (FAZ 2)

**Amaç:** Golden QA setinin versiyonlu, idempotent saklanması (İP-2.1a; DDL: repo `docs/FAZ2_Sema.sql`). Doğruluk kaynağı repo'daki JSONL'dir; DB kopyası harness/rapor sorguları içindir.

**eval_golden_sets:** set_version (PK), payload_hash (idempotency: aynı hash → no-op), source_path, record_count, timestamps.

**eval_golden_records:** (set_version, record_id) PK, FK CASCADE → sets. question + question_norm (duplicate kontrolü, `UNIQUE(set_version, question_norm)`), ideal_answer, category (6'lı enum: single_fact/synthesis/multi_hop/table_based/unanswerable/citation_sensitive), difficulty (1-3), gold_evidence (jsonb — file_name/page|sheet/quote; quote `normalize_for_quote` ile `chunk_text_norm`'a eşlenerek doğrulanır), doc_scope, answerable, created_by, notes, payload_hash.

Dolduran: İP-2.1a loader / Tüketen: İP-2.3 RAGAS harness, İP-2.4 retrieval benchmark, raporlar.

## Tasarım Gerekçeleri (özet)

1. **chunks ↔ vectors ayrımı:** Embedding modeli değişiminde yalnızca `core_vectors` yeniden üretilir (`model_name` ile seçici migrasyon); chunk ve citation zinciri sabit kalır.
2. **`chunk_text_norm` DB'de saklanır (on-the-fly değil):** FAZ 4 quote doğrulaması ile İP-9 duplicate tespitinin AYNI normalize çıktı üzerinde çalışması garanti edilir; normalize fonksiyonu değişirse fark migration'la görünür olur.
3. **Job state ayrı tablo değil `core_files.status`:** MVP ölçeğinde ek tablo/Redis karmaşıklığı gereksiz (v1'de kanıtlandı); İP-10 heartbeat/retry bunun üzerinde çalışır.
4. **`doc_scope` FAZ 1'den itibaren var:** Yetki filtresi sonradan eklenirse tüm korpusun yeniden etiketlenmesi gerekirdi; en pahalı retrofit buydu (FAZ 4 tasarımı, Bölüm 9).
5. **`tsv` generated column:** Uygulama kodu full-text index'i unutamaz/bozamaz; DB tutarlılığı garanti eder.

## Değişiklik Günlüğü

| Tarih | Versiyon | Değişiklik |
|---|---|---|
| 2026-07-02 | 0.1 | İlk sürüm: 8 tablo, kolon sözlüğü, ilişki şeması, tasarım gerekçeleri. |
| 2026-07-02 | 0.2 | ADR-011: `core_files.quality_score` eklendi; `app_config`'e `quality` anahtarı (eşikler/ağırlıklar/OCR fallback). Kaynak: `FAZ1_Sema_Ek1_Kalite.sql`. |
| 2026-07-02 | 0.3 | `metrics_ingestion.step` listesine `intake` eklendi (İP-1 kriteri #4). Kaynak: `FAZ1_Sema_Ek2_Intake.sql`; taban DDL de güncellendi. |
| 2026-07-02 | 0.4 | `qc_findings.finding` listesine Ek-A soft flag değerleri eklendi (İP-2 raporu). Kaynak: `FAZ1_Sema_Ek3_QC_Soft.sql`; taban DDL güncellendi. `source_path` istisnası belgelendi (İP-1). |
| 2026-07-03 | 0.5 | FAZ 2 eval tabloları eklendi (eval_golden_sets, eval_golden_records — İP-2.1a, repo `docs/FAZ2_Sema.sql`, mimari onaylı). |

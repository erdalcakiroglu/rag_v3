# M-4 — Config Bütünlüğü (Kapanış Raporu)

**Durum:** PARÇA 1–4 tamamlandı. Kod + 28 yeni test + canlı migrasyon + admin canlı demo.
Tam süit **353 passed**.

---

## 0. Ön-veri kontrolü (kod öncesi) — şartnamedeki bir bombayı etkisiz hâle getirdi

Şartname `MODEL_STAMP` için `f"{model}@ollama"` diyordu. Veriye bakınca:

| Kontrol | Sonuç |
|---|---|
| `core_vectors` damga dağılımı | **tek damga**: `bge-m3@ollama` × 1266 |
| `embedding.model` (config/DB) | `BAAI/bge-m3` (HF repo id) |
| Tokenizer gereksinimi | `AutoTokenizer.from_pretrained("BAAI/bge-m3")` → repo id ZORUNLU |
| Ollama `/api/embed` gereksinimi | etiket (`bge-m3`) |

Naif türetme `BAAI/bge-m3@ollama` üretirdi ≠ korpustaki `bge-m3@ollama`. Yeni uyuşmazlık
koruması eklendiği anda **her embed yazımı reddedilir, ingestion tuğlaya dönerdi** (ya da
1266 satırlık backfill zorunlu olurdu).

**Çözüm:** tek otorite alan HF repo id olarak kalır; Ollama etiketi ve damga ondan
**basename ile türetilir**: `BAAI/bge-m3` → `bge-m3` → `bge-m3@ollama`. Mevcut korpusla
birebir uyumlu, backfill yok. `test_stamp_matches_existing_corpus_format` bunu mühürler.

İkincil bulgular: `test_ip31`/`test_ip32` fixture'ları damgayı açıkça `bge-m3@ollama` yazıyor
(koruma testleri kırmaz). Şemadaki `core_vectors.model_name DEFAULT 'BAAI/bge-m3'` **ölü
default** — hiçbir insert onu kullanmıyor; damga ile tutarsız olduğu için ileride kaldırılmalı.

---

## 1. PARÇA 1 — Yalan alanlar

### `embedding.normalize` KALDIRILDI

Alan `app_config`'te vardı, **hiçbir yerde okunmuyordu**; `l2_normalize` koşulsuz
uygulanıyordu. `normalize=false` yazmak hiçbir şey yapmıyordu — config yalan söylüyordu.

Düğmeyi bağlamak yerine **yok ettik**: normalize'lı ve normalize'sız vektörlerin aynı
korpusta karışması kosinüs benzerliğini bozar ve geri dönüşü tam reprocess'tir. Var olmayan
düğme, yanlış çevrilemeyen düğmedir. Davranış "daima normalize" olarak `EmbeddingConfig`
docstring'inde sabitlendi.

### `embedding.model` TEK OTORİTE

- Tokenizer (`chunking/adapter.py`) **ve** Ollama embed çağrısı (`embedding/service.py`)
  **ve** sorgu embedding'i (`retrieval/service.py`) artık aynı alandan besleniyor.
  Eskiden ingest modeli `.env`'deki `RAGINTEL_OLLAMA_MODEL`'den, tokenizer ise DB'den
  geliyordu — "hangi model" sorusunun iki kaynağı vardı.
- `OllamaSettings.model` yalnızca **bootstrap-fallback** (DB erişilemezse).
- `MODEL_STAMP` sabiti **silindi**; `model_stamp(model)` ve `ollama_tag(model)` türetir.
  Artık model değişince damga da değişir (eskiden `model_name` sınıf sabitiydi ve
  `core_vectors.model_name` köken bilgisi **yanlış** olabiliyordu).

### YENİ KORUMA — `assert_corpus_model`

`copy_vectors` yazımdan önce korpusun mevcut damgasını kontrol eder; farklıysa
`CorpusModelMismatch` fırlatır. ADR-012 "tek korpus tek backend" kuralı artık **disiplinle
değil mekanik olarak** zorlanıyor. Boş korpus her damgayı kabul eder.

---

## 2. PARÇA 2 — Tunable taşıma (davranış DEĞİŞMEDİ)

Taşınan her tunable'ın kod varsayılanı korundu; 9 parametrik test bunu doğruluyor.

| Grup | Alan | Default | Eski yeri |
|---|---|---|---|
| `storage` *(yeni)* | `hnsw_m`, `hnsw_ef_construction` | 16, 64 | `storage_repo` SQL literal |
| `ingestion` | `max_retry` | 3 | `orchestrator.MAX_RETRY` |
| `pii` | `date_dmy_pattern`, `date_iso_pattern`, `year_min`, `year_max` | M-3 korumaları | `pii.py` modül sabitleri |
| `api` *(yeni)* | `health_timeout`, `feedback_timeout` | 8.0 | `runtime.py` gömülü `timeout=8` |
| `quality` | `parse_success_target` | 0.95 | `ingestion_report.PARSE_SUCCESS_TARGET` |
| `eval` *(yeni)* | `ctx_cap`, `agent_model`, `judge_model` | 10, qwen3-32b, llama-3.3-70b | `harness.py` sabitleri |

**HNSW notu:** `create_vector_index` `IF NOT EXISTS` kullandığı için parametre değişikliği
mevcut index'i etkilemez → **yeniden index gerektirir** (docstring'e ve config
description'ına yazıldı).

**PII güvenliği:** desen config'ten geldiği için bozuk/eksik-gruplu regex artık ham istisna
atmaz; maskeleme sessizce atlanır (`test_pii_broken_pattern_is_skipped_not_crash`).

### Taşınmayanlar (bilinçli)

`_SECTION_RE`, `_MARKER_RE`, `PLACEHOLDER_CELL` ve UI'daki `"Kolon N"` metni tunable değil,
**kod-içi biçim bağları**: chunker çıktısına / LLM işaret sözdizimine kilitli. DB'ye taşımak
drift daveti olurdu ve `_SECTION_RE` zaten M-2b (kalıcı `table_id` kolonu) ile ölecek.

### Yol boyunca çıkan: env prefix çakışması

Yeni `eval` grubunun öneki (`RAGINTEL_EVAL_`) mevcut `eval_gates`'in
`RAGINTEL_EVAL_GATES_*` değişkenlerini de yakalıyordu. `loader._collect_env_overrides`
artık **en uzun eşleşen grup öneki kazanır** kuralını uyguluyor
(`test_env_prefix_longest_match_wins`).

---

## 3. PARÇA 3 — Altyapı default temizliği

`settings.py` kod varsayılanlarından **gerçek host/IP/kullanıcı/parola kalktı**; eksikse
açık hata: `RAGINTEL_DB_HOST tanımsız (.env'de tanımlayın)`.

| Ayar | Önce (kodda) | Sonra |
|---|---|---|
| `DbSettings.host/user/password` | gerçek IP + `ragintel_app` | `""` + `MissingBootstrapSetting` |
| `OllamaSettings.base_url` | iç FQDN | `""` + `require_base_url()` |
| `LiteLLMSettings.api_base` | iç FQDN | `""` |
| `TeiSettings.rerank_url` | iç IP:8085 | `""` (**opsiyonel**) |

`test_settings_source_has_no_internal_hostnames` regresyon kilidi: `settings.py` içinde
çıplak IP veya kurum alan adı kalırsa kırmızı yanar.

**Bulgu:** `.env.example` **izleniyor ve dün push edilmişti**, içinde gerçek DB IP'si vardı.
Şablona çevrildi (`db.example.internal`).

**TEI istisnası:** `RAGINTEL_TEI_RERANK_URL` `.env`'de tanımlı değildi; tek kaynağı kod
default'uydu. `rerank_backend=passthrough` olduğu için TEI fiilen kullanılmıyor. Zorunlu
kılmak yerine **opsiyonel** yapıldı: URL boşsa health `"disabled"` der ve
`derive_health_status` bunu **degrade saymaz** (yapılandırılmamış bileşen ≠ arızalı bileşen).
`rerank_backend='tei'` seçilirse `require_rerank_url()` zorunlu kılar.
`.env`'e satır eklendi ki canlı davranış birebir korunsun.

---

## 4. PARÇA 4 — Kanıt

### 4a. Migrasyon — dry-run bir veri kaybını yakaladı

İlk migrasyon betiği `prompts.agent_system` (serbest `dict[str,str]`) alanını pydantic
alt-modeli sanıp içine özyineledi; dry-run çıktısı `dusen=['agent_system.v1','v2','v3']`
gösterdi — **DB'de versiyonlanmış sistem prompt'ları silinecekti.** Betik, yalnızca gerçek
pydantic alt-modellerine özyineleyecek şekilde düzeltildi
([scripts/m4_config_migration.py](../scripts/m4_config_migration.py)).

Uygulama sonrası canlı değerler **korundu**: `agent.model=qwen3.5:35b`, `timeout_sec=180`,
`embedding.batch_size=16`, `retrieval.hybrid_fusion=weighted`, prompts `v1/v2/v3` (aktif `v2`).
Eklenenler: `chunking.table_subchunk_max_tokens` (M-1'den beri seed'siz), `agent.max_question_chars`,
`ingestion.max_retry`, `quality.parse_success_target` + üç yeni grup. Düşen: `embedding.normalize`.

### 4b. `show --source` — tüm davranışsal alanlar `db`

```
davranışsal yaprak sayısı: 86
source != db olanlar: YOK — hepsi db
```

`Config_Seed.sql` yeniden üretildi (13 grup); içinde `normalize` yok ve
`assert_no_secret_fields` guard'ı yeni gruplarda secret sızıntısı olmadığını doğruluyor.

### 4c. Admin panelden canlı değişiklik + etki

Gerçek `/api/admin/config/{group}` ucu, gerçek doğrulama, gerçek DB yazımı. (Paylaşılan DB'de
**kalıcı admin hesabı açılmadı**; resolver in-process taklit edildi.)

| Adım | Sonuç |
|---|---|
| `POST /api/admin/config/ingestion {max_retry: 5}` | 200, `updated_by=m4-demo` |
| Etki | `cfg.group('ingestion').max_retry == 5` (kod default 3) |
| `POST /api/admin/config/quality {parse_success_target: 0.60}` | 200 |
| Etki | `build_report(...)['parse_success_target'] == 0.60` |
| `POST /api/admin/config/storage {hnsw_m: "cok"}` | **400** — DB'ye yazılmadı (`hnsw_m` hâlâ 16) |
| Geri alma | iki grup da eski değerlerine döndü (doğrulandı) |

### 4d. Eval gate — PASS (davranış değişmedi)

`python -m ragintel.eval gate --smoke` → **exit 0**, *"tüm eşikler geçildi"*
(agent `deepseek-v4-pro`, judge `deepseek/dev-mode`, 5 answerable + 2 unanswerable):

| Metrik | Değer | Eşik | |
|---|---|---|---|
| faithfulness | 0.7111 | ≥ 0.70 | ✅ |
| context_precision | 0.9386 | ≥ 0.75 | ✅ |
| honesty_ratio | 1.0 | ≥ 0.80 | ✅ |

Config bütünlüğü çalışması pipeline davranışını bozmadı: taşınan tunable'ların hepsi kod
default'unda kaldı, `embedding.model` tek otoriteye çekilince de aynı model (`BAAI/bge-m3` →
`bge-m3`) kullanılmaya devam etti.

### 4e. Testler

- `tests/test_faz_m4_config_integrity.py` — 28 test (yalan alan, damga türetme + korpus
  uyumu, uyuşmazlık koruması, 9 parametrik "default değişmedi", env prefix, PII config,
  health disabled, HNSW parametreleri, bootstrap fail-fast, settings.py IP kilidi).
- `tests/test_config_priority.py` — `normalize` örneği `dim` ile değiştirildi (alan kalktı).
- Tam süit: **353 passed** (`-m "not slow"`).

---

## 5. Kesinti notu (dürüstlük)

PARÇA 4 çalışması sırasında DB erişimi kesildi:
`FATAL: no pg_hba.conf entry for host "192.168.36.1"`. Bunun M-4 değişikliklerinden
kaynaklanmadığı, `settings.py` stash'lenip **eski kodla da aynı reddin alınmasıyla**
doğrulandı (TCP 5432 açıktı; reddeden yetkilendirmeydi). Erişim geri gelene kadar canlı
kanıt üretilmedi ve "tamam" denmedi.

# RAG v2 — Enterprise Agentic RAG Proje Dokümanı

| | |
|---|---|
| **Versiyon** | 0.1 |
| **Tarih** | 2026-07-02 |
| **Sahip** | Erdal Çakıroğlu |
| **Durum** | Aktif geliştirme — yaşayan doküman |
| **İlgili dosyalar** | `rag_V2_updated.drawio`, `ingestion_pipeline.drawio`, `RAG_v2.docx` |

---

## 1. Vizyon ve Amaç

Kurumsal dokümanlar (PDF/DOCX/XLSX/TXT, ileride SharePoint/Wiki/DB/Ticket) üzerinde, tamamen **on-premise** çalışan, kaynak gösteren (citation), guardrail'li ve **agentic** bir RAG platformu. v1 (Classic RAG, pgvector + SentenceTransformers, config-first) bu projenin temelidir; v2 onu çok katmanlı, ajan tabanlı kurumsal mimariye taşır.

**Temel ilkeler:**

- Veri egemenliği: tüm bileşenler self-hosted / local LLM
- Config-first: tüm pipeline parametreleri DB üzerinden dinamik
- Ölçülebilirlik: her mimari karar golden set + metriklerle doğrulanır
- Basit başla, ölçerek büyüt: karmaşıklık ancak ölçüm gerekçesiyle eklenir

## 2. Hedef Mimari (Özet)

```
User → API Gateway (AuthN/AuthZ, RBAC, Tenant, Rate Limit)
     → INPUT GUARDRAIL (injection, PII, policy, permission context)
     → AGENTIC ORCHESTRATION (LangGraph)
     → TOOL LAYER (vector / hybrid / graph / SQL / rerank / cache / memory / audit)
     → LLM GATEWAY (model router, prompt template, token control)
     → OUTPUT GUARDRAIL (grounding, citation, hallucination, PII masking)
     → FINAL RESPONSE (answer + sources + confidence + follow-up)
     ↕
     OBSERVABILITY & EVALUATION (Langfuse + RAGAS + golden set)  ← tüm katmanlara yatay
```

**Veri katmanı:** PostgreSQL + pgvector (omurga: files, chunks, vectors, metadata, metrics) · Apache AGE (knowledge graph — ertelenmiş) · Redis (session memory, semantic cache, queue, rate limit) · Object Storage (raw + parsed dosyalar).

**Ingestion (offline):** Folder Scanner → Parser (layout-aware) → Cleaning → Chunking → Metadata → Embedding → pgvector. Entity/Relation extraction → GraphRAG fazına ertelendi.

## 3. Mimari Kararlar (ADR)

Kabul edilen kararlar. Yeni karar eklerken aynı formatı kullan.

### ADR-001 — MVP'de tek agentic loop, 7 ajan değil
- **Karar:** Router/Planner/Retrieval/Graph/SQL/Validation/Composer yerine tek ajan + tool seti ile başlanır. Router+Planner birleşik; Validation bir LangGraph node'u.
- **Gerekçe:** Local LLM'de her ajan sıçraması ek gecikme/token maliyeti; 2026 üretim pratiği "az ajan, çok tool". Bölme kararı ancak ölçümle verilir.
- **Durum:** Kabul edildi.

### ADR-002 — GraphRAG MVP dışı
- **Karar:** Entity/relation extraction ve Apache AGE, MVP'den çıkarılıp ayrı faza (FAZ G) alındı. Ingestion şeması graph-ready tasarlanır ama üretilmez.
- **Gerekçe:** GraphRAG'da ana başarısızlık nedeni entity extraction kalitesi; kötü graf RAG'ı bozar. AGE küçük graflar için uygun, büyürse Neo4j/Memgraph veya LightRAG değerlendirilecek.
- **Durum:** Kabul edildi.

### ADR-003 — LLM serving: Dev=Ollama, Prod=vLLM, soyutlama=LiteLLM
- **Karar:** LLM Gateway OpenAI-uyumlu API'ye (LiteLLM) karşı yazılır; geliştirmede Ollama, üretimde vLLM.
- **Gerekçe:** vLLM eşzamanlı yükte 3-9x throughput ve tutarlı latency; soyutlama sayesinde motor değişimi kod değişikliği gerektirmez.
- **Durum:** Kabul edildi.

### ADR-004 — Observability & Evaluation öne çekildi (FAZ 8 → FAZ 2-3)
- **Karar:** Langfuse (self-hosted) + OpenTelemetry tracing + RAGAS metrikleri + Türkçe golden QA seti, retrieval katmanı kurulmadan ÖNCE hazır olur.
- **Gerekçe:** Chunking/embedding/rerank kararları ölçümsüz verilemez; eval-driven development.
- **Durum:** Kabul edildi.

### ADR-005 — Embedding/Reranker: BGE-M3 (birincil aday)
- **Karar:** Embedding: BGE-M3 (dense+sparse tek modelde, 100+ dil) — alternatif Qwen3-Embedding. Reranker: bge-reranker-v2-m3 — alternatif Qwen3-Reranker. Nihai seçim golden set üzerindeki Türkçe performansla.
- **Gerekçe:** Türkçe korpus için çok dilli model şart; BGE-M3'ün sparse çıktısı hybrid search'ü sadeleştirir.
- **Durum:** Kabul edildi — benchmark bekliyor.

### ADR-006 — Semantic cache permission-scoped
- **Karar:** Redis semantic cache anahtarları tenant + permission context içerir; cache hit yalnızca aynı yetki kapsamında geçerli.
- **Gerekçe:** Aksi halde kullanıcının erişemediği içerik cache üzerinden sızar (RBAC bypass).
- **Durum:** Kabul edildi.

### ADR-007 — Guardrail kapsamı ingestion'ı da içerir
- **Karar:** Input guardrail yalnızca kullanıcı sorgusunu değil, ingest edilen doküman içeriğini de tarar (indirect prompt injection). Araç adayı: NeMo Guardrails / Llama Guard.
- **Gerekçe:** Dokümanlar güvenilmeyen girdidir; RAG'a özgü ana saldırı vektörü.
- **Durum:** Kabul edildi.

### ADR-008 — Tool Layer MCP uyumlu
- **Karar:** Tool'lar MCP server olarak tasarlanır.
- **Gerekçe:** Framework bağımsızlık, yeniden kullanılabilirlik; 2026 fiili standardı.
- **Durum:** Kabul edildi.

### ADR-009 — Hybrid search Postgres içinde
- **Karar:** pgvector (dense) + tsvector/pg_search (sparse/BM25) + RRF fusion; ayrı arama motoru yok. Ölçek büyürse pgvectorscale (~50M vektöre kadar).
- **Gerekçe:** Tek veri deposu = RBAC row-level security retrieval anında uygulanır; operasyonel sadelik.
- **Durum:** Kabul edildi.

### ADR-010 — Agent state: LangGraph PostgresSaver
- **Karar:** Kalıcı agent state/checkpoint Postgres'te (PostgresSaver); Redis yalnızca geçici cache/queue.
- **Gerekçe:** Retry, execution replay, denetlenebilirlik bedavaya gelir.
- **Durum:** Kabul edildi.

### ADR-011 — Aşama bazlı kalite skorlama (ölçüm evet, lokal maksimizasyon hayır)
- **Karar:** Her pipeline aşaması (parse/clean/chunk/embed) 0-100 alt skor üretir (`metrics_ingestion.detail`); dosya düzeyinde ağırlıklı bileşik skor `core_files.quality_score`'a yazılır. Eşikler/ağırlıklar `app_config('quality')`'de. Hard fail → FAILED, soft → qc_finding. Tespit edilebilir hata modlarına hedefli fallback (ör. düşük coverage → OCR retry).
- **Gerekçe:** Kalite her aşamada ölçülmeli; ancak aşama metriği lokal hedefe dönüşürse aşırı optimizasyon (ör. aşırı temizlik) uçtan uca kaliteyi düşürür. Aşama skorları alarm/teşhis, golden set nihai hakem. Skorlama deterministik ve ucuz — LLM sıcak yolda değil.
- **Durum:** Kabul edildi. Detay: `FAZ1_Is_Plani.md` Ek-A, `FAZ1_Sema_Ek1_Kalite.sql`.

### ADR-012 — Embedding backend: Ollama/bge-m3 (H200 sunucusu)
- **Karar:** İP-7 birincil embedding backend'i remote Ollama HTTP (`/api/embed`, model `bge-m3`) — GPU'lu sunucuda (NVIDIA H200). Yerel torch/FlagEmbedding bağımlılığı EKLENMEZ; `embed_batch` arayüzü backend'i soyutlar. Vektörler istemci tarafında L2-normalize edilir (norm=1 garantisi backend'den bağımsız). Tek korpus = tek backend kuralı: `core_vectors.model_name` backend damgası taşır (`bge-m3@ollama`); FAZ 3 sorgu embedding'i de AYNI backend'i kullanır.
- **Gerekçe:** H200'de embedding CPU'dan kat kat hızlı; torch matris riski tamamen düşer; ağ tek hata noktası olduğundan retry/timeout + batch küçültme kuralları eklenir. Quant farkı nedeniyle FlagEmbedding çıktısıyla karıştırılamaz.
- **Durum:** Kabul edildi (2026-07-02).

### ADR-013 — Tracing platformu: Langfuse v3 (Podman, DB sunucusu)
- **Karar:** FAZ 2 observability platformu Langfuse v3; RHEL üzerinde Podman/podman-compose ile (Docker'sız, rootless), 192.168.36.15'te. Langfuse'un iç postgres/redis container'ları host'a port açmaz (mevcut PG17/Redis ile çakışma önlemi). Enstrümantasyon OTel üzerinden — platform değişimi (Phoenix vb.) uygulama kodunu etkilemez.
- **Gerekçe:** Container kullanımı onaylandı; Langfuse prompt yönetimi + feedback UI + LLM-as-judge entegrasyonuyla FAZ 7-8 ihtiyaçlarını da karşılar. Kurulum: `Kurulum_RHEL_Langfuse_Podman.md`.
- **Durum:** Kabul edildi (2026-07-02).

## 4. Teknoloji Yığını

| Bileşen | Dev | Prod | Not |
|---|---|---|---|
| Orkestrasyon | LangGraph | LangGraph | PostgresSaver checkpointing |
| LLM serving | Ollama | vLLM | LiteLLM soyutlaması |
| LLM modelleri | Qwen3 / Llama / Mistral | aynı | TR performansı benchmark'a bağlı |
| Embedding | BGE-M3 | BGE-M3 | ADR-005 |
| Reranker | bge-reranker-v2-m3 | aynı | ADR-005 |
| Vector store | PostgreSQL + pgvector | + pgvectorscale | ADR-009 |
| Keyword search | tsvector / pg_search | aynı | RRF fusion |
| Graph | — (ertelendi) | AGE → Neo4j/Memgraph? | ADR-002, FAZ G |
| Cache/queue | Redis | Redis | permission-scoped cache (ADR-006) |
| Parsing | Docling / Unstructured | aynı | layout-aware, tablo desteği |
| Guardrails | NeMo Guardrails / Llama Guard | aynı | ADR-007 |
| Observability | Langfuse + OTel | aynı | self-hosted |
| Evaluation | RAGAS + golden set | aynı | CI gate |
| API | FastAPI | FastAPI | RBAC, tenant, rate limit |
| UI | React (Chat + Admin) | aynı | |

## 5. Revize Yol Haritası

Orijinal 10 fazın revize hâli. **Değişiklikler kalın.**

| Faz | Kapsam | Değişiklik |
|---|---|---|
| FAZ 1 | Ingestion & Data Preparation (parse → clean → chunk → metadata → embedding → pgvector) | **Entity/relation extraction çıkarıldı → FAZ G. Parser: Docling/Unstructured. Doküman içi injection taraması eklendi (ADR-007)** |
| FAZ 2 | Knowledge Storage (Postgres+pgvector, Redis) **+ Observability & Eval temeli: Langfuse, tracing, golden QA seti v1** | **Eval FAZ 8'den öne çekildi (ADR-004). GraphDB kurulumu FAZ G'ye** |
| FAZ 3 | Retrieval Layer: vector + hybrid (RRF) + metadata filter + rerank | **Her konfigürasyon golden set ile ölçülür; graph search FAZ G'ye** |
| FAZ 4 | Agentic Orchestration: **tek agentic loop + tool seti (ADR-001), PostgresSaver** | **7 ajan → 1; bölünme ölçüm gerekçesiyle** |
| FAZ 5 | LLM Gateway: **LiteLLM + Ollama/vLLM**, prompt template, token control, context compression | **ADR-003** |
| FAZ 6 | Guardrail & Security: input/output guardrail, PII masking, **permission-scoped cache** | **Minimal set (injection + grounding check) FAZ 3-4'te başlar; burada tamamlanır** |
| FAZ 7 | UI & Admin Panel (React chat, doküman yönetimi, config ekranları) | değişiklik yok |
| FAZ 8 | ~~Observability & Evaluation~~ → **İleri evaluation: CI eval gates, A/B, drift izleme** | **Temel kısmı FAZ 2'ye taşındı** |
| FAZ 9 | Enterprise Integration: LDAP/AD, SharePoint, Jira, API'ler | değişiklik yok |
| FAZ 10 | Production Hardening: performans, DR, deployment, versiyonlama | değişiklik yok |
| **FAZ G (yeni, opsiyonel)** | **GraphRAG: entity/relation extraction, AGE veya Neo4j/Memgraph/LightRAG, graph search tool, multi-hop değerlendirme** | **ADR-002 — ancak FAZ 3 metrikleri multi-hop açığı gösterirse başlar** |

## 6. MVP Kapsamı

**Dahil:** Dosya tabanlı ingestion (PDF/DOCX/XLSX/TXT) · hybrid retrieval + rerank · tek agentic loop (LangGraph) · citation'lı yanıt · temel input/output guardrail · Langfuse tracing + RAGAS + golden set · FastAPI + basit chat UI.

**Hariç (bilinçli):** GraphRAG · SQL/Data agent · multi-agent bölünmesi · SharePoint/LDAP entegrasyonları · semantic cache (önce doğruluk, sonra hız) · admin paneli tam kapsamı.

**MVP başarı kriterleri (golden set üzerinde):**

- Faithfulness ≥ 0.85, context precision ≥ 0.80 (RAGAS)
- Yanıtların %100'ünde doğrulanabilir kaynak citation'ı
- P95 uçtan uca yanıt < 15 sn (local LLM, tek kullanıcı)

## 7. Riskler

| Risk | Etki | Azaltma |
|---|---|---|
| Türkçe embedding/LLM kalitesi beklenenin altında | Yüksek | Golden set benchmark'ı FAZ 2'de; model değişimi LiteLLM ile ucuz |
| Ajan döngüsü local LLM'de yavaş | Orta | Tek loop (ADR-001), context compression, sonradan semantic cache |
| Tablo ağırlıklı PDF parse hataları | Yüksek | Docling; parse kalite metrikleri (FAZ 1.12 QC korunuyor) |
| Cache üzerinden yetki sızıntısı | Yüksek | ADR-006; cache'i MVP dışına aldık |
| GraphRAG karmaşıklığı projeyi bloke eder | Orta | ADR-002: ertelendi, metrik gerekçesi şartı |
| pgvector ölçek limiti | Düşük | <5-10M vektör sorunsuz; pgvectorscale hazır çıkış yolu |

## 7b. FAZ 1 — Minimum Altyapı ve Teknoloji Seçimi

### Altyapı

| Bileşen | Minimum | Not |
|---|---|---|
| Sunucu | 1 makine, 8 core, 32 GB RAM, SSD | Disk: korpusun ~3-5 katı (raw + parsed + DB) |
| GPU | Opsiyonel; önerilen 12 GB VRAM (RTX 3060 / T4) | BGE-M3 ~2.2 GB; CPU'da embedding 10-20x yavaş |
| PostgreSQL | 16/17 + pgvector ≥ 0.7 | `pgvector/pgvector:pg17` imajı |
| Redis | FAZ 1'de OPSİYONEL | Job state Postgres status-based (v1 yaklaşımı, core_files.status) |
| Object storage | Dosya sistemi | MinIO (S3-uyumlu) FAZ 2'de |
| Python | 3.11 / 3.12 | 3.13 bazı ML paketlerinde sorunlu |
| Deployment | RHEL bare-metal (Docker'sız) — bkz. `Kurulum_RHEL_PG17_pgvector_Redis.md` | PGDG reposu ile PG17 + pgvector_17; Redis AppStream |

### Python Modülleri

| Adım | Birincil | Alternatif / Not |
|---|---|---|
| Dosya kabul, hash | `hashlib`, `python-magic` | stdlib ağırlıklı |
| Parsing | `docling` (layout-aware, tablo, OCR dahili) | Yedek: `pymupdf`, `python-docx`, `openpyxl`; alternatif `unstructured` |
| Temizlik/encoding | `charset-normalizer`, `ftfy` + v1 `document_cleaning.py` | Mevcut çekirdek taşınır |
| Dil tespiti | `lingua-language-detector` | `fasttext` |
| Chunking | Kendi modülü + `tiktoken` / HF tokenizer | `langchain-text-splitters` gerekirse |
| Injection taraması | Kural tabanlı tarayıcı (deterministik; llm-guard torch getirdiği için düştü — ADR-012 sonucu) | Model tabanlı FAZ 6: Llama Guard via Ollama/H200 |
| Embedding | `FlagEmbedding` (BGE-M3) + `torch` | `sentence-transformers` da uyumlu |
| DB | `psycopg` v3 + `pgvector` adapter + `SQLAlchemy` + `alembic` | |
| Job yönetimi | Postgres status-based | `arq`/`celery` ancak ölçek gerekirse |
| Config | `pydantic` + `pydantic-settings` | Config-first DB modeliyle birleşik |
| Log/metrik | `structlog` + `opentelemetry-sdk` iskeleti | FAZ 2 Langfuse hazırlığı |
| API | `FastAPI` + `uvicorn` | Admin tetikleme ucu |
| Test | `pytest` + dosya fixtures | Parse regresyon seti |

**İlke:** FAZ 1 tek makinede GPU'suz başlayabilir; zorunlu çekirdek = Postgres+pgvector, Python 3.11+, Docling, FlagEmbedding. Redis, MinIO ve worker framework'leri bilinçli ertelendi.

## 7c. Ajan Devreye Alma Planı (ADR-001 açılımı)

| Etap | Devreye giren | Biçim | Tetik / Gerekçe |
|---|---|---|---|
| FAZ 4 (MVP) | Tek Agentic Loop | 1 ajan | Router+Planner+Retrieval+Composer davranışları tek ajanda; tool seçimi LLM'de |
| FAZ 4 (MVP) | Validation | LangGraph node (ajan değil) | Grounding + citation kontrolü deterministik; her yanıtta zorunlu |
| FAZ 7-8 | Router Agent | Ayrı ajan | Tetik: trace'lerde sorgu tipleri arası belirgin latency/kalite farkı. Küçük/ucuz model ile sınıflandırma |
| FAZ 8 | Planner Agent | Ayrı ajan | Tetik: multi-step sorularda iterasyon bütçesi yetersizliği (golden set multi-hop skorları) |
| FAZ 8 | Validation Agent (LLM-as-judge) | Node → ajan yükseltme | Tetik: kural tabanlı check'in kaçırdığı halüsinasyon örnekleri birikirse |
| FAZ 9 | SQL/Data Agent | Ayrı ajan | Kurum içi DB/API entegrasyonuyla birlikte; text-to-SQL + şema erişim kontrolü |
| FAZ G | Graph Reason Agent | Ayrı ajan | Yalnızca GraphRAG tetiklenirse; graph search tool + traversal |
| Muhtemelen hiç | Retrieval Agent, Response Composer | Tool / loop içinde | Ek ajan sıçraması maliyetini haklı çıkarmıyor |

**İlkeler:** (1) Her bölünme Langfuse trace + golden set gerekçesi ister. (2) Bölünme sırası maliyet/getiri: önce Router (en ucuz, en yüksek getiri), sonra Planner/Judge. (3) SQL ve Graph ajanları entegrasyon işidir — kendi fazlarının çıktısıdır, öne çekilmez.

## 7d. Ortam Bilgileri (Dev)

| | |
|---|---|
| DB sunucusu | 192.168.36.15:5432 (RHEL, PostgreSQL 17 + pgvector) |
| Veritabanı / Şema | `ragintel` / `ragintel` |
| Uygulama rolü | `ragintel_app` |
| Parola | `<DB_PASSWORD>` — **TEST ortamı; prod öncesi değiştirilecek** |
| Redis | Aynı sunucu, 6379 (yalnızca iç ağ) |

**Uygulama tarafı `.env` şablonu** (repo'ya `.env.example` olarak girer, `.env` `.gitignore`'da):

```env
RAGINTEL_DB_HOST=192.168.36.15
RAGINTEL_DB_PORT=5432
RAGINTEL_DB_NAME=ragintel
RAGINTEL_DB_USER=ragintel_app
RAGINTEL_DB_PASSWORD=<DB_PASSWORD>   # TEST değeri — prod'da secret store
RAGINTEL_DB_SCHEMA=ragintel
```

**Bağlantı dizeleri:**

```bash
# psql
psql "host=192.168.36.15 port=5432 dbname=ragintel user=ragintel_app"

# SQLAlchemy / psycopg URL — paroladaki '!' URL-encode edilir (%21)
postgresql+psycopg://ragintel_app:ragintel_app%21.@192.168.36.15:5432/ragintel
```

**DB ayrı sunucuda olduğu için kurulum rehberine ek (Kurulum_RHEL... Bölüm 5/7 revizyonu):**

1. `postgresql.conf`: `listen_addresses` uygulama makinesinin eriştiği arayüzü içermeli.
   **ÖLÇÜLDÜ (2026-07-16): şu an `*`** — yani tüm arayüzlerde dinliyor ve bu madde
   karşılanmış durumda. (Bu satır önceden `'localhost, 192.168.36.15'` diyordu; canlı
   değer öyle değil — `SHOW listen_addresses;` ile doğrulanabilir.)
2. `pg_hba.conf`: uygulama makinesinin IP'si için `host ragintel ragintel_app <APP_IP>/32 scram-sha-256`.
   **DİKKAT — `<APP_IP>` istemcinin kendi IP'si olmayabilir:** ölçüm (2026-07-16),
   araya NAT girdiğini gösterdi (`SELECT inet_client_addr()` dev makinesi için
   `192.168.36.1` — yani gateway) . Doğru değeri tahmin etmeyin: bağlanmayı deneyin,
   PostgreSQL reddederse IP'yi kendisi söyler →
   `FATAL: no pg_hba.conf entry for host "X.X.X.X"`. Bağlanabiliyorsanız
   `SELECT inet_client_addr();` zaten cevaptır.
3. Firewall: 5432 yalnızca uygulama makinesinin IP'sine açık — subnet'e değil.
4. Redis ağa açılacaksa aynı prensip (6379 sadece APP_IP); mümkünse Redis'e yalnızca uygulama sunucusundan erişin.

**Güvenlik notları:** Paylaşılan parola bu sohbette düz metin geçti — dev ortamı için kabul edilebilir, ancak (a) prod'a taşınmaz, (b) repo/dokümana asla yazılmaz, (c) FAZ 6 öncesi rotasyon önerilir. `ragintel_app` süperuser DEĞİLDİR; `CREATE EXTENSION` gibi işlemler postgres rolüyle yapılır.

**LLM ortamı (remote Ollama, 2026-07-02):** Dev makinede GPU yok; ayrı sunucuda Ollama mevcut. Model envanteri: `qwen3.6-27b (27.8B)`, `bge-m3 (566.7M)`, `qwen3.5:35b (36B)`, `qwen3-coder (30.5B)`, `deepseek-coder-v2:16b`, `llama3.3 (70.6B)`, `qwen2.5-32b-instruct IQ3_M`.

- FAZ 4-5 LLM ön adayları: birincil `qwen3.5:35b` (instruct, tool-calling, çok dilli), judge/ağır iş `llama3.3:70b`. Coder modeller RAG yanıtı için aday dışı; IQ3_M agresif quant nedeniyle aday dışı. Nihai karar: FAZ 2 golden set Türkçe benchmark'ı.
- Embedding backend kuralı (İP-7): aynı korpus tek backend'le embed edilir — Ollama bge-m3 (GGUF/quant) ile FlagEmbedding FP32 çıktıları karıştırılamaz; `core_vectors.model_name` backend+quant içerir. Backend seçimi Ollama sunucusunun GPU durumuna bağlı (açık soru).
- Ollama endpoint: `http://banasor.goldenglobalbank.com.tr:11434` (→ 10.50.130.55, iç DNS) — sunucuda **NVIDIA H200**. **Ağ notu (2026-07-02):** dev makinesinden TCP 11434 kapalı (farklı segment/firewall) — istisna açılacak; açılınca İP-7 canlı smoke (`pytest -m slow`) koşulup "1000 chunk canlı" kriteri kapatılır. `.env`'e: `RAGINTEL_OLLAMA_BASE_URL=http://banasor.goldenglobalbank.com.tr:11434`. Not: Ollama auth'suzdur — endpoint yalnızca iç ağda kalmalı, dışa açılmamalı (FAZ 6'da erişim katmanı değerlendirilir).
  - **GÜNCELLEME (M-10/0, 2026-07-16):** API artık **H200'ün üzerinde konteynerde koşuyor** → Ollama'ya `http://localhost:11434` ile, **doğrudan** (Open WebUI `/ollama/v1` üzerinden DEĞİL) erişiyor. Dev-segment engeli uygulama için **aşılarak değil, ATLANARAK** ortadan kalktı: `10.50.130.55` H200'ün kendi IP'si olduğundan `network_mode: host` ile ağ atlaması hiç olmuyor (ölçüldü: `POST localhost:11434/api/embed → 200`, auth'suz). **Engel duruyor** ama yalnızca *dev makinesinden* koşan işler için (`pytest -m slow` canlı smoke). Ollama'nın auth'suz kalması kuralı aynen geçerli — üstelik artık dinleyicinin yanı başındayız.

## 7e. Observability Mimarisi — Langfuse'un Rolü ve Container İlkesi

### Langfuse'un dört amacı

1. **Trace (teşhis):** Bir yanıtın/ingest koşusunun tüm adımları (guardrail → agent → tool'lar → rerank → LLM → validation; parse → clean → chunk → embed → store) süre, girdi/çıktı ve token maliyetiyle ağaç halinde izlenir. "Yanıt neden kötü?" sorusunun cevabı dakikalara iner.
2. **Evaluation kaydı:** RAGAS/golden set koşu sonuçları Langfuse'ta birikir — "X'i değiştirdik, faithfulness ne oldu?" karşılaştırması buradan yapılır. ADR-004'ün pratikte işlediği yer.
3. **Prompt yönetimi (FAZ 5):** Prompt şablonları versiyonlanır, sürüm↔kalite ilişkisi izlenir (config-first ilkesinin prompt karşılığı).
4. **Feedback döngüsü (FAZ 7-8):** UI'daki 👍/👎 trace'iyle eşleşir; kötü yanıt analizi ve golden set büyütme buradan beslenir.

**İş bölümü:** `metrics_ingestion` = "ne oldu"nun kalıcı, dosya-granül muhasebesi (SQL ile raporlanır, FAZ 1 çıkış kriteri buradan okunur). Langfuse = "neden/nasıl oldu"nun akış-granül mikroskobu. İkisi bilinçli olarak birlikte yaşar; Langfuse kapalıyken pipeline etkilenmez (İP-2.2 kabul kriteri — fire-and-forget).

### Container (Podman) ilkesi

**Çekirdek veri ve sorgu yolu bare-metal kalır; container yalnızca destek servisleri içindir.** Podman (RHEL-yerli, rootless, daemon'sız) şu an tek amaçla kullanılır: Langfuse'un 6 bileşenli yığınını (web, worker, kendi postgres'i, ClickHouse, kendi redis'i, MinIO) pratik kurmak. Kullanıcı sorgu zincirinin (retrieval → LLM → yanıt) hiçbir adımı container'dan geçmez. Langfuse'un iç postgres/redis'i host'a port açmaz — ragintel DB'si ve host Redis'ten tamamen izoledir. İleride (FAZ 10) başka yardımcı servisler için Podman kullanılabilir; ilke değişmez.

## 8. Açık Sorular

1. Golden QA seti: kaç soru, hangi doküman havuzu, kim etiketleyecek? (hedef: ≥100 soru, çok belgeli/multi-hop örnekler dahil)
2. LLM model kararı: Qwen3 hangi boyut? GPU bütçesi/donanım envanteri netleşmeli (vLLM planı için). **Güncelleme 2026-07-02: dev/test ortamında GPU YOK — FAZ 1 embedding CPU-only (batch=8). FAZ 4-5'te local LLM çalıştırmak için GPU temini kritik ön koşul; prod GPU envanteri hâlâ açık.**
3. Chunking varsayılanları: v1'deki paragraph/section-based ayarlar v2 golden set ile yeniden doğrulanacak mı?
4. PII masking regülasyon kapsamı (KVKK): maskelenmesi zorunlu alan listesi.
5. Tenant modeli: tek kurum çok departman mı, gerçek multi-tenant mı? (RLS şemasını etkiler)

## 9. Sonraki Adımlar

- [ ] FAZ 1: Docling ile parser PoC (mevcut `document_cleaning.py` pipeline'ına giriş)
- [ ] Golden QA seti v1 (Türkçe, ≥100 soru) — eş zamanlı
- [ ] Langfuse self-hosted kurulum + FastAPI/LangGraph tracing entegrasyonu
- [ ] BGE-M3 vs Qwen3-Embedding Türkçe benchmark (golden set ile)
- [ ] pgvector + tsvector + RRF hybrid search PoC ve RAGAS ölçümü
- [ ] LangGraph tek agentic loop iskeleti (LiteLLM + Ollama, PostgresSaver)

## 10. Değişiklik Günlüğü

| Tarih | Versiyon | Değişiklik |
|---|---|---|
| 2026-07-02 | 0.1 | İlk sürüm: mimari analiz, ADR-001..010, revize yol haritası, MVP kapsamı. `rag_V2_updated.drawio` üretildi. |
| 2026-07-02 | 0.2 | Bölüm 7b eklendi: FAZ 1 minimum altyapı ve Python modül seçimi. `RAG_v2_Yol_Haritasi.docx` üretildi. |
| 2026-07-02 | 0.3 | Deployment kararı: RHEL bare-metal, Docker'sız. `Kurulum_RHEL_PG17_pgvector_Redis.md` eklendi. |
| 2026-07-02 | 0.4 | Bölüm 7c eklendi: ajan devreye alma planı (hangi ajan hangi fazda, tetik kriterleriyle). |
| 2026-07-02 | 0.5 | `Tasarim_FAZ4_Agentic_Loop.md` eklendi: state şeması, graf topolojisi, tool/validation kontratları, FAZ 1-3'e geri beslenen gereksinimler. |
| 2026-07-02 | 0.6 | Çalışma modeli netleşti: kod VS Code'da (Sonnet 5), teknik plan/spesifikasyon burada. `FAZ1_Sema.sql` ve `FAZ1_Is_Plani.md` (İP-0..İP-10) eklendi. Altyapı kurulumu (PG17+pgvector+Redis) tamamlandı. |
| 2026-07-02 | 0.7 | `FAZ1_Veri_Sozlugu.md` eklendi: tablo amaçları, kolon sözlüğü (dolduran İP / tüketen faz eşlemesiyle), tasarım gerekçeleri. |
| 2026-07-02 | 0.8 | Bölüm 7d eklendi: dev ortam bilgileri (DB 192.168.36.15), .env şablonu, uzak-DB için pg_hba/firewall ekleri, parola güvenlik notları. |
| 2026-07-02 | 0.9 | 7d: test ortamı parolası kullanıcı onayıyla dokümana eklendi (prod öncesi rotasyon şartıyla); hazır URL-encoded bağlantı dizesi. |
| 2026-07-02 | 1.0 | ADR-011 eklendi: aşama bazlı kalite skorlama. `FAZ1_Sema_Ek1_Kalite.sql` (additive migration), İş Planı Ek-A, veri sözlüğü v0.2. |
| 2026-07-02 | 1.1 | ADR-012 (embedding=Ollama/H200), Ek2-Ek3 migration'ları, İP-4 kural-tabanlı revizyonu. **FAZ 1 KOD TAMAM** (İP-0..10, 131 test). Resmi kapanış koşulu: Ollama ağ istisnası → canlı smoke + gerçek korpus koşusu + `ragintel report ingestion` çıktısının ≥%95 parse kriterine karşı değerlendirilmesi. |
| 2026-07-02 | 1.2 | ADR-013 (Langfuse/Podman) + Bölüm 7e: Langfuse'un dört amacı, metrics↔Langfuse iş bölümü, container ilkesi. `Kurulum_RHEL_Langfuse_Podman.md` + doldurulmuş `.env`/compose dosyaları üretildi. FAZ 2 planı yayında (`FAZ2_Is_Plani.md`). |
| 2026-07-03 | 1.3 | **Langfuse v3 KURULDU ve erişildi** (http://192.168.36.15:3000, "RAG v2" projesi). Kurulum dersleri rehbere işlendi: ClickHouse `latest` → 24.8 pin (hibrit CPU/hypervisor "illegal instruction"), secret'lar hex-only (base64 `+` ClickHouse URL auth'unu bozdu). Sunucu RAM'i 16 GB olarak netleşti — PG17 tuning düşürüldü (shared_buffers 4GB). Kalan altyapı engeli: Ollama firewall istisnası. |
| 2026-07-16 | 1.4 | **M-10/0 — API H200'E TAŞINDI ve konteynerde KOŞUYOR.** Rehberler: `M10_H200_Kurulum.md` (uygulama) + `M10_Deploy_Kurulum.md` (kararlar/ölçümler). **(a) Deployment kararı REVİZE:** v0.3'teki "RHEL bare-metal, Docker'sız" ilkesi **API için** kalktı — `ragintel-api` Docker imajı (torch/docling HARİÇ; ölçüldü: API import zinciri docling'e dokunmuyor, `--no-deps` şart). DB/Redis bare-metal, Langfuse Podman: **değişmedi**. **(b) Doğrudan Ollama:** `localhost:11434` (host-network) — Open WebUI devre dışı; hem ağ atlamasını hem M-9.1'deki tool-call JSON arızasının şüpheli katmanını kaldırır. **(c) Tokenizer imaja gömüldü:** soğuk yükleme ~12 sn → **1.4 sn**, runtime'da internet şartı YOK. **(d) Bootstrap config kararı (M-4/3) GENİŞLETİLDİ:** os.environ zincire eklendi ama `.env`'in ARKASINA (`init > .env > os.environ > default`) — M-4'ün amacı (bayat host env `.env`'i ezemez) korunuyor; imajda `.env` yoktur ve olmamalı, o yüzden compose'un geçirdiği değerler okunabilmeliydi. **(e) Ölçüm düzeltmeleri (7d):** `listen_addresses` gerçekte `*`; `pg_hba` için istemci IP'si NAT nedeniyle gateway görünüyor (`inet_client_addr()`). **Bekleyen:** stabilizasyon testi (kayıtlı istek ×20 → suçlu WebUI muydu?) ve sonrasında temiz k=1 karne. |

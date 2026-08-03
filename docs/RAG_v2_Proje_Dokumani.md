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

### ADR-014 — Rerank serving: TEI, parametrik endpoint (önce CPU, sonra H200)
- **Karar:** bge-reranker-v2-m3, Hugging Face TEI (text-embeddings-inference) container'ı ile servis edilir (Podman). Başlangıç: DB sunucusunda CPU imajı; H200 erişimi açılınca GPU TEI'ye geçiş yalnızca `RAGINTEL_TEI_RERANK_URL` değişikliğidir. Uygulama tarafında `rerank_backend` config'i: `passthrough | tei`. TEI erişilemezse **fail-open**: passthrough'a düşer, span'e işaretlenir, log'lanır — rerank kalite artırıcıdır, kritik yol değildir (retrieval rerank'siz de çalışır).
- **Gerekçe:** Ollama rerank API'si sunmuyor; torch projede yok (ADR-012); TEI hem CPU hem GPU imajıyla aynı API'yi verir — model serving = uzak HTTP ilkesiyle (Ollama gibi) tutarlı. H200 takvimi belirsiz; CPU rerank (~200-800ms/10 doküman) dev için kabul edilebilir, katkısı İP-3.6'da ölçülür.
- **Durum:** Kabul edildi (2026-07-03). **Güncelleme (2026-08-03: rerank A/B ön-verisi, changelog v1.11):** TEI CPU'da ölçüldü (kalite CPU/GPU özdeş → nvidia-toolkit kurulmadı). Blanket `rerank_backend='tei'` prod'da **AÇILMADI** — HEDEF multi_hop recall@5 −0.200 + single_fact −0.100 bozuyor, GENEL net +0.032'yi telafi etmez (mekanik: cross-encoder chunk'ları tek tek puanlar → multi-hop ikinci-sıçrama kanıtını top-5'ten iter). TEI-GPU kurulumu **ERTELENDİ** (reddedilmedi; orijinal gerekçe=multi_hop düştü). Güçlü olduğu table_based (+0.400) / synthesis (+0.143) için **kategori-koşullu rerank** backlog'a alındı (görev #11) — prod'da kategori etiketi yok, gating sinyali gerekir; küçük-n: **yön güvenilir, büyüklük kırılgan**. Adım-1 planı: `Brief_M11_Kategori_Kosullu_Rerank_Adim1`. Bkz. `rerank-ab-onveri`.

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
- ~~P95 uçtan uca yanıt < 15 sn~~ → **M-15'te yeniden tanımlandı** (aşağı bak)

**Gecikme kriteri — M-15 revizyonu (2026-07-24).** Özgün kriter *"P95 uçtan uca < 15 sn"*
idi. M-15 anatomisi ([M15_Latency_Anatomisi.md](M15_Latency_Anatomisi.md)) bunun **kalite
korunarak ulaşılamaz** olduğunu ölçtü: süre tur sayısı × (prompt-eval + üretim); decode
tavanı ~110 tok/s, prompt-eval 0.33 ms/tok — ikisi de sabit. Kalan davranış-nötr kollarla
tavan p95 ≈ 19 sn. Tek gerçek kaldıraç modelin ürettiğini değiştirmekti; denendi (kol-1) ve
fallback'i %9.68 → %20.4'e çıkardığı için **reddedildi** ([M15_Kol1_Red_Kaydi.md](M15_Kol1_Red_Kaydi.md)).

Yerine geçen üç kriter (tek kullanıcı, local LLM):

| kriter | eşik | durum |
|---|---|---|
| **p50** uçtan uca yanıt | < 15 sn | **SAĞLANIYOR** — ölçülen 11.5-13.3 sn |
| **TTFB** (kullanıcının ilk geri bildirim aldığı an) | < 1 sn | `/api/ask/stream` ile ~0.1 sn |
| p95 uçtan uca yanıt | *gösterge* — eşik değil | ölçülen 21.1-21.3 sn; **kol-2 (a+b) canlıda `4d0e815`** → hedef ~19 sn |

**Neden p95 eşik olmaktan çıktı:** p95'i belirleyen şey çağrı hızı değil **tur sayısı**
(ort. 3.5, max 5) — yani sorunun kaç adımda çözüldüğü. Bunu eşiğe bağlamak, sistemi zor
soruyu erken bırakmaya teşvik eder; M-9'da görülen "sapkın gate teşviki" deseninin aynısı.
Gösterge olarak izlenir, gerileme raporlanır, ama fix'in kabul şartı değildir.

**kol-2 kapanışı — prefix (KV-cache) disiplini (2026-07-31, canlıda `4d0e815`).** M-15'in latency
kolu iki parçada mühürlendi. **(a)** tur sayacı sistem prompt'undan en-son mesaja taşındı (commit
`491de56`) → sistem mesajı turlar arası byte-özdeş. **(b)** `context_builder.build()` **append-only**
(stateful) yapıldı (commit `ef5f455`): gösterilmiş bloklar numarasıyla+bytes'ıyla korunur, yeni chunk'lar
yalnız sona eklenir → mesaj-2'deki ~2500 token bağlam her tur yeniden numaralanmaz. Ölçülen kaldıraç:
Ollama ardışık `/api/chat` çağrılarında byte-özdeş prefix'in KV-cache'ini yeniden kullanıyor (prob commit
`dfd62db`: STABLE 2. çağrı prompt-eval 97 ms vs MUTATED 694 ms).

**Davranış-nötrlük — k=3 A/B karne (izole worktree, aynı prod DB + config parmak-izi).** kol-2'nin
karnesi latency için değil, **kalite regresyonu olmadığını** kanıtlamak için şart: baseline↔(b) — honesty
**12/15 birebir** (D4 taban), fallback **0.2366→0.2258** (artış yok), faithfulness **0.8939→0.8899**
(Δ −0.004), context_precision **0.8854=0.8854**. 4/4 kapı yeşil → kabul. Canlıda `4d0e815` smoke'u groundlu
cevap (4 kaynak, gerçek `quote`'lar) + aşama streaming'i döndürerek (b)'yi çalışan sistemde doğruladı.

**#4 (tool-şemasını her tur göndermeme) — ön-veri ile elendi (prob commit `4d0e815`).** Tool şeması qwen
chat-template'inde sistem prologuna, bağlamdan önce render edilir → zaten (b)'nin dondurduğu cache'li
prefix'in parçası (byte-özdeş tool → 2. çağrı 295 ms cache-hit). Şemayı tur başına düşürmek prologun önünü
değiştirip tüm prefix'i cache-miss yapardı (aynı prob: token sayısı 3455→2732 düşse de süre 295→679 ms) —
yani tur başına ~597 ms geri gelir, (b) tersine döner. **Kod yazılmadı** (ön-veri kuralı).

**Dürüst sınır.** M-15'in çekirdek kriteri (p50 < 15 sn + TTFB < 1 sn) zaten M-15 kapanışında yeşildi;
kol-2 bunların üstüne p95'i ~21 sn'den ~19 sn'e çeken **ek** kazanç. Prefix cache tek model-slotu →
eşzamanlı yükte erir; ~2 sn tasarruf yalnız sıralı/tek-kullanıcı akışında gerçek (M-15'in "kapasite ayrı
kalem" ilkesiyle tutarlı, aşırı iddia yok).

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
| 2026-07-31 | 1.5 | **M-14 (wiring) — çok-tur oturum hafızası (agent-pull).** Mekanizma `memory_search` tool'u: geçmiş, LLM'in tool çağrısıyla **tail'deki tool-result'tan** gelir → prepare'in her tur `messages=None` reset'ini VE kol-2 prefix (KV-cache) disiplinini **BOZMAZ** (donuk head'e seed edilmez). `conversation_id` (=session_id) RUNTIME enjekte edilir — **LLM argümanı DEĞİL**; şema yalnız `query` içerir (oturum kimliği model tarafından üretilemez/atlatılamaz, search_hybrid'deki `user_ctx` deseniyle aynı). **Sahiplik fail-closed** (`get_conversation_messages` None/boş → boş hafıza); son 3 tur (=6 mesaj) döner, `{role,content}`'e sanitize (chunk/scope/trace sızmaz). Ön-koşul: M-13 DDL (`FAZ7_Sema_Ek2_Conversations.sql`) canlıda uygulanmış (kayıt akıyor). Testler H200 default alt-kümesinde **yeşil** (M-14 sözleşmesi 11 vaka + kol-2/grounding/honesty regresyon). **YAN BULGU (ölçüm-zemini):** kol-2(b) `ef5f455`'ten beri iki test fake'i (`test_faz4_graph_flow`, `test_faz6_auth`) `prior=` kwarg'ını kabul etmiyordu → graph-flow birim çiti **gizli-kırmızıydı**, fark edilmemişti çünkü (b) mührü k=3 canlı karne + smoke'a dayandı, bu alt-küme (b) sonrası koşulmadı. **Canlı üretim etkilenmedi** (gerçek `ContextBuilder.build` `prior` kabul ediyor, smoke geçmişti); kırık olan yalnız fake'lerdi → append-only sözleşmeye sadık düzeltildi (`67ea30a`). **CANLIDA `c5f4e45` (2026-07-31), wiring KANITLI:** isim-izolasyon smoke'u (Tur-1 "Benim adım Erdal…", Tur-2 aynı oturumda "adım neydi") + salt-okur DB probu → `memory_search` önceki turları modele **eksiksiz teslim ediyor** (reader 4 mesajı sahiplik-kontrollü döndürdü; kayıt/zamanlama/sahiplik sağlam). **AMA BULGU (by-design sınır):** Tur-2 declined/fallback döndü — çünkü `submit_answer` sözleşmesi her iddiayı bir **bağlam bloğu (chunk)** citation'ıyla ister; memory tool-result'u bağlam bloğu DEĞİL (chunk_id yok) → model adı citelayamayıp doğru biçimde reddediyor (honesty D4 tutarlı). Sonuç: mevcut wiring konuşma hafızasını **reference-resolution**'a yarar kılar (agent "o konu"yu çözüp yine chunk-groundlu cevaplar), **standalone/meta recall**'a ("adım neydi", "ne konuştuk") DEĞİL. Hafızayı citelenebilir kaynak yapmak ayrı bir tasarım kararı (honesty/grounding fenced zone — M-16/M-17). **REF-RESOLUTION ÖLÇÜMÜ (A/B, canlı `c5f4e45`):** Tur-1 "Karbon vergisi nedir?", Tur-2 topic-siz zamirle "Peki bunu hangi ülkeler uyguluyor?" — A (aynı oturum, hafıza var) vs B (yeni oturum, hafıza yok). SONUÇ: A'da `memory_search` **HİÇ çağrılmadı** (agent doğrudan `search_hybrid`'e gitti); A≡B birebir (aynı sorgular, aynı 21306 token, ikisi de declined) → hafıza davranışa **sıfır etki**. Kök: prepare reset'i agent'ı önceki turlara kör bırakıyor, `memory_search`'ü ancak soru pür-meta göründüğünde (retrieve edilecek şey yokken) çağırıyor; invocation phrasing'e bağlı ve güvenilmez. **Ölçülmüş verdict: wiring-only pratik değer vermiyor** — reference-resolution'a girmiyor (invocation yok), meta-recall'a giremiyor (honesty). Pratik çok-tur faydası için EN AZ prompt-nudge (invocation'ı zorla) gerekiyor; kullanıcının "prompt değişmesin" kapsamı bunu erteliyor. **KARAR (2026-07-31): M-14 wiring-complete-but-DORMANT olarak RAFA KALDIRILDI** — kod/prompt değişmez, çatal (nudge / grounding-eligible) backlog'da bekliyor; sıradaki iş gs-034 grounding margin. |
| 2026-08-03 | 1.12 | **Görev #11 Adım-1 (kategori-koşullu rerank) KAPANDI — kategori-koşullu rerank GEREKÇESİ KANITLANDI; fizibilite: gate YALNIZ table_based (güvenli), synthesis güvenle hasat EDİLEMEZ. Değişen üretim kodu YOK.** 1.11'in backlog #11'i: table/synthesis kazancı büyük-n'de tutuyor mu, ve canlıda kategori etiketi olmadan gate kurulabilir mi? **Golden `rr-ext-v1`** (16 table_based + 15 synthesis, Sonnet-taslak→**İNSAN onaylı** §1c kapısı; `docs/golden_rr_ext_v1.jsonl` + gerekçeli taslak `docs/golden_aday_M11_taslak.md`) additive üretildi (**v0 DOKUNULMADI**), quote→chunk mapping **%100** (46/46, `retrieval --from-file` ile DB'ye yazmadan kapı geçildi). **(1) table_based n=16: rerank YARDIM** — recall@10 +0.125, ndcg@10 +0.092, mrr +0.069; v0 table (n=5) recall@5 +0.400 ile **aynı yön → İKİ BAĞIMSIZ SET.** **(2) rr-ext synthesis ÖLÇÜLEMEDİ — ölçü-aracı kusuru (kendi hatam):** recall@20 **%3.3**; kök sorularım **iki-parçalı birleşik** ("X nasıl sıralanır VE Y nedir") → sorgu-embed hiçbir chunk'a işaret etmiyor, dilüe. Retriever SAĞLAM: **v0 synthesis** (n=7, aynı gold_n=2, aynı korpus/retriever) recall@20 **0.714**. **ANTİ-PATTERN dersi:** synthesis golden = tek-niyetli soru, cevabı 2 semantik-yakın chunk'ı birleştirir; iki-parçalı "…ve…" YASAK. **(3) v0 synthesis A/B (GEÇERLİ araç, n=7): rerank AÇIK ARA YARDIM** — recall@5 **+0.143**, recall@10 +0.214, mrr +0.217, ndcg@10 +0.203 → orijinal "+0.143" BİREBİR geri geldi (önceki "tekrarlanmadı" bozuk-araç eseriydi, erozyon değil). **(4) v0 tüm kategori tekrarı (fail-open yok, recall@20 özdeş):** table +0.400, synthesis +0.143 🟢 / single_fact −0.100, **multi_hop −0.200 🔴 (bayrak)** → 1.11 ön-verisini tekrarladı; GENEL net-pozitif (recall@5 +0.032) multi_hop zararını **MASKELİYOR** → blanket hâlâ yanlış. **ADIM-2 FİZİBİLİTE (sorgu metninden kategori ayrılıyor mu?):** **table_based → TEMİZ AYRILIYOR** ("tablo"/"tabloya göre" markerı: 5/5 v0 + 16/16 rr-ext'te VAR, multi_hop/single_fact/synthesis'te HİÇ YOK → yüksek kesinlik). **synthesis vs multi_hop → sorgu yüzeyinden AYRILMIYOR:** ikisi de iki-parçalı bağlaç ("…ve… nasıl/nedir"), fark **semantik** (synthesis=aynı belge/tema 2 chunk; multi_hop=farklı belge akıl-sıçraması) — İKİZ: v0-016 (synthesis, "SKDM hangi amaçla geliştirildi **ve** ithal ürünler için nasıl maliyet") vs v0-024 (multi_hop, "SKDM nasıl işler **ve** hangi sektörleri etkiler"). **KARAR: rerank gate'i YALNIZ table_based için aç** (kesinlik-öncelikli tablo-niyet dedektörü: "tablo" + "kaç dolar/sent/ton"/"yüzde kaç" hücre-arama kalıpları), gerisi OFF. En büyük + 2× teyitli kazancı (+0.400/ndcg +0.092) ~sıfır multi_hop riskiyle alır; "tablo" demeyen gerçek soru boost almaz ama bu **GÜVENLİ** başarısızlık (baseline'a düşer). **Synthesis kazancı gerçek ama güvenle HASAT EDİLEMEZ** — multi_hop'tan sorgu-metniyle ayırt edilemiyor, synthesis'i açan gate kaçınılmaz multi_hop'u da açar → −0.200 flagship. Adım-2 böylece "5-sınıf sınıflandırıcı" (zor/riskli) → **"tablo-niyet dedektörü"** (küçük/güvenli) küçüldü; sızıntı riski nedeniyle kendi gate-isabet eval'i (multi_hop yanlış-pozitif) yine şart. **KÜÇÜK-n uyarısı yürürlükte:** yönler çift-koşum+eski-teyitle güvenilir, büyüklükler n=4-16 kırılgan. **Verdict: kategori-koşullu rerank gerekçesi kanıtlandı; güvenli hasat yüzeyi YALNIZ table_based; synthesis retriever'da değil ölçü-aracımda tıkandı (ders çıkarıldı). Değişen üretim kodu yok — eklenen: onaylı golden `rr-ext-v1` + taslak.** [[on-veri-kontrolu-kural]] [[olcum-zemini-dersleri]] |
| 2026-08-03 | 1.11 | **TEI rerank A/B ön-veri (Milestone #1 §7 re-baseline) — rerank multi_hop HEDEFİNİ ÇÖZMÜYOR, KÖTÜLEŞTİRİYOR; TEI-GPU ERTELENDİ, blanket rerank AÇILMIYOR. AKSİYON YOK.** Milestone #1'in sorusu: TEI rerank (bge-reranker-v2-m3) §7'nin işaretlediği multi_hop recall@5 açığını çözer mi? **CPU'da ölçtük** (kalite CPU/GPU'da özdeş — aynı model/skor; GPU yalnız latency → nvidia-container-toolkit kurulumuna gerek YOK, "önce CPU'da ölç" kararı doğrulandı). Araç `scripts/rerank_ab_probe.py` (commit `fa0057b` + düzeltme `fea7485`): passthrough vs TEI, golden v0, judge'sız/deterministik; `StoreRetriever` AÇIK `rerank_fn` ile DB-pinli `rerank_backend`'i baypas eder (prod/DB/config'e DOKUNMAZ, TEI URL yalnız process ENV'inde); baseline üretim hybrid parametreleriyle (ef_search/rrf_k/ağırlıklar cfg'den → karne zemini). **ÖLÇÜM-ZEMİNİ YAKALAMA (ilk koşu VOID):** ilk çalıştırmada Δ tümüyle sıfır çıktı → probun NOT'u tuzağı yakaladı; loglar `rerank_fallback: timed out` gösterdi. Kök: prod `_tei_rerank` config `rerank_timeout_sec` (~5s) içinde 20 adayı CPU'da (bge v2-m3 batch>4 desteklemez → 5 seri batch) sıralayamadı → SESSİZCE passthrough'a düştü → B fiilen passthrough ölçtü. Düzeltme (`fea7485`): prob rerank'i KENDİ İÇİNDE, fail-open'sız, cömert timeout'la (`--rerank-timeout` vars. 120s) yapar → **kalite ölçümü ASLA sessiz-düşmez, gürültülü çöker.** **GEÇERLİ SONUÇ (2. koşu, fallback logu YOK, `recall@20` her kategoride ÖZDEŞ = havuz aynı sağlık-kontrolü geçti) — KATEGORİYE GÖRE KESKİN BÖLÜNMÜŞ:** GENEL n=31 net pozitif (recall@5 +0.032, **recall@10 +0.081**, mrr +0.035); **table_based recall@5 +0.400** (2/5→4/5) 🟢, **synthesis +0.143** (recall@10 +0.214, mrr +0.217) 🟢, citation nötr, **single_fact −0.100** (9/10→8/10) 🔴, **multi_hop (HEDEF) recall@5 −0.200** (2/5→1/5, mrr −0.071, ndcg@5 −0.111) 🔴. **Mekanik:** cross-encoder her chunk'ı sorguya TEK TEK puanlar → multi-hop'ta ikinci-sıçrama kanıtı tek başına zayıf göründüğü için top-5'ten itiliyor (bilinen cross-encoder zaafı). **KÜÇÜK-n UYARISI (mühre dahil): YÖN GÜVENİLİR, BÜYÜKLÜK KIRILGAN** — kategori n'leri 4-10; recall@5'te birim 0.20/soru → "multi_hop −0.200" = TEK sorunun gold'u top-5'ten düştü, "table +0.400" = 2 soru; sayılar deterministik/tekrarlanabilir (RAGAS gürültüsü yok) ama 5 soru tüm kategoriyi temsil etmez → +0.400'ü mutlak gerçek sanma. **§7 multi_hop PREMİSİ İKİLİ ÇÜRÜDÜ:** passthrough şimdi **0.400** (§7'nin dayandığı ~0.30 DEĞİL — korpus/config drift'i açığı zaten büyük ölçüde kapatmış) VE rerank onu çözmüyor, kötüleştiriyor → §7'nin "rerank multi_hop lever'ı" premisi geçersiz (bu oturumun bayat-premise ailesine katılır: "12/15 tavanı", "§5=API kapasitesi", "büyük-context KV"). **AKSİYONLAR:** (1) **TEI-GPU prod kurulumu ERTELENDİ** — orijinal gerekçe (multi_hop) düştü; *reddedilmedi*, geri dönüşü conditional-rerank backlog'una bağlı. (2) **`rerank_backend='tei'` blanket prod'da AÇILMIYOR** — flagship multi_hop −0.200 + single_fact −0.100 bozar, net +0.032 telafi etmez. (3) **YENİ BACKLOG (kategori-koşullu rerank, yalnız table/synthesis):** güçlü sinyal (+0.400/+0.143) ama basit "kategoride aç" DEĞİL — prod'da kategori etiketi YOK (golden'da var, canlıda soru-tipi bilinmiyor) → dürüst kapsam: (a) sorgu-tipi sınıflandırıcı / gating sinyali (kendi başına hataya açık), (b) kategori başına daha büyük golden (magnitude için), (c) sonra koşullu rerank ölçümü. Küçük iş değil. **Verdict: pahalı prod-kurulum (nvidia-toolkit) + config-flip (blanket rerank), CPU'da ucuz ölçümle KURULMADAN/AÇILMADAN reddedildi/ertelendi — hedef çözülmüyor, bozuluyor; yan-fırsat (table/synthesis) dürüst kapsamıyla backlog'a. Değişen kod yok (yalnız salt-ölçüm aracı eklendi).** [[on-veri-kontrolu-kural]] [[olcum-zemini-dersleri]] |
| 2026-07-31 | 1.10 | **Kapasite bind-noktası ön-veri (1.8'in devamı) — SERİLEŞMENİN KÖKÜ KESİN: `OLLAMA_NUM_PARALLEL=1`; iki seri kapı seri bağlı → lock-removal-tek-başına ERKEN (KANITLI). AKSİYON YOK.** 1.8 "lock kalkınca sıradaki tavan Ollama" demişti; bu prob (`scripts/kapasite_bind_noktasi_probe.py` commit `20909be`; kod/kilit DEĞİŞMEDEN, salt-okur) eşzamanlılığın NEREDE bağlandığını attribute etti. **Test A** (2 eşzamanlı Ollama-direct, Barrier ile eş-başlangıç, KİLİT YOK): bireysel **1616ms / 3036ms**, duvar-saati 3036ms, **paralel_faktör 1.53**. **ELLE OKUMA (otomatik "KISMİ" hükmü yanlış-kalibre):** Barrier eş-başlangıçta metrik cebirsel `pf = 1 + min/max` → tam-**seri** tabanı **1.50** (1.0 DEĞİL; 2. istek süresi ortak start'tan ölçülünce kuyruk beklemesini içerir), tam-paralel 2.00. Ölçülen min/max=0.53≈0.50 + bireysel oran 3036/1616≈1.9×2 = **klasik bekle-sonra-çalış imzası → Ollama SERİLEŞTİRİYOR.** (Paralellik olsaydı contention iki isteği simetrik yavaşlatır, min/max→1; asimetri kuyruğun tellidir.) **KÖK — server config log'u kesin:** `OLLAMA_NUM_PARALLEL:1` (systemd `Environment=` bunu SET ETMİYOR → Ollama'nın çözdüğü varsayılan), `OLLAMA_CONTEXT_LENGTH:0` (262144 model-max ZORLA ayrılmıyor → serileşme **KV baskısı değil salt config**; "büyük-context KV'yi patlatıyor" tahminim çürüdü), `OLLAMA_MAX_QUEUE:512` (2. istek kuyruğa girer). **KRİTİK SONUÇ — iki seri kapı seri bağlı:** app `_lock` (tek-bağlantı saver) VE Ollama `NUM_PARALLEL=1`; **birini tek başına kaldırmak API'de sıfır kazanç** (öteki yine serileştirir) → 1.8'in "lock-removal tek başına erken" hükmü artık *kanıtla* mühürlü, spekülasyon değil. **Ucuz doğrulama deneyi ERTELENDİ (salt-okur DEĞİL):** `NUM_PARALLEL=2` + Test A tekrarı Ollama restart (canlı H200 kısa kesinti + 34.5GB reload) gerektirir ve 36B MoE'de kazanç belirsiz (expert-routing batching'i zayıflatır) → kapasite milestone'unun bilinçli ilk hamlesi, bugün koşulmadı. **Verdict: serileşmenin kökü kesin (config `NUM_PARALLEL=1` + app lock, seri bağlı); mimari değil ayarlanabilir bir kapı ama lift = infra kararı + çift değişiklik. Şimdilik aksiyon yok, değişen kod yok.** [[on-veri-kontrolu-kural]] |
| 2026-07-31 | 1.9 | **FE deploy doğrulaması — TEMİZ GEÇTİ (canlı), değişen kod yok.** FE = tek statik `index.html`, `GET /`'te her istekte diskten okunup sunulur (`app.py:109`), imaja gömülü → sunulan sürüm = deploy commit'in dosyası. (1) GÜNCELLİK: `index.html` en son `a2ecbd2` (24 Tem) değişti, de5dacf..HEAD arası SABİT; canlı `curl / | md5sum` = **2adb3e7e…** repo HEAD ile **byte-eş** → sunulan FE = deploy build = repo HEAD. (2) UÇ YÜZEYİ: `create_app` (`app.py:213-223`) M-12 auth + M-13 conversations + FAZ7 admin dahil hepsini kaydediyor; `git_sha=de5dacf` (güncel). (3) AUTH ZİNCİRİ: canlı `POST /api/login` bogus-cred → **401** (503 değil; login public uç, guard yok) → auth tablosu/DDL yürürlükte, FE login CANLI. MEMORY DÜZELTMESİ: `m12-email-auth-pending-apply` adı tarihsel — M-12 DDL zaten uygulanmış (9/9 kabul 2026-07-20), 401 bugün de canlı doğruladı. Ask yolu M-13 canlı kabul 6/6 + prefix smoke ile zaten kanıtlı. **Verdict: FE byte-güncel, uçlar kayıtlı, auth canlı, health healthy → deploy doğrulandı; aksiyon yok.** |
| 2026-07-31 | 1.8 | **Kapasite / eşzamanlılık ön-veri — MİMARİ TAVAN, ŞİMDİLİK AKSİYON YOK (canlı teyitli).** Çatal: gerçek 2. kullanıcı §5'in 170.6s thrash'ini mi yaşar, yoksa lock kuyruğunu mu? Kanıt (kod+deployment+canlı): API `_lock` **tüm `run_agent`'ı** sarıyor (`runtime.py:215`, sebep tek-bağlantılı PostgresSaver) → eşzamanlı `/api/ask` **tümüyle serileştiriliyor.** Deployment tek worker: `Dockerfile:118` uvicorn `--factory` **`--workers` yok**, compose'da command override / `deploy.replicas` yok. CANLI TEYİT (`docker exec ragintel-api`): `/proc` cmdline dökümü **tam 1 uvicorn süreci**, `WEB_CONCURRENCY`/`UVICORN_WORKERS` unset, `/api/health` healthy `git_sha=de5dacf` (konteyner güncel). ⇒ tek lock → API kapasitesi **seri kuyruk**, tavan ≈ 1/ort_latency ≈ **~4-5 istek/dk**; N kullanıcı p95 ≈ (N-1)×ort + kendi_p95 (doğrusal, patlama YOK). **§5'in 170.6s'i API'yi tarif ETMİYOR** — eval-app'te (`harness.py:125` checkpointer=None, lock yok) iki kilitsiz süreç Ollama thrash'iydi; gerçek API'de ulaşılamaz. Bağlayıcı kısıt Ollama değil **tek-bağlantılı PostgresSaver → global lock**; Ollama contention ancak lock KALKINCA sıradaki tavan (batching/replica). **Bir yük testi koşmadık — yanlış sayıyı ölçerdi** (lock=sıkıcı kuyruk ya da eval-thrash=prod-dışı); ön-veri disiplini kod+deployment+canlı-teyitle çözdü. ÖLÇÜM HİJYENİ DERSİ: ilk worker-sayımı `grep -o uvicorn` self-referential çıktı (komutun kendisi "uvicorn" içeriyordu → 3 saydı); cmdline dökümü 1'e düzeltti → [[olcum-zemini-dersleri]]. **Verdict: kapasite mimari bir tavan; kaldırmak çok-bağlantılı/pool'lu checkpointer = ayrı milestone. Şimdilik aksiyon yok, değişen kod yok.** |
| 2026-07-31 | 1.7 | **gs-v0-029 latency outlier (16.8s LLM-dışı) — SALT-OKUR ANATOMİ İLE KAPANDI, AKSİYON YOK.** Çıpa (`M15_Latency_Anatomisi.md:145`): bir koşumda gs-029 24.1s, LLM payı %30, **16.8s retrieval+embed+DB** tarafında (zemin ~1.5s); "tek gözlem, incelenmedi". Prob (`scripts/gs029_retrieval_anatomi.py` commit `de5dacf`; kod/config DEĞİŞMEDEN retrieval kovasını graph'tan İZOLE ölçer) gs-029 sorgusunu **warm ×5** koştu: KOVA med **136ms** (embed 123ms Ollama / db 10-13ms pgvector / rerank **0.2ms** — backend `passthrough`, TEI deploy edilmemiş → `rerank_timeout_sec=5.0` devre dışı). **16.8s ~120× → REPRODÜKTİF DEĞİL.** İki aday elendi: (a) soğuk TEI-timeout — backend passthrough, no-op; (b) kalıcı per-query maliyet — embed+db warm'da kaya gibi. Geriye tek makul açıklama: **tek-akışlı Ollama contention** (outlier koşumu eşzamanlı generation'la çakışıp embed'ler kuyrukta bekledi; §5 kapasite bulgusuyla tutarlı). DÜRÜST SINIR: prob warm+contention-siz ölçer → outlier'ı ÜRETMEZ, yalnız 16.8s'in **intrinsik olmadığını** kanıtlar; outlier'ı üretecek şey (contention) zaten AYRI kalem = kapasite/eşzamanlılık. **Verdict: sızıntı retrieval'da değil; gs-029 aksiyon yok ile kapandı. Değişen kod yok.** [[on-veri-kontrolu-kural]] |
| 2026-07-31 | 1.6 | **backlog #8 (gs-034 grounding + entailment-ON) — İKİ SALT-OKUR PROBLA KAPANDI, AKSİYON YOK.** Ön-koşul teyidi (canlı konteyner, `git pull` öncesi HEAD=`c5f4e45`; NOT: konteyner `GIT_SHA` env'i yok, sha kaynağı `/api/health`): aktif prompt **v2** (katı REDDETME, yumuşak v3 KISMİ-CEVAP DEĞİL), `validate_entailment=False`. **(A) grounding lever'ı BAYAT çıktı:** honesty betiği (`scripts/m17_honesty_onveri.py`, 5 unanswerable ×3) canlıda **D4=15/15**; gs-034 ×3 artık `coverage=1.0` (M-17 mührdeki 0.75 DEĞİL), TEYİT-1b 0 near-miss. gs-034 artık *bağlanmamış trailer* bırakmıyor — **groundlu negatif cevap** veriyor ("Türkiye'de yürürlükte karbon vergisi yok → bütçedeki payı yok/sıfır", geçerli citation'larla). "12/15 tavanı" bayat veriymiş → prompt'a **dokunulmadı** (ön-veri-kontrolü bir k=3 karneyi olmayan soruna harcamaktan döndürdü). **(B) entailment-ON DEĞER probu** (`scripts/gs034_entailment_probe.py` commit `3234f2d`; config DEĞİŞMEDEN "açık olsaydı ne derdi"yi simüle eder, LOKAL judge → belge buluta gitmez): 9 citation'lı satırın hepsi `supported=1 hypothetical_as_fact=0` → **0 WOULD-FAIL**. coverage=1.0 sözcük-örtüşmesi HİÇBİR cited-fabrication maskelemiyordu. entailment ON honesty kazandırmaz, yalnız +1 LLM/soru latency → **rafa**. **DÜRÜST SINIR:** prob judge'ı agent'la AYNI model (qwen3.5:35b, veri egemenliği için lokal) → aynı-model kontrolü, bağımsız güçlü judge'tan DAHA DÜŞÜK bir bar; ama prod zaten lokal judge şart koşar ve D4=15/15 + drafts elle-incelemede groundlu → *entailment'i açMAma* kararı için yeterli. **Verdict: sistem zaten (ölçülü) dürüst; #8 aksiyon yok ile kapandı.** İki ucuz prob iki fenced karneyi de gereksiz kıldı. |
| 2026-07-16 | 1.4 | **M-10/0 — API H200'E TAŞINDI ve konteynerde KOŞUYOR.** Rehberler: `M10_H200_Kurulum.md` (uygulama) + `M10_Deploy_Kurulum.md` (kararlar/ölçümler). **(a) Deployment kararı REVİZE:** v0.3'teki "RHEL bare-metal, Docker'sız" ilkesi **API için** kalktı — `ragintel-api` Docker imajı (torch/docling HARİÇ; ölçüldü: API import zinciri docling'e dokunmuyor, `--no-deps` şart). DB/Redis bare-metal, Langfuse Podman: **değişmedi**. **(b) Doğrudan Ollama:** `localhost:11434` (host-network) — Open WebUI devre dışı; hem ağ atlamasını hem M-9.1'deki tool-call JSON arızasının şüpheli katmanını kaldırır. **(c) Tokenizer imaja gömüldü:** soğuk yükleme ~12 sn → **1.4 sn**, runtime'da internet şartı YOK. **(d) Bootstrap config kararı (M-4/3) GENİŞLETİLDİ:** os.environ zincire eklendi ama `.env`'in ARKASINA (`init > .env > os.environ > default`) — M-4'ün amacı (bayat host env `.env`'i ezemez) korunuyor; imajda `.env` yoktur ve olmamalı, o yüzden compose'un geçirdiği değerler okunabilmeliydi. **(e) Ölçüm düzeltmeleri (7d):** `listen_addresses` gerçekte `*`; `pg_hba` için istemci IP'si NAT nedeniyle gateway görünüyor (`inet_client_addr()`). **Bekleyen:** stabilizasyon testi (kayıtlı istek ×20 → suçlu WebUI muydu?) ve sonrasında temiz k=1 karne. |

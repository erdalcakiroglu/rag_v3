# FAZ 4 Tasarım — Tek Agentic Loop (LangGraph)

**Proje:** RAG v2 · **Versiyon:** 0.1 · **Tarih:** 2026-07-02
**Bağlam:** ADR-001 (tek loop), ADR-003 (LiteLLM), ADR-006 (permission-scoped cache), ADR-008 (MCP uyumlu tool'lar), ADR-010 (PostgresSaver)
**Amaç:** FAZ 4'te kodlanacak agentic loop'un state şeması, graf topolojisi, tool kontratları ve validation kontratını önceden netleştirmek. FAZ 1-3 bu kontratlara uygun üretim yapar.

---

## 1. Graf Topolojisi

```
START
  │
  ▼
[prepare]        permission context yükle, session memory getir, input guardrail sonucu al
  │
  ▼
[agent] ◄─────────────────┐         LLM karar noktası: tool çağır VEYA yanıt taslağı üret
  │                       │
  ├── tool_calls var ──► [tools] ──┘   ToolNode: paralel tool yürütme, sonuçlar state'e
  │
  └── taslak hazır
        │
        ▼
[validate]       grounding + citation kontrolü (deterministik node, LLM değil)
  │
  ├── PASS ────────────► [compose] ──► END     final response: answer+sources+confidence+followups
  │
  ├── FAIL + bütçe var ─► [agent]              validation feedback'i state'e eklenir, 1 retry
  │
  └── FAIL + bütçe yok ─► [fallback] ──► END   "güvenilir yanıt üretilemedi" + bulunan kaynaklar
```

Kurallar:

- `validate` ve `compose` deterministik node'lardır; ajan değildir, LLM çağrısı yapabilir ama karar akışı koddadır.
- Retry en fazla 1 kez: validation feedback ile ajana dönülür. İkinci fail → fallback. (Sonsuz düzeltme döngüsü yasak.)
- Her kenar geçişi Langfuse'a trace edilir (FAZ 2 altyapısı).

## 2. State Şeması

```python
# ragintel/agents/state.py
from typing import Annotated, TypedDict, Literal
from langgraph.graph.message import add_messages

class UserContext(TypedDict):
    user_id: str
    tenant_id: str
    roles: list[str]
    allowed_doc_scopes: list[str]     # retrieval filtresine girer — LLM ASLA üretmez

class RetrievedChunk(TypedDict):
    chunk_id: int
    text: str
    score: float
    source: dict          # {file_id, file_name, page, section, version}
    retrieval_method: Literal["vector", "hybrid", "lookup"]

class Citation(TypedDict):
    claim: str            # yanıttaki iddia (cümle/span)
    chunk_id: int
    quote: str            # chunk içindeki destekleyen ifade

class ValidationResult(TypedDict):
    passed: bool
    coverage: float           # citation coverage 0..1
    issues: list[str]         # ["claim_without_citation:...", "citation_not_in_context:..."]

class Budget(TypedDict):
    iteration: int            # ajan-tool tur sayacı
    max_iterations: int       # varsayılan 4
    tokens_used: int
    max_tokens: int           # varsayılan 16k (config-first, DB'den)
    deadline_ts: float        # wall-clock timeout (varsayılan 60 sn)

class AgentState(TypedDict):
    # girdi (immutable)
    query: str
    user_ctx: UserContext
    session_id: str
    # çalışma alanı
    messages: Annotated[list, add_messages]   # LLM konuşma geçmişi (tool çağrıları dahil)
    retrieved: list[RetrievedChunk]            # birikimli, dedup chunk_id ile — TAM havuz (teşhis)
    context: ContextBuildResult | None         # context builder çıktısı; validate/compose GİRDİSİ
    # çıktı adayları
    draft_answer: str | None
    citations: list[Citation]
    validation: ValidationResult | None
    # kontrol
    budget: Budget
    retry_count: int
```

Notlar:

- `user_ctx` yalnızca `prepare` node'unda yazılır; sonrasında salt-okunur. Tool'lara runtime enjekte eder, LLM parametresi DEĞİLDİR (yetki atlatma önlenir).
- `retrieved` birikimlidir: retry turunda önceki chunk'lar kaybolmaz. Bütçeyle context'ten elenen chunk'lar `retrieved`'de kalır (yalnızca teşhis/span) ama validation evrenine GİRMEZ (bkz. §4).
- `context`, ajan taslak üretmeden hemen önce context builder (İP-3.4) tarafından `retrieved`'den üretilir; validate ve compose'un yetkili girdisidir.
- Checkpointing: `PostgresSaver`, `thread_id = session_id`. Redis'te yalnızca session memory özeti tutulur.

## 3. Tool Kontratları (MCP uyumlu)

Tüm tool'lar: JSON-schema girdili, tek sorumluluklu, `user_ctx` runtime-enjekte. FAZ 3 bu kontratlara göre implemente eder; FAZ 4 yalnızca bağlar.

### 3.1 search_hybrid (birincil retrieval)
```
girdi : query: str, top_k: int = 10,
        filters: {file_type?, language?, date_from?, date_to?, section?}
işlem : pgvector cosine + tsvector BM25 → RRF fusion → allowed_doc_scopes filtresi (RLS)
çıktı : RetrievedChunk[]
```

### 3.2 search_vector
```
girdi : query: str, top_k: int = 10, filters (aynı)
işlem : yalnızca dense arama (hybrid'in fallback'i / A-B karşılaştırma)
çıktı : RetrievedChunk[]
```

### 3.3 lookup_document (bağlam genişletme)
```
girdi : chunk_id: int, window: int = 2        # komşu chunk sayısı
        VEYA file_id: int, page: int
işlem : chunk'ın çevresini/ilgili sayfayı getirir (multi-hop "devamını oku")
çıktı : RetrievedChunk[]
```

### 3.4 rerank
```
girdi : query: str, chunk_ids: int[]          # state.retrieved'den
işlem : bge-reranker-v2-m3 skorlama, yeniden sıralama
çıktı : {chunk_id, rerank_score}[] (sıralı)
```

### 3.5 memory_search (session memory)
```
girdi : query: str | None                     # None → son N mesaj özeti
işlem : Redis session memory (tenant+user scoped anahtar — ADR-006)
çıktı : {role, summary, ts}[]
```

**Tool DEĞİL, middleware:** audit log (her tool çağrısı otomatik kaydedilir), token sayımı, per-tool timeout (varsayılan 10 sn), hata sarma (tool exception → LLM'e "tool_error" mesajı, crash değil).

## 4. Validation Node Kontratı

Girdi: `draft_answer`, `citations`, **context builder'ın sakladığı chunk'lar** (`context`'ten türetilir). Çıktı: `ValidationResult`.

**Citation evreni (netleştirildi):** Denetlenen chunk kümesi, context builder'ın PROMPT'A KOYDUĞU (sakladığı) chunk'lardır — yani LLM'in gerçekten gördüğü bağlam. Bütçeyle elenen chunk'lar bu evrende YOKTUR. Gerekçe: citation, modelin kendisine sunulan kanıta işaret etmesidir; hiç görmediği bir chunk'a citation mantıksal olarak imkânsızdır ve geçerli sayılırsa şans eseri metni örtüşen bir uydurmayı meşrulaştırır. `retrieved` tam havuzu yalnızca teşhis amaçlı span'de kalır.

Kontroller (v1 — deterministik, hızlı):

1. **Citation bütünlüğü:** her `citation.chunk_id` gerçekten context evreninde (saklanan chunk'lar) mi? Değilse → FAIL (`citation_not_in_context`). Bütçeyle elenen chunk'a citation da bu koda düşer.
2. **Quote doğrulama:** `citation.quote`, ilgili chunk metninde (normalize edilmiş) geçiyor mu? → FAIL (`fabricated_quote`).
3. **Coverage:** `coverage = geçerli citation'a bağlanan BENZERSİZ cümle / toplam cümle` ≥ eşik (config, varsayılan 0.7). Yalnızca geçerli (evrende + quote doğrulanmış) citation'lar paya girer; tek cümleye verilen çoklu citation o cümleyi bir kez sayar (şişme yok). Altındaysa → FAIL (`low_coverage`).
4. **Boş yanıt / kaçamak kontrolü:** retrieved doluyken "bilgi yok" yanıtı → issue olarak işaretle (ajan feedback'ine gider).

v2 (FAZ 8): NLI tabanlı entailment / LLM-as-judge — yalnızca v1'in kaçırdığı örnekler birikirse (bkz. 7c ajan planı).

FAIL feedback formatı (ajana dönen mesaj):
```
VALIDATION_FAILED:
- claim_without_citation: "<cümle>"
- fabricated_quote: chunk 4211
Talimat: Yalnızca sağlanan bağlamdaki bilgiyle yanıtla; desteklenmeyen iddiaları çıkar veya ek arama yap.
```

## 5. Final Response Kontratı

```json
{
  "answer": "markdown yanıt, inline [1][2] işaretli",
  "sources": [
    {"n": 1, "file_name": "...", "page": 12, "section": "...", "chunk_id": 4211, "quote": "..."}
  ],
  "confidence": "high | medium | low",
  "followups": ["...", "..."],
  "meta": {"iterations": 2, "tokens": 8400, "latency_ms": 9200, "model": "qwen3-...", "trace_id": "..."}
}
```

`confidence` türetme (deterministik): coverage ≥0.9 ve rerank top skoru yüksek → high; validation retry yaşandıysa → en fazla medium; fallback → low.

## 6. Bütçe ve Limitler (config-first, DB'den)

| Limit | Varsayılan | Aşılınca |
|---|---|---|
| max_iterations (ajan-tool turu) | 4 | Eldeki bağlamla yanıt zorla → validate |
| max_tokens (toplam) | 16k | Aynı |
| Wall-clock timeout | 60 sn | Fallback |
| Per-tool timeout | 10 sn | tool_error mesajı, ajan devam eder |
| Validation retry | 1 | Fallback |
| top_k üst sınırı | 20 | Tool içinde kırpılır |

## 7. Modül Yerleşimi (öneri)

```
ragintel/
├── agents/
│   ├── state.py            # Bölüm 2
│   ├── graph.py            # Bölüm 1 — StateGraph kurulumu, PostgresSaver
│   ├── prompts.py          # sistem prompt'ları (DB'den, config-first)
│   └── nodes/
│       ├── prepare.py
│       ├── agent.py        # LLM karar noktası (LiteLLM üzerinden)
│       ├── validate.py     # Bölüm 4
│       ├── compose.py      # Bölüm 5
│       └── fallback.py
├── tools/                  # Bölüm 3 — her biri MCP uyumlu tanım + yerel impl
│   ├── base.py             # user_ctx enjeksiyonu, timeout, audit middleware
│   ├── search_hybrid.py
│   ├── search_vector.py
│   ├── lookup_document.py
│   ├── rerank.py
│   └── memory_search.py
├── llm/
│   └── gateway.py          # LiteLLM sarmalayıcı, model router, token sayımı
├── guardrails/
│   ├── input_check.py      # FAZ 3-4 minimal set
│   └── grounding.py        # validate node'un kullandığı kontroller
└── observability/
    └── tracing.py          # Langfuse + OTel
```

## 8. Ajan Sistem Prompt'u — Taslak İlkeler

- Rol: kurumsal doküman asistanı; YALNIZCA tool'lardan gelen bağlamla yanıt verir.
- Önce `search_hybrid`; sonuç zayıfsa sorguyu yeniden yazarak 1 kez daha dene; bağlam eksikse `lookup_document` ile genişlet.
- 10'dan fazla chunk varsa `rerank` çağır.
- Her iddiaya citation; bağlamda olmayan bilgi için "dokümanlarda bulunamadı" de.
- Türkçe yanıtla (sorgu dili farklıysa sorgu dilinde).
- Bütçe farkındalığı: kalan iterasyon state'ten prompt'a enjekte edilir.

## 9. FAZ 1-3'e Geri Beslenen Gereksinimler

Bu tasarımın önceki fazlara dayattığı kontratlar — FAZ 1'e başlarken dikkate alın:

1. `core_chunks` şemasında `source` alanları eksiksiz olmalı: file_name, page_number, section_title, version (citation bunlarsız kurulamaz).
2. Chunk metni, quote doğrulaması için normalize edilmiş halde de erişilebilir olmalı (veya normalize fonksiyonu ortak kütüphanede).
3. RLS / `allowed_doc_scopes` filtresi FAZ 1 şemasında kolon olarak planlanmalı (ör. `core_files.doc_scope`).
4. `metrics_*` tablolarına agent trace özet metrikleri için yer ayrılmalı (FAZ 2 Langfuse ile birleşir).

## 10. Açık Tasarım Soruları

1. Session memory özeti: her N mesajda LLM özeti mi, son-K mesaj penceresi mi? (öneri: MVP'de son-K)
2. Query rewriting agent prompt'unda mı, ayrı hafif LLM çağrısı mı? (öneri: MVP'de prompt içinde)
3. Streaming yanıt MVP'de var mı? (compose sonrası SSE kolay; validate akışı streaming'i geciktirir — öneri: MVP'de yok)

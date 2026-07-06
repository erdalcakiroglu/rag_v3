# FAZ 4 — Uçtan Uca Canlı Smoke Raporu

**Tarih:** 2026-07-03 · **Durum:** 3 senaryo koştu, akış uçtan uca çalışıyor.
**Zincir:** FAZ 1 korpusu (DB) → `search_hybrid` → `ContextBuilder` → agent (LiteLLM/Ollama) → `validate` → `compose`/`fallback`.

## Ortam
- **Korpus:** 42 dosya, 1341 chunk, 1341 vector (`core_vectors`). doc_scope: `default`(36), `smoke_trace`(4), `smoke_trace_2`(2). Smoke `default` scope'ta koştu.
- **Ollama:** `finagoseek.finagotech.com.tr:11434` erişilebilir — embed `bge-m3`, agent `llama3.2:3b`. (`banasor` — LiteLLMSettings kod default'u — ERİŞİLEMEZ; canlı çalıştırmada `RAGINTEL_LLM_API_BASE=finagoseek` gerekli.)
- **Agent modeli:** `llama3.2:3b` (dev CPU küçük model). Config: `max_iterations=3`, `timeout_sec=240`.
- **Langfuse:** KAPALI (host/anahtar yok) → UI'da ağaç görüntülenemez; span ağacı OTel `InMemorySpanExporter` ile kanıtlandı (aşağıda).

## Senaryo çıktıları

| # | Soru | Sonuç | conf | validation | iters | tokens | trace |
|---|---|---|---|---|---|---|---|
| a | Türkiye karbon azaltım hedefi / Yeşil Mutabakat | fallback | low | FAIL (coverage 0.0) | 3 | 3908 | tam ağaç |
| b | İstanbul'da en iyi pizzacı (korpus dışı) | fallback | low | FAIL (coverage 0.0) | 3 | 3785 | tam ağaç |
| c | Karbon vergisi ↔ yenilenebilir teşvik (TEI kapalı) | fallback | low | FAIL (coverage 0.0) | 3 | 2035 | tam ağaç + rerank_fallback |

- **(a) Cevaplanabilir:** Model `search_hybrid`'i çağırdı, bağlam geldi (`retrieval.search_hybrid` + `retrieval.build_context` span'leri), ama geçerli citation üretemedi → coverage 0 → retry → forced-submit (iters=3) → **dürüst fallback**. Crash yok.
- **(b) Korpus dışı:** Uydurma yüksek-güven yanıt YOK → dürüst fallback (confidence=low, sources boş). Doğru davranış.
- **(c) TEI kapalı:** Akış sorunsuz tamamlandı. Deterministik doğrulama: TEI `127.0.0.1:59999`'a sabitlendi → `rerank_fallback` (WinError 10061) log'landı → **6 sonuç passthrough ile döndü, eleman kaybı yok**.

## Span ağacı (Langfuse'a export-hazır)
`agent.run` kökü altında tam nesting doğrulandı:
```
agent.run
├─ agent.prepare
├─ agent.step        (search_hybrid çağrısı)
│  └─ retrieval.search_hybrid
├─ agent.tools
├─ retrieval.build_context
├─ agent.step        (submit_answer)
├─ agent.validate
└─ agent.compose | agent.fallback
```
`RAGINTEL_LANGFUSE_HOST`/anahtarları set edilince aynı ağaç Langfuse UI'da görünür (OTLP exporter zaten kurulu). Her span'de öznitelikler: iteration, decision, forced_final, tokens_used, coverage, passed, rerank_fallback.

## Küçük-model davranış gözlemleri (H200 notu — kod sorunu DEĞİL)
- `llama3.2:3b` tool-calling YAPIYOR (search_hybrid çağrıldı) ama **grounding'i geçen citation (claim+chunk_id+quote, quote bağlamda birebir) üretemiyor** → 3 senaryonun 3'ünde de coverage 0.0 → fallback.
- **Bütçe mekanizması doğal olarak test edildi:** `max_iterations=3` aşılınca **forced-submit_answer** yolu devreye girdi (span `forced_final=true`); retry=1 yaşandı; sonsuz döngü yok.
- **Token sayımı doğrulandı:** `tokens_used` 2035–3908 (sıfır değil) → Ollama `prompt_eval_count`/`eval_count` passthrough ÇALIŞIYOR, tiktoken kullanılmadı.
- **Beklenti:** H200 + `qwen3.5:35b` (config: `RAGINTEL_AGENT_MODEL`) ile citation kalitesi yükselince (a) PASS + citation'lı yanıt beklenir. Kod değişmez.

## Smoke'un yakaladığı GERÇEK bug (düzeltildi)
İlk canlı koşuda `validate` **crash** etti: `llama3.2:3b`, `submit_answer.citations`'ı bozuk (str / eksik-alan) üretince `int(citation["chunk_id"])` → `TypeError`. Herhangi bir kusurlu model üretimde bunu tetikler.
**Düzeltme (defense-in-depth):**
- `agents/nodes/agent.py::_normalize_citations` — LLM sınırında bozuk citation'ları eler, `chunk_id`'yi int'e çevirir.
- `guardrails/grounding.py` — dict-olmayan/eksik citation'ı `malformed_citation` olarak işaretler, crash etmez.
- Unit test: `test_validate_grounding_tolerates_malformed_citations`.

## Bu dilimde eklenen kod
- OTel span'leri: `prepare`/`agent.step`/`tools`/`validate`/`compose`/`fallback` + `graph.run_agent` kök span helper'ı.
- `tests/test_faz4_live_smoke.py` (@db @slow) — 3 senaryo + span ağacı assert + rerank fallback.
- Citation robustluk düzeltmesi (yukarıda).

## Test durumu
- Tam varsayılan suite: **191 passed, 8 deselected (slow)**, regresyon yok.
- Canlı smoke (3 senaryo): 3/3 passed (toplam ~8.5 dk; model latency).
- DB temizliği: çalıştırmalar sonrası ragintel'de **0 stray checkpoint tablosu** (PostgresSaver testi izole `faz4_ckpt_test` şemasında; manuel-DDL disiplini korundu).

## Açık öneriler
1. `LiteLLMSettings.api_base` kod default'u `banasor` erişilemez — `.env`'e `RAGINTEL_LLM_API_BASE=http://finagoseek...:11434` eklenmeli (veya default güncellenmeli).
2. Langfuse'u aktifleştir (`RAGINTEL_LANGFUSE_*`) → canlı trace ağacını UI'da görmek için.
3. H200 + büyük model gelince (a) senaryosunu PASS beklentisiyle yeniden koş.

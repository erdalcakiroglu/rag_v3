# v0 Resmi Retrieval Baseline — MÜHÜRLÜ

**Tarih:** 2026-07-08 · **Golden set:** `v0` (DB, `created_by=erdal`, 36 kayıt) · **Judge'sız, deterministik.**
**CLI:** `python -m ragintel.eval retrieval --golden v0 --variant vector|hybrid [--json]`

Bu rapor, İP-2.1b onay tablosunun (rev.2) tüm 36 kaydı **kabul** edilip `v0.jsonl` olarak
DB'ye yüklenmesinin (`--version v0`) ardından koşulan **resmi** baseline'dır. İP-2.4 draft
baseline'ının ([IP24_Retrieval_Baseline_Raporu.md](IP24_Retrieval_Baseline_Raporu.md))
yerini alır (aşağıda §4 fark açıklaması).

---

## 0. Mühür durumu

- **Golden set v0 DB'de:** `eval_golden_sets` → `v0`, 36 kayıt, `created_by=erdal`. Kaynak:
  `eval/golden/v0.jsonl` (draft ile birebir aynı; onaylı tek fark `created_by`).
  Yükleme: **36 inserted, 0 skipped**.
- **Evidence doğrulaması:** loader tüm `gold_evidence` quote'larını korpus chunk'larında
  aradı → **43/43 = %100** (yazımdan önce; eşleşmeseydi yazılmazdı).
- **Kota:** single_fact=10, citation_sensitive=4, synthesis=7, multi_hop=5, table_based=5,
  unanswerable=5. (`answerable=false` 5 unanswerable retrieval metriklerinden **hariç** →
  31 answerable kayıt ölçülür.)

## 1. Mühürlü retrieval config (İP-3.6)

Bu baseline, İP-3.6 tuning'i ile app_config'e yazılmış (`updated_by=codex`) config altında
koşuldu — bkz. [IP36_Tuning_Raporu.md](IP36_Tuning_Raporu.md) §6:

| Parametre | Değer | Kaynak |
|---|---|---|
| `retrieval.hybrid_fusion` | **weighted** | db |
| `retrieval.hybrid_dense_weight` | **0.8** | db |
| `retrieval.hybrid_sparse_weight` | **0.2** | db |
| `retrieval.hybrid_sparse_variant` | simple | kod varsayılanı |
| `retrieval.vector_ef_search` | 80 | kod varsayılanı |
| `retrieval.rerank_backend` | passthrough | kod varsayılanı |

Embedding: `bge-m3` (remote Ollama, finagoseek). doc_scope=`default`. top_k=20 (k=5/10/20).

## 2. Quote→chunk eşleme (ground truth)

Her `gold_evidence.quote` → `normalize_for_quote` → `chunk_text_norm` içinde aranır
(loader ile birebir mantık). **43/43 = %100.0** — eşlenemeyen yok (hedef ≥%95).

## 3. Resmi Baseline — vector & hybrid (k=5/10/20)

31 answerable kayıt · top_k=20 · doc_scope=default · embedding=bge-m3.

> **Not:** Mühürlü config (`weighted 0.8/0.2`) altında **hybrid = vector birebir**. Bu
> korpusta sparse/BM25 bileşeni recall'a katkı vermiyor ve düşük ağırlıkta (0.2) skor-tabanlı
> weighted füzyon dense sıralamayı koruyor → iki variant aynı çıktı. (İP-3.6 bulgusu.)

### vector (dense pgvector cosine)
| kategori | n | recall@5 | recall@10 | recall@20 | MRR | nDCG@5 | nDCG@10 | nDCG@20 |
|---|---|---|---|---|---|---|---|---|
| **GENEL** | 31 | 0.597 | 0.677 | 0.758 | 0.549 | 0.527 | 0.553 | 0.577 |
| single_fact | 10 | 0.900 | 0.900 | 0.900 | 0.725 | 0.769 | 0.769 | 0.769 |
| citation_sensitive | 4 | 0.750 | 0.750 | 0.750 | 0.750 | 0.750 | 0.750 | 0.750 |
| synthesis | 7 | 0.429 | 0.500 | 0.786 | 0.458 | 0.385 | 0.412 | 0.500 |
| table_based | 5 | 0.400 | 0.800 | 0.800 | 0.347 | 0.326 | 0.450 | 0.450 |
| **multi_hop** | 5 | **0.300** | 0.300 | 0.400 | 0.367 | 0.261 | 0.261 | 0.290 |

### hybrid (weighted 0.8/0.2 — dense + tsvector)
| kategori | n | recall@5 | recall@10 | recall@20 | MRR | nDCG@5 | nDCG@10 | nDCG@20 |
|---|---|---|---|---|---|---|---|---|
| **GENEL** | 31 | 0.597 | 0.677 | 0.758 | 0.549 | 0.527 | 0.553 | 0.577 |
| single_fact | 10 | 0.900 | 0.900 | 0.900 | 0.725 | 0.769 | 0.769 | 0.769 |
| citation_sensitive | 4 | 0.750 | 0.750 | 0.750 | 0.750 | 0.750 | 0.750 | 0.750 |
| synthesis | 7 | 0.429 | 0.500 | 0.786 | 0.458 | 0.385 | 0.412 | 0.500 |
| table_based | 5 | 0.400 | 0.800 | 0.800 | 0.347 | 0.326 | 0.450 | 0.450 |
| **multi_hop** | 5 | **0.300** | 0.300 | 0.400 | 0.367 | 0.261 | 0.261 | 0.290 |

## 4. İP-2.4 draft baseline ile fark (açıklandı)

- **vector:** IP24 draft koşusuyla **birebir aynı** (füzyon config'inden bağımsız).
- **hybrid:** IP24'ten FARKLI çünkü IP24 hybrid'i **RRF, 1.0/1.0** ile koşulmuştu
  (MRR 0.528, nDCG@5/10/20 = 0.510/0.537/0.561). Bu baseline **weighted 0.8/0.2** (İP-3.6
  mühürlü config) ile koşuldu → MRR 0.549. Fark bir determinizm hatası **değil**; İP-3.6'nın
  IP24 draft'ından sonra uyguladığı **bilinçli config retune**'undandır. Güncel hybrid sayıları
  İP-3.6'nın "weighted 0.8/0.2" satırıyla birebir uyuşur.
- **IP24'ün hybrid tablosu tuning-öncesi (RRF) → geçersiz** olarak işaretlendi; resmi hybrid
  bu rapordur.

## 5. Determinizm / DB-roundtrip kanıtı

Aynı korpus ve config altında, **resmi** (`--golden v0`, DB) ve **draft** (`--from-file
v0.draft.jsonl`, dosya) koşuları karşılaştırıldı:

- **vector: BİREBİR AYNI** (aggregate + per-record).
- **hybrid: BİREBİR AYNI** (aggregate + per-record).

→ Loader'ın DB'ye yazıp geri okuması, dosyadan koşmayla aynı retrieval'ı üretir; v0 mührü sağlam.

## 6. Açık notlar (H200-sonrası yeniden koşu girdisi)

1. **multi_hop en zayıf (recall@5=0.30, @20=0.40)** — FAZ G (agentic multi-hop) tetik
   ölçümünün resmi baseline'ı. İki dosyanın kanıtı da top-5'e giremiyor.
2. **Korpus dağıtıcıları:** golden korpusta 2 GGB-envanter XLSX (karbon-vergisi dışı) mevcut
   olabilir (bkz. IP24 §4). Retrieval havuzunda düşük örtüşmeli distractor; mühür sayısı
   (39 vs 40) ve ayrı doc_scope kararı hâlâ açık.
3. **Yeniden koşu (İP-3.6 §7):** GPU TEI rerank + daha güçlü embedding sonrası multi_hop
   recall'ı yeniden ölç; asıl kaldıraçlar tuning-config değil mimari (rerank / FAZ G / embedding).

# İP-2.4 — Retrieval Benchmark Düzeneği + İlk Baseline

**Tarih:** 2026-07-05 · **Judge'sız, deterministik.** İP-3.6 retrieval tuning'in hakemi.
**CLI:** `python -m ragintel.eval retrieval --golden v0 --variant vector|hybrid [--top-k N] [--json]`

---

## 0. Ön koşul durumu (DİKKAT — mismatch)

Görev "v0 golden set DB'de, korpus 39 mühürlü" varsayıyordu; gerçek durum:

- **eval_golden_sets BOŞ** — v0 (ya da herhangi bir set) DB'ye **yüklenmemiş**. `v0.draft.jsonl` dosyada mevcut (36 kayıt, `created_by=sonnet-draft`, önceki dry-run %100). Onaylı yükleme yapılmamış.
- **Korpus 40 COMPLETED, 39 değil** — 2 XLSX var (`GGB-Server-Inventory.xlsx`, `01-GGB-MsSQL Migration-Docs.xlsx`, GGB envanteri; karbon-vergisi DIŞI). Önceki datetime-fix + reprocess mührü kaydırmış olabilir.

**Bu baseline draft dosyasından koşuldu** (`--from-file`), DB'ye YAZILMADAN. Resmi `--golden v0` yolu, onaylı v0 yüklendiğinde **aynı sayıları** verecek (determinizm kanıtlı). Sayıları mühürlemek için: (a) onaylı v0 DB'ye yüklenmeli, (b) XLSX'lerin golden korpusa dahil/hariç kararı verilmeli (§4).

> **ÇÖZÜLDÜ (2026-07-08):** Onaylı v0 (36 kayıt, `created_by=erdal`) DB'ye yüklendi ve resmi
> baseline mühürlendi → [v0_Resmi_Baseline_Raporu.md](v0_Resmi_Baseline_Raporu.md). Determinizm
> kanıtlandı (resmi `--golden v0` = draft `--from-file`, birebir). **vector** IP24 ile birebir;
> **hybrid** İP-3.6 config retune'u (weighted 0.8/0.2) nedeniyle değişti — bu rapordaki RRF hybrid
> tablosu **geçersiz** işaretlendi (§2). XLSX/mühür-sayısı kararı (§4) hâlâ açık.

---

## 1. Quote→chunk eşleme raporu (ground truth)

Her `gold_evidence.quote` → `normalize_for_quote` → `chunk_text_norm` içinde aranır (loader ile birebir mantık). Eşleşen chunk_id'ler kaydın "bulunması gereken küme"sidir.

- **Eşleme: 43/43 = %100.0** — eşlenemeyen yok. (Hedef ≥%95; v0 tam geçti → İP-2.1'e geri bildirim gerekmiyor.)
- `answerable=false` (5 unanswerable) retrieval metriklerinden hariç — gold chunk'ları yok.

## 2. İlk Baseline — vector vs hybrid (k=5/10/20)

31 answerable kayıt · top_k=20 · doc_scope=default · embedding=bge-m3 (Ollama).

### vector (dense pgvector cosine)
| kategori | n | recall@5 | recall@10 | recall@20 | MRR | nDCG@5 | nDCG@10 | nDCG@20 |
|---|---|---|---|---|---|---|---|---|
| **GENEL** | 31 | 0.597 | 0.677 | 0.758 | 0.549 | 0.527 | 0.553 | 0.577 |
| single_fact | 10 | 0.900 | 0.900 | 0.900 | 0.725 | 0.769 | 0.769 | 0.769 |
| citation_sensitive | 4 | 0.750 | 0.750 | 0.750 | 0.750 | 0.750 | 0.750 | 0.750 |
| synthesis | 7 | 0.429 | 0.500 | 0.786 | 0.458 | 0.385 | 0.412 | 0.500 |
| table_based | 5 | 0.400 | 0.800 | 0.800 | 0.347 | 0.326 | 0.450 | 0.450 |
| **multi_hop** | 5 | **0.300** | 0.300 | 0.400 | 0.367 | 0.261 | 0.261 | 0.290 |

### hybrid (dense + tsvector BM25, RRF) — ⚠ TUNING-ÖNCESI, GEÇERSIZ

> **GEÇERSIZ (2026-07-08):** Bu tablo hybrid'i **RRF, 1.0/1.0** ile koşmuştur. İP-3.6 tuning'i
> config'i **weighted 0.8/0.2**'ye aldı (app_config, `updated_by=codex`; bkz. IP36 §6). Resmi
> hybrid baseline artık [v0_Resmi_Baseline_Raporu.md](v0_Resmi_Baseline_Raporu.md)'dedir
> (MRR 0.549; weighted altında hybrid=vector). Aşağıdaki RRF sayıları yalnızca tarihsel kayıttır.
> (vector tablosu geçerliliğini korur — füzyondan bağımsız.)

| kategori | n | recall@5 | recall@10 | recall@20 | MRR | nDCG@5 | nDCG@10 | nDCG@20 |
|---|---|---|---|---|---|---|---|---|
| **GENEL** | 31 | 0.597 | 0.677 | 0.758 | 0.528 | 0.510 | 0.537 | 0.561 |
| single_fact | 10 | 0.900 | 0.900 | 0.900 | 0.658 | 0.719 | 0.719 | 0.719 |
| citation_sensitive | 4 | 0.750 | 0.750 | 0.750 | 0.750 | 0.750 | 0.750 | 0.750 |
| synthesis | 7 | 0.429 | 0.500 | 0.786 | 0.458 | 0.385 | 0.412 | 0.500 |
| table_based | 5 | 0.400 | 0.800 | 0.800 | 0.347 | 0.326 | 0.450 | 0.450 |
| **multi_hop** | 5 | **0.300** | 0.300 | 0.400 | 0.367 | 0.261 | 0.261 | 0.290 |

## 3. Bulgular (İP-3.6 başlangıç noktası)

- **Hybrid ≈ vector (recall birebir aynı), sıralama marjinal DAHA KÖTÜ** (GENEL MRR 0.528 vs 0.549; nDCG@5 0.510 vs 0.527). Bu korpusta sparse/BM25 bileşeni recall'a katkı vermiyor; RRF füzyonu dense sıralamayı hafif bozuyor. **Tuning adayı:** sparse_variant/ağırlıklar (`retrieval.hybrid_sparse_weight`), Türkçe tsvector konfigürasyonu, ya da rerank (İP-3.3) devreye alma.
- **multi_hop en zayıf (recall@5=0.30) — FAZ G tetik ölçümünün RESMİ baseline'ı.** İki dosyanın kanıtı da top-5'e giremiyor; küme-recall doğası gereği sert. FAZ G (agentic multi-hop) buradan ölçülecek iyileşmeyi hedefler.
- **single_fact güçlü (0.90)**, citation_sensitive iyi (0.75). synthesis/table_based derinlikle toparlıyor (recall@5→@20: 0.43→0.79, 0.40→0.80) — top-5 dar, k artınca kanıt geliyor → **rerank + top_k artışı** adayı.
- **MRR düşük (~0.55)** → doğru chunk çoğu zaman ilk sırada değil; rerank en somut kaldıraç.

## 4. Açık kararlar (mühürleme öncesi)

1. **Onaylı v0 DB'ye yükle** (`created_by=erdal`) → `--golden v0` resmi yol. Bu baseline draft'tan koşuldu; onaylı setle sayı değişmez (aynı sorular/kanıtlar).
2. **XLSX'ler golden korpusa dahil mi?** GGB envanter dosyaları karbon-vergisi dışı; retrieval havuzunda distractor olabilir (pratikte karbon sorularıyla örtüşme düşük). Mühür 39 mu 40 mı — bunları ayrı doc_scope'a almak ya da golden korpustan hariç tutmak netleşmeli.

## 5. Düzenek kabul kanıtları

- **Sentetik self-test:** gold chunk'lar retriever'a verildiğinde recall@k=MRR=nDCG=1.0 (`test_ip24_benchmark::test_self_test_recall_one_when_gold_returned`).
- **Determinizm:** aynı girdi → aynı sonuç (unit test) + **canlı 2. koşu GENEL satırı birebir aynı**.
- **answerable=false hariç**, **multi_hop recall gold-KÜME üzerinden**, **kategori kırılımı** — testlerle sabit.
- Sonuç `eval.retrieval_benchmark` OTel span'iyle Langfuse'a gider (multi_hop_recall@5 öznitelik dahil); Langfuse kapalıysa no-op.

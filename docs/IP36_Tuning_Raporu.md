# İP-3.6 — İlk Retrieval Tuning Raporu

**Tarih:** 2026-07-05 · **Düzenek:** İP-2.4 benchmark (judge'sız, deterministik).
**Korpus:** default+COMPLETED **38 dosya** / 1157 chunk (2 XLSX `envanter`e taşındı — mühür 38).
**Golden:** v0.draft.jsonl (31 answerable) · **quote→chunk eşleme %100 (43/43)**.

> **Config değişikliği ÖNERİDİR** — app_config'e yazım senin onayından sonra.

---

## 1. Karşılaştırma tablosu (parametre + sparse taraması)

top_k=20 · embedding=bge-m3. Tüm sayılar 31 answerable kayıt üzerinden.

| config | recall@5 | recall@10 | recall@20 | MRR | nDCG@10 | multi_hop r@5 | mh r@20 | mh MRR |
|---|---|---|---|---|---|---|---|---|
| **vector** | 0.597 | 0.677 | **0.758** | **0.549** | **0.553** | 0.300 | 0.400 | 0.367 |
| hybrid-simple [rrf60] | 0.597 | 0.677 | 0.758 | 0.528 | 0.537 | 0.300 | 0.400 | 0.367 |
| hybrid-unaccent | 0.597 | 0.677 | 0.758 | 0.528 | 0.537 | 0.300 | 0.400 | 0.367 |
| hybrid-trgm | 0.516 | 0.629 | 0.726 | 0.525 | 0.515 | **0.100** | 0.300 | 0.247 |
| hybrid rrf_k=20 | 0.597 | 0.677 | 0.758 | 0.528 | 0.537 | 0.300 | 0.400 | 0.367 |
| hybrid rrf_k=120 | 0.597 | 0.677 | 0.758 | 0.528 | 0.537 | 0.300 | 0.400 | 0.367 |
| weighted 0.8/0.2 | 0.597 | 0.677 | 0.758 | **0.549** | **0.553** | 0.300 | 0.400 | 0.367 |
| weighted 0.5/0.5 | 0.597 | 0.677 | 0.758 | **0.549** | **0.553** | 0.300 | 0.400 | 0.367 |
| hybrid ef=40 | 0.597 | 0.677 | 0.758 | 0.528 | 0.537 | 0.300 | 0.400 | 0.367 |
| hybrid ef=200 | 0.597 | 0.677 | 0.758 | 0.528 | 0.537 | 0.300 | 0.400 | 0.367 |

## 2. Bulgular

1. **Recall dense-doygun; sparse/füzyon/parametreler recall'ı OYNATMIYOR.** rrf_k {20,60,120}, weighted α/β, ef_search {40,100,200} → **hepsi aynı recall** (0.758@20). Doğru chunk'lar zaten dense top-20'de; sparse yeni chunk eklemiyor. ef=40 bile ef=200 ile aynı → küçük korpusta HNSW recall'ı doygun.
2. **Türkçe sparse variantlar bu korpusta işe yaramıyor:**
   - **unaccent = simple** (fark yok) — aksan eşleşmesi recall'a katkı vermedi (dense zaten yakalıyor).
   - **trgm DAHA KÖTÜ** (recall@20 0.758→0.726; multi_hop@5 0.30→0.10). word_similarity gürültülü eşleşme getiriyor, füzyonu bozuyor. **Etkinleştirilmemeli.**
3. **RRF füzyonu MRR'ı hafif bozuyor:** RRF hybrid MRR=0.528 vs **weighted/vector MRR=0.549**. RRF'in rank-tabanlı birleştirmesi dense sıralamayı geriye çekiyor; weighted (skor tabanlı) dense'i koruyor.
4. **multi_hop en zayıf ve inatçı (recall@5=0.30, @20=0.40)** — hiçbir tek-sorgu retrieval config'i kıpırdatmıyor. İki dosyanın kanıtı tek sorguyla top-20'ye birlikte giremiyor → **FAZ G (agentic multi-hop) doğru yol**, tek-sorgu tuning değil.
5. **Recall tavanı embedding-bound:** @20=0.758 tavanı; kalan %24 gold chunk hiç top-20'ye girmiyor → reranking bunları KURTARAMAZ (havuzda yoklar). Tavanı yükseltmek için daha güçlü embedding / query expansion / multi-hop akışı gerekir.

## 3. Rerank — ölçülemedi (ALTYAPI bloğu, kod değil)

- vector+rerank / hybrid+rerank **mekanizması kuruldu ve bağlandı** (`StoreRetriever` + TEI; fail-open fallback doğrulandı).
- **TEI (bge-reranker, CPU @192.168.36.15:8085) çağrı başına >30 sn** (20 metin; warm dahi ~13-25 sn, yük altında >30 sn). Her rerank çağrısı timeout'a düşüp passthrough'a fallback ediyor → gerçek rerank sırası alınamıyor. 31 (hatta 13) sorunun tam koşusu komut limitlerine sığmıyor.
- **Teorik olarak rerank tek anlamlı kaldıraç** (havuzdaki gold'u öne çekip MRR 0.55'i ve recall@5'i iyileştirebilir; recall@20'yi değiştirmez — aynı havuz). Ama **GPU/H200-destekli TEI gerekiyor.** H200 sonrası bu satır tekrar koşulmalı.

## 4. Kazanan config önerisi (ONAY BEKLER — app_config'e YAZILMADI)

Bu korpusta hiçbir parametre recall'ı iyileştirmiyor; en iyi MRR **vector ≈ weighted-fusion** (0.549), RRF marjinal daha kötü. Öneri:

| Parametre | Mevcut | Öneri | Gerekçe |
|---|---|---|---|
| `retrieval.hybrid_fusion` | rrf | **weighted** | RRF MRR'ı 0.528'e düşürüyor; weighted 0.549 (dense sıralamayı korur) |
| `retrieval.hybrid_dense_weight` / `sparse_weight` | 1.0 / 1.0 | **0.8 / 0.2** | recall aynı, MRR en iyi; sparse'a düşük ama sıfır-olmayan ağırlık |
| `retrieval.hybrid_sparse_variant` | simple | **simple** (değişme) | unaccent fark yok, trgm zararlı |
| `retrieval.vector_ef_search` | 80 | **değişme** (40–200 aynı) | küçük korpusta etkisiz; H200/büyük korpusta yeniden bak |
| `retrieval.rerank_backend` | passthrough | **passthrough** (şimdilik) | TEI GPU olana kadar; sonra `tei`+ölç |

**Net:** ölçülebilir tek kazanım weighted-fusion 0.8/0.2 ile **MRR +0.02** (marjinal). Asıl kaldıraçlar bu korpus için tüketilmiş; gerçek iyileşme **(a) GPU TEI ile rerank, (b) FAZ G agentic multi-hop, (c) daha güçlü embedding** — hepsi tuning-config değil, mimari.

## 5. Altyapı notu (onayına)

- Bu dilim için DB'ye **`unaccent` ve `pg_trgm` extension'ları kuruldu** (sparse variantları çalıştırmak zorunluydu). Standart contrib, zararsız/idempotent. İstersen migration dosyasına da eklerim.
- Kod: `repository._sparse_variant_sql` artık simple/unaccent/trgm'yi GERÇEKTEN uyguluyor (önceden üçü de simple'a map ediliyordu). trgm testi güncellendi. **222 passed.**

## 6. Uygulanan kararlar

1. **app_config yazıldı** (`updated_by=codex`): `retrieval.hybrid_fusion=weighted`, `hybrid_dense_weight=0.8`, `hybrid_sparse_weight=0.2`. Gerekçe (description'da): RRF'in MRR zararını (0.528→0.549) geri alma; korpus büyüyünce yeniden ölçüm notu. `show --source` → dört anahtar da `db`.
2. **`docs/FAZ3_Sema.sql`** oluşturuldu: `unaccent` + `pg_trgm` `CREATE EXTENSION IF NOT EXISTS` (superuser notuyla) — elle kurulumun kalıcı kaydı.
3. **trgm KAPALI kalır**: `sparse_variant=simple` (ölçümde zararlı; uzantı seçilebilir dursun diye kurulu).
4. **`rerank_backend=passthrough` kalır** (TEI GPU olana dek).

## 7. H200-sonrası yeniden koşu listesi (FAZ G kararının girdisi)

Bu korpusta tuning-config kaldıraçları tükendi; asıl iyileşme H200/GPU ile gelecek. Sıradaki resmi koşular:

1. **TEI-GPU rerank sweep** — vector+rerank ve hybrid+rerank, tam 31 soru, top-20 havuz → k'da ölç. MRR (~0.55) ve recall@5'teki iyileşmeyi ölç; anlamlıysa `rerank_backend=tei`.
2. **Qwen3-Embedding vs bge-m3** — recall tavanı (@20=0.758) embedding-bound; güçlü embedding tavanı yükseltir mi? İki embedding'le baseline'ı karşılaştır (aynı benchmark, aynı golden).
3. **multi_hop re-baseline → FAZ G kararı** — rerank + güçlü embedding sonrası multi_hop recall@5/@20'yi yeniden ölç. Tek-sorgu retrieval hâlâ zayıfsa (mevcut 0.30/0.40) FAZ G agentic multi-hop'un gerekliliği resmen doğrulanır.

Not: Bu üç koşu golden set **onaylı v0 DB'ye yüklendikten** sonra `--golden v0` ile resmileştirilmeli (mevcut baseline draft dosyasından; sayılar birebir aynı olmalı).

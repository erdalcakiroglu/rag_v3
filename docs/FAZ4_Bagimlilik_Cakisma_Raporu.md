# FAZ 4 ADIM 1 — Bağımlılık Çakışma Raporu

**Kapsam:** `langgraph` + `langgraph-checkpoint-postgres` + `litellm` eklenmesinin mevcut pinli matrise (numpy 1.26 / torch 2.3.1+cpu / psycopg3 / pydantic v2) etkisi.
**Tarih:** 2026-07-03 · **Durum:** ANALİZ — pyproject'e EKLENMEDİ, onay bekliyor.
**Yöntem:** `pip install --dry-run` ile mevcut (donmuş) ortama karşı resolver çözümü; hiçbir paket kurulmadı.

---

## 1. Sonuç (özet)

**Matris çakışması YOK.** İP-2 dersi olan numpy-2/torch kırılması **gerçekleşmiyor**: resolver numpy, pydantic, psycopg, httpx, transformers, tokenizers, opencv pinlerinin hiçbirine dokunmadı (hepsi "already satisfied"). langgraph/litellm ailesi numpy'a hiç bağımlı değil.

**TEK sorun:** `litellm`, transitif olarak **`tiktoken`** çekiyor — bu [pyproject.toml:42](../pyproject.toml#L42) "tiktoken YASAK" kuralıyla çakışıyor. Blocker değil ama **açık bir karar** gerektiriyor (bkz. §4).

**İkincil:** `openai` 2.15.0 → **2.44.0** yükseltilecek (litellm `openai>=2.20.0` istiyor). Çakışma yaratmıyor.

---

## 2. Çözünen sürümler ("would install")

| Paket | Sürüm | Rol |
|---|---|---|
| langgraph | 1.2.7 | graph runtime (doğrudan) |
| langgraph-checkpoint | 4.1.1 | checkpoint çekirdeği (transitif) |
| langgraph-checkpoint-postgres | 3.1.0 | PostgresSaver (doğrudan) |
| langgraph-prebuilt | 1.1.0 | ToolNode vb. (transitif) |
| langgraph-sdk | 0.4.2 | transitif |
| langchain-core | 1.4.8 | mesaj/tool tipleri (transitif) |
| langsmith | 0.9.7 | trace (transitif; devre dışı bırakılabilir) |
| litellm | 1.90.2 | LLM gateway (doğrudan) |
| openai | 2.44.0 | litellm bağımlılığı — **yükseltme** (2.15.0→) |
| **tiktoken** | **0.13.0** | **litellm bağımlılığı — YASAK ihlali (§4)** |
| tenacity, jsonpatch, jsonpointer, ormsgpack, uuid_utils, fastuuid, websockets, langchain-protocol, langgraph başka transitifler | çeşitli | çakışma yok |

---

## 3. Kritik matris kontrolü (hepsi GEÇTİ)

| Pin | Mevcut | Yeni paketlerin talebi | Sonuç |
|---|---|---|---|
| **numpy** | 1.26.4 | (hiçbiri numpy istemiyor) | ✅ dokunulmadı — torch 2.3.1+cpu güvende |
| **torch** | 2.3.1+cpu | — | ✅ etkilenmedi |
| **pydantic** | 2.12.5 | langgraph `>=2.7.4`, litellm `>=2.5` | ✅ dokunulmadı |
| **psycopg** | 3.3.2 | checkpoint-postgres `>=3.2.0` | ✅ psycopg3 hizalı |
| **psycopg-pool** | 3.3.0 | checkpoint-postgres `>=3.2.0` | ✅ |
| **httpx** | 0.28.1 | langgraph-sdk `>=0.25.2`, openai 2.44 | ✅ dokunulmadı |
| **tokenizers** | 0.22.1 | litellm `>=0.21,<1.0` | ✅ transformers 4.57.3 ile uyumlu |
| **transformers** | 4.57.3 | — | ✅ etkilenmedi |
| **opencv-python** | 4.10.0.84 | — | ✅ etkilenmedi |
| **python-dotenv** | 1.2.1 | litellm `>=1.0,<2.0` | ✅ |

> "torch'suz" notu: torch zaten kuruluydu (docling→torch 2.3.1+cpu). numpy<2 sabitinin gerçek nedeni budur; yeni paketler bu sabiti **bozmuyor**.

---

## 4. TEK açık sorun: `tiktoken` (litellm transitif bağımlılığı)

**Durum:** litellm 1.90.2, `tiktoken<1.0,>=0.8.0`'ı **zorunlu** bağımlılık olarak listeler; kurulacak (0.13.0).

**Yasağın gerekçesi (İP-5):** token **sayımı** BGE-M3 tokenizer'iyle yapılır — chunking/bütçe sayımı embedding modeliyle tutarlı olmalı, OpenAI BPE'siyle değil.

**Çakışma gerçek mi?** Kısmen. Yasağın *niyeti* (bizim token sayacımız BGE olmalı) ihlal edilmiyor: litellm tiktoken'ı yalnızca kendi iç OpenAI maliyet/token tahmininde kullanır; biz Ollama kullandığımız için bu yol pratikte tetiklenmez. Ama paket **kuruluyor** — yani "hiç tiktoken olmasın" lafzı ihlal ediliyor.

**Öneri:** Yasağı "tiktoken'ı **doğrudan token sayımı için import etme**" olarak netleştir; litellm'in transitif tiktoken'ına izin ver. İki koruma önlemi:
1. **Guard testi:** `ragintel/**` içinde `import tiktoken` kullanımını yasaklayan bir test (grep tabanlı), yasağın kod düzeyinde korunmasını sağlar.
2. **Bütçe sayacı:** AgentState `budget.tokens_used` sayımı litellm'in tiktoken tabanlı sayacına DEĞİL, mevcut BGE-M3 sayacına (veya Ollama yanıt meta'sına) bağlanır. litellm yalnızca çağrı taşıyıcısı olur.

**Alternatif (önerilmez):** litellm'i atıp doğrudan Ollama HTTP + kendi router'ımız. LiteLLM'in model-router/retry/format soyutlamasını kaybederiz; ADR-003 LiteLLM'i seçmişti.

> Not (offline): litellm'in token sayacı bilinmeyen model için cl100k_base BPE dosyasını indirmek isteyebilir. Bütçe sayımını BGE'ye bağlarsak bu ağ çağrısı hiç tetiklenmez — ek fayda.

---

## 5. Önerilen pin seti (onay sonrası pyproject'e)

FAZ 4 dilimi için ayrı bir yorum bloğu altında:

```toml
    # FAZ 4 (ADR-001/003/010): agentic loop. langgraph ailesi numpy'a bağımlı
    # DEĞİL — numpy 1.26/torch matrisi etkilenmez (bkz. FAZ4_Bagimlilik_Cakisma_Raporu).
    "langgraph==1.2.7",
    "langgraph-checkpoint-postgres==3.1.0",  # PostgresSaver (psycopg3)
    "litellm==1.90.2",                        # LLM gateway (ADR-003); tiktoken transitif — §4
    "openai==2.44.0",                         # litellm >=2.20.0 gereği (2.15.0'dan yükseltme)
```

Transitifler (langchain-core 1.4.8, langgraph-checkpoint 4.1.1, langgraph-sdk 0.4.2, tenacity, tiktoken 0.13.0 …) resolver ile gelir; proje stili doğrudan bağımlılıkları pinler. İstenirse `langchain-core==1.4.8` ve `langgraph-checkpoint==4.1.1` de tekrarlanabilirlik için açıkça pinlenebilir.

---

## 6. ADIM 2'ye taşınan notlar (bu raporun kapsamı dışında, ön uyarı)

- **checkpoint şeması:** `langgraph-checkpoint-postgres` kendi tablolarını (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) `.setup()` ile kurar; varsayılan olarak bağlantının `search_path`'ine yazar. `ragintel` şemasına yönlendirmemiz ve elle-uygulama disiplinine uymamız gerekecek → ADIM 2'de DDL dosyası çıkarılıp onaya sunulacak.
- **Bütçe/token sayımı:** §4 gereği BGE/Ollama-meta tabanlı; litellm'in sayacına bağlanmaz.

---

## 7. Onay sorusu

1. **tiktoken exception** (§4): yasağı "doğrudan import yasak" olarak netleştirip litellm transitif tiktoken'ına izin + guard testi — onaylıyor musun?
2. **Pin seti** (§5): önerilen 4 doğrudan pin uygun mu? (openai 2.44 yükseltmesi dahil)

Onaylarsan pyproject'e eklerim ve ADIM 2 (agents/graph.py) dilimine geçerim.

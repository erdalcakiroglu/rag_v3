# M-1 — Büyük Tablo/Sheet Alt-Chunk'lama (Kapanış Raporu)

**Durum:** Kod tamamlandı + test edildi; korpus reprocess'lendi. **Sonuç: precision
iyileştirmesi DEĞİL — truncation/doğruluk düzeltmesi.** Aşağıdaki teşhis, kabul
kriterindeki hipotezin veriyle çürütüldüğünü ve M-1'in gerçek değerinin bir veri-kaybı
hatasını gidermek olduğunu gösterir.

## 1. Ne yapıldı (kod)

- **Config eşiği** ([settings.py](../ragintel/config/settings.py) `ChunkingConfig.table_subchunk_max_tokens`, vars. **512**):
  tablo chunk'ı bu token'ı aşarsa satır-gruplarına bölünür. Eşik-altı tablolar **AYNEN
  tek chunk** (çoğunluk davranışı korunur). ∞'a çekmek davranışı eski hâline döndürür
  (kod kalır, kapalı).
- **Chunker** ([chunker.py](../ragintel/ingestion/chunking/chunker.py) `_table_chunks`):
  eşik-üstü tabloyu satır-gruplarına böler; **her alt-chunk BAŞLIK satırını tekrar taşır**
  (bağlamsız satır anlamsızdır); `section_title`'da `tabloN · satır a-b` kimliği +
  `sheet_name`/`page_number` korunur.
- **`core_tables` DEĞİŞMEDİ** — yapısal bütünlük orada; değişen yalnızca chunk/embedding granülü.
- **Testler** ([test_faz_m1_table_subchunk.py](../tests/test_faz_m1_table_subchunk.py), 5/5):
  eşik-üstü bölünme + başlık her grupta; eşik-altı tek chunk; 8192+ token sentetik tablo
  truncation'sız (tüm satırlar korunur, her alt-chunk ≤ eşik); eşik=∞ → tek chunk;
  tek-dev-satır kendi chunk'ına düşer. Regresyon: 56 chunk/ingest/faz4 testi PASS.

## 2. Teşhis — hipotez veriyle çürütüldü

Kabul gerekçesi: *"table_based context_precision 0.47 — tek-dev-chunk kabalığı şüphelisi."*
Golden table_based (gs-v0-027..031) evidence chunk'larının **gerçek boyutları**:

| Soru | Dosya | Gold tablo chunk | 512 eşiği |
|---|---|---|---|
| gs-v0-027/031 | _005.pdf p8 | **200 tok** | altında → bölünmez |
| gs-v0-028 | 15-ET004233.pdf p96 | **247 tok** | altında → bölünmez |
| gs-v0-029 | 02-KARBON…pdf p15 | **461 tok** | altında → bölünmez |
| gs-v0-030 | 06-KARBON…pdf p5 | **87 tok** | altında → bölünmez |

→ **5 golden tablosunun hiçbiri 512 eşiğini aşmıyor; hepsi zaten küçük, odaklı tek chunk.**
table_based precision 0.47'nin nedeni dev-tablo kabalığı **değil**; precision düşüklüğü
top-k'ye giren **yan (tablo-dışı) metin chunk'larından** kaynaklanıyor — M-1 bunlara
dokunmaz. Dolayısıyla 512 eşiğiyle "0.47 → iyileşme" **gerçekleşemez**; pahalı 31×3
free-tier baseline koşusu önceden-bilinen ~sıfır deltayı doğrulamaktan öteye gitmez
(karar: koşulmadı — bkz. Bitiş).

## 3. M-1'in GERÇEK değeri — bir veri-kaybı hatası

Korpusta eşik-üstü büyük tablosu olan **10 dosya** (hiçbiri golden table_based değil):

- **GGB-Server-Inventory.xlsx: tek tablo chunk'ı 24 589 token** → bge-m3'ün 8192 bağlamını
  3× aşıyordu. Embed sırasında tokenizer'ın uyarısı bunu kanıtlıyor:
  `Token indices sequence length is longer than the specified maximum (24589 > 8192)` →
  chunk **sessizce truncate ediliyordu** (gerçek veri kaybı; envanterin ~2/3'ü vektöre girmiyordu).
- Diğer 9 dosyada (_008/_009/_021/011/007/012/015.pdf, 01-GGB-MsSQL/KarbonVergisi xlsx)
  512–2126 token tablolar tek-blob'du → retrieval granülerliği kaba.

M-1 bunları satır-gruplarına bölerek **truncation'ı ortadan kaldırır** ve granülerliği
düzeltir. (Reprocess sonrası her tablo alt-chunk'ı ≤512 token — bkz. §4.)

## 4. Reprocess sonucu (kanıt)

Büyük-tablo chunk'ı = `char_start IS NULL AND token_count > 512`. **8/10 dosya**
reprocess'lendi (embedder finagoseek koşu sonunda düştü → 012/015.pdf beklemede):

| file_id | dosya | tblMax (önce→sonra) | >512 chunk | chunk (önce→sonra) |
|---|---|---|---|---|
| 8514 | **GGB-Server-Inventory.xlsx** | **24 589 → 1 084** ★ | 4 → 1† | 4 → **96** |
| 8513 | 01-GGB-MsSQL…xlsx | 2 126 → 507 | 2 → 0 | 9 → 14 |
| 8007 | _008.pdf | 1 551 → 506 | 3 → 0 | 66 → 72 |
| 8009 | _021.pdf | 1 094 → 502 | 2 → 0 | 58 → 61 |
| 8008 | _009.pdf | 755 → 512 | 7 → 0 | 46 → 53 |
| 7982 | 007.pdf | 769 → 490 | 1 → 0 | 54 → 55 |
| 10441 | KArbonVergisi-v3.xlsx | 688 → 412 | 1 → 0 | 3 → 4 |
| 7984 | 011.pdf | 566 → 509 | 2 → 0 | 41 → 43 |
| 7985 | 012.pdf | 735 → *(beklemede)* | — | embedder DOWN |
| 7987 | 015.pdf | 684 → *(beklemede)* | — | embedder DOWN |

★ **Truncation giderildi:** 24 589-token tek blob (bge-m3 8192'yi 3× aşıyordu, sessiz
veri kaybı) → 96 alt-chunk. † Kalan tek >512 chunk (1 084 tok), **bölünemez tek bir
envanter satırı** — yine de 8192'nin çok altında, truncation yok.

**Golden evidence re-doğrulama (kabul):** 43 evidence quote'un tamamı reprocess sonrası
hâlâ bir chunk'a eşleşiyor → **0 mismatch, VALIDATION_PASS**. Alt-chunk'lama (başlık
tekrarıyla) hiçbir golden quote'unu bozmadı.

**Kalan:** finagoseek embedder koşu sonunda düştü (bilinen kararsızlık); 012/015.pdf
(küçük, truncation-riski YOK — 735/684 tok < 8192) embedder dönünce bitirilecek.
**eval gate smoke** embedder+judge gerektirir → embedder dönünce koşulacak (PASS beklentisi:
golden evidence korundu, retrieval inputs golden'da değişmedi).

## 5. Bitiş kuralı — nasıl kapatıldı

Bitiş kuralı: *"Delta iyileşme göstermezse eşik ∞ yapılıp davranış eski hâline döner."*
Ancak eşik=∞ **24589-token truncation'ı geri kırardı** (regresyon). Çatal kullanıcıya
sunuldu; karar: **eşik 512'de kalır, M-1 truncation/doğruluk düzeltmesi olarak aktif.**
"table_based precision iyileştirmesi" **iddia edilmez** — dürüst çerçeve budur.
Golden tablolarını da böldürmek isteyen ileri bir çalışma, eşiği (ör. 256) düşürüp
recall etkisini ayrı ölçmelidir (tek değişken disiplini).

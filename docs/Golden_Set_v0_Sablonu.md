# Golden Set v0 — Soru Yazım Şablonu ve Kılavuzu

**Versiyon:** 1.0 · **Tarih:** 2026-07-04 · **Referans:** FAZ2_Is_Plani İP-2.1 + Ek-B
**Hedef:** 36 soru (v0 — elle yazım; H200 sonrası LLM-destekli üretimle 100+'a genişler)
**Dosya:** `eval/golden/v0.jsonl` — her satır bir JSON kaydı (loader İP-2.1a ile yüklenir)

---

## 1. Kota Tablosu (v0 = 36 soru)

| Kategori | Kod | Adet | Ne test eder |
|---|---|---|---|
| Tekil fakt | `single_fact` | 10 | Tek chunk'tan cevaplanabilir doğrudan soru |
| Sentez | `synthesis` | 7 | Aynı dokümanın birden çok chunk'ının birleşimi |
| Multi-hop | `multi_hop` | 5 | İki FARKLI dokümanın birleştirilmesi (FAZ G tetik ölçümü!) |
| Tablo-kaynaklı | `table_based` | 5 | Cevabı tabloda olan soru (Docling tablo zinciri) |
| Cevapsız | `unanswerable` | 5 | Korpusta OLMAYAN bilgi — dürüstlük ölçümü |
| Citation-hassas | `citation_sensitive` | 4 | Doğru sayfa/kaynak gösterimi kritik olan soru |

## 2. Kayıt Formatı (JSONL — satır başına bir kayıt)

```json
{"id": "gs-v0-001", "question": "...", "ideal_answer": "...", "category": "single_fact", "difficulty": 1, "gold_evidence": [{"file_name": "...", "page": 3, "quote": "..."}], "doc_scope": "default", "answerable": true, "created_by": "erdal", "notes": ""}
```

Alan kuralları:

- `id`: `gs-v0-NNN` sıralı; benzersiz.
- `difficulty`: 1 = doğrudan, 2 = çıkarım gerektirir, 3 = çok adımlı/inceltilmiş.
- `gold_evidence`: cevabı destekleyen kanıt(lar). `quote` **dokümandan birebir kopya** olmalı (aşağıda kritik kural). Multi-hop'ta ≥2 farklı file_name; synthesis'te aynı file_name'den ≥2 kanıt.
- `page`: PDF/DOCX sayfa no; XLSX için `"sheet": "SayfaAdı"` kullanın.
- `unanswerable` kayıtlarında: `answerable: false`, `gold_evidence: []`, `ideal_answer` = beklenen dürüst yanıt ("Bu bilgi dokümanlarda bulunmamaktadır." gibi).

## 3. ⚠️ Kritik Kural: Quote Doğrulaması

Loader her `quote`'u normalize edip korpustaki `chunk_text_norm` içinde ARAR — bulunamazsa kayıt REDDEDİLİR. Bu yüzden:

1. Quote'u dokümandan/DB'den **kopyala-yapıştır** yazın, hatırlayarak yazmayın.
2. Quote **tek bir chunk'ın içinde** kalmalı — çok uzun alıntı iki chunk'a yayılırsa eşleşmez. Güvenli uzunluk: 1-2 cümle (10-40 kelime).
3. En pratik yöntem — chunk'lara DB'den bakın:

```sql
-- Bir dosyanın chunk'larını gezin:
SELECT c.chunk_index, c.page_number, left(c.chunk_text, 200) AS onizleme
FROM ragintel.core_chunks c JOIN ragintel.core_files f USING (file_id)
WHERE f.file_name = '09-KarbonVergisiNedir.pdf' ORDER BY c.chunk_index;

-- Seçtiğiniz chunk'ın tam metni (quote'u BURADAN kopyalayın):
SELECT chunk_text FROM ragintel.core_chunks c JOIN ragintel.core_files f USING (file_id)
WHERE f.file_name = '09-KarbonVergisiNedir.pdf' AND c.chunk_index = 4;
```

Tablo-kaynaklı sorular için quote'u `core_tables.table_text`'ten (düzleştirilmiş tablo metni) alın — o metin tablonun chunk'ıdır.

## 4. Soru Yazım Kuralları (Ek-B'den)

- **Gerçek kullanıcı sorusu gibi:** Soru, dokümanı görmeyen birinin soracağı gibi olmalı. ❌ "Bu paragrafta ne anlatılıyor?" ✅ "Karbon vergisi hangi ürünleri kapsıyor?"
- **Leak yok:** Soru cevabı içermemeli. ❌ "Karbon vergisi ton başına 25 € olarak mı belirlendi?" ✅ "Karbon vergisinin ton başına tutarı nedir?"
- **Multi-hop gerçekten multi-hop olmalı:** Tek dokümandan cevaplanabiliyorsa kategori düşürün. İyi kalıp: "X dokümanındaki A kavramı ile Y dokümanındaki B düzenlemesi arasındaki ilişki nedir?"
- **Cevapsızlar elle ve gerçekçi:** Korpus konusuna YAKIN ama korpusta olmayan sorular (uzak konu değil — "İstanbul'da pizza" değil, "karbon vergisinin 2030 sonrası oran takvimi" gibi, eğer korpusta yoksa). Sistemin "bilmiyorum" diyebilmesini gerçekten zorlasın.
- Türkçe yazın; kurum/alan terminolojisini kullanın.

## 5. Doldurulmuş Örnekler (quote'ları KENDİ korpusunuzdan değiştirin!)

> Aşağıdaki quote'lar TEMSİLİDİR — loader'dan geçmeleri için gerçek chunk metninden kopyalanmalıdır.

```json
{"id": "gs-v0-001", "question": "Karbon vergisi nedir ve hangi ilkeye dayanır?", "ideal_answer": "Karbon vergisi, fosil yakıtların karbon içeriğine veya karbon emisyonlarına uygulanan bir vergidir; 'kirleten öder' ilkesine dayanır.", "category": "single_fact", "difficulty": 1, "gold_evidence": [{"file_name": "09-KarbonVergisiNedir.pdf", "page": 1, "quote": "<GERÇEK CHUNK METNİNDEN KOPYALA>"}], "doc_scope": "default", "answerable": true, "created_by": "erdal", "notes": ""}
{"id": "gs-v0-011", "question": "Sınırda karbon düzenlemesi (SKDM/CBAM) Türkiye'deki ihracatçıları hangi sektörlerde ve nasıl etkileyecek?", "ideal_answer": "<Aynı dokümanın birden çok bölümünden sentezlenen yanıt>", "category": "synthesis", "difficulty": 2, "gold_evidence": [{"file_name": "16-KARBON_VERGISI_EMISYON_TICARET_SISTEMI_VE_SINIRDA_.pdf", "page": 4, "quote": "<kanıt-1>"}, {"file_name": "16-KARBON_VERGISI_EMISYON_TICARET_SISTEMI_VE_SINIRDA_.pdf", "page": 9, "quote": "<kanıt-2>"}], "doc_scope": "default", "answerable": true, "created_by": "erdal", "notes": "aynı doküman, 2 bölüm"}
{"id": "gs-v0-018", "question": "Karbon vergisi ile emisyon ticaret sistemi arasındaki temel fark nedir ve Türkiye için hangisi öneriliyor?", "ideal_answer": "<İki farklı dokümandan birleştirilen yanıt>", "category": "multi_hop", "difficulty": 3, "gold_evidence": [{"file_name": "02-KARBON-VERGISI-1890343.pdf", "page": 2, "quote": "<kanıt-1: fark tanımı>"}, {"file_name": "14-TEZ-304230.pdf", "page": 45, "quote": "<kanıt-2: Türkiye önerisi>"}], "doc_scope": "default", "answerable": true, "created_by": "erdal", "notes": "FAZ G tetik ölçümü kategorisi"}
{"id": "gs-v0-023", "question": "Rapordaki tabloya göre hangi ülke ton başına en yüksek karbon vergisini uyguluyor?", "ideal_answer": "<Tablodan okunan değer ve ülke>", "category": "table_based", "difficulty": 2, "gold_evidence": [{"file_name": "011.pdf", "page": 7, "quote": "<core_tables.table_text içinden ilgili satır>"}], "doc_scope": "default", "answerable": true, "created_by": "erdal", "notes": "quote table_text'ten"}
{"id": "gs-v0-028", "question": "Türkiye'de karbon vergisinin 2030 sonrası yıllara göre oran artış takvimi nedir?", "ideal_answer": "Bu bilgi dokümanlarda bulunmamaktadır.", "category": "unanswerable", "difficulty": 2, "gold_evidence": [], "doc_scope": "default", "answerable": false, "created_by": "erdal", "notes": "konuya yakın ama korpusta yok — dürüstlük testi"}
{"id": "gs-v0-033", "question": "Greenpeace'in iklim kanunu talebi kaç maddeden oluşuyor ve bu talep hangi belgede yer alıyor?", "ideal_answer": "<Madde sayısı + doğru belge adı/sayfası>", "category": "citation_sensitive", "difficulty": 1, "gold_evidence": [{"file_name": "01--12-maddede-iklim-kanunu-istiyoruz-greenpeace.docx", "page": 1, "quote": "<gerçek metinden>"}], "doc_scope": "default", "answerable": true, "created_by": "erdal", "notes": "kaynağın kendisi cevabın parçası"}
```

## 6. Pratik İş Akışı (öneri: ~2-3 saat)

1. Rapordaki dosya listesini önünüze alın; her dosyaya 1-2 soru düşecek şekilde gezin (tek dosyaya yığılmayın — max 3).
2. Önce kolay kategoriler: single_fact (10) + citation_sensitive (4) → ısınma.
3. Sonra synthesis (7) ve table_based (5) — chunk önizleme SQL'iyle.
4. Multi-hop (5) için dosya ÇİFTLERİ düşünün: aynı kavramı farklı açıdan işleyen dokümanlar.
5. En son unanswerable (5) — korpusu artık iyi tanıyorsunuz, "yakın ama yok"u en iyi bu aşamada yazarsınız.
6. Yükleme + doğrulama: loader eşleşmeyen quote'ları raporlar; düzeltin, tekrar yükleyin.

```
python -m ragintel.eval load eval/golden/v0.jsonl --version v0   # (loader CLI adı repo'daki haliyle)
```

## 7. Kalite Kontrol Listesi (yüklemeden önce)

- [ ] 36 kayıt, kota tablosuna uygun (±1 tolerans)
- [ ] Tüm quote'lar kopyala-yapıştır (elle yazılmış quote yok)
- [ ] Multi-hop'ların hepsinde ≥2 FARKLI file_name
- [ ] Unanswerable'larda evidence boş + answerable=false
- [ ] Hiçbir soru cevabını içermiyor (leak taraması — kendiniz okuyun)
- [ ] id'ler benzersiz ve sıralı

"""İP-5 chunking çekirdeği (DB gerekmez; WordTokenCounter ile deterministik/hızlı).

Kapsanan kabul kriterleri: sınır durumlar, max aşılmaz, min-altı yalnız kısa
dosyada, overlap char-span kesişimi, determinizm, tablo bölünmez.
"""

from __future__ import annotations

import hashlib

from ragintel.ingestion.chunking import chunk_document, WordTokenCounter
from ragintel.ingestion.parsing.parsed_document import Page, ParsedDocument, Section, Table
from ragintel.text import normalize_for_quote

WC = WordTokenCounter()
CFG = dict(max_tokens=50, overlap_tokens=10, min_tokens=5)


def _long_section_doc():
    words = " ".join(f"w{i}" for i in range(200))
    return ParsedDocument(
        pages=[Page(1, ["Bölüm A", words]), Page(2, ["Bölüm B", " ".join(f"x{i}" for i in range(120))])],
        sections=[Section("Bölüm A", 1, 1), Section("Bölüm B", 1, 2)],
    )


def _sig(chunks):
    payload = "|".join(
        f"{c.chunk_index}:{c.chunk_text}:{c.char_start}:{c.char_end}:{c.token_count}:"
        f"{c.page_number}:{c.section_title}:{c.is_table}"
        for c in chunks
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# --- Sınır durumlar ----------------------------------------------------------
def test_single_line_file():
    doc = ParsedDocument(pages=[Page(1, ["Tek satır kısa metin."])])
    chunks = chunk_document(doc, counter=WC, strategy="section", **CFG)
    assert len(chunks) == 1
    # Dosyanın tamamı kısa -> tek chunk min altı olabilir (izinli).
    assert chunks[0].chunk_text == "Tek satır kısa metin."


def test_500_page_pdf():
    pages = [Page(i + 1, [f"Sayfa {i} içerik metni burada yer alıyor."]) for i in range(500)]
    doc = ParsedDocument(pages=pages)
    chunks = chunk_document(doc, counter=WC, strategy="section", **CFG)
    assert len(chunks) > 0
    assert all(c.token_count <= CFG["max_tokens"] for c in chunks)
    # Sayfa numaraları korunur.
    assert chunks[0].page_number == 1


def test_table_only_xlsx():
    doc = ParsedDocument(tables=[
        Table(0, [["a", "b"]], "hücre1 | hücre2", sheet_name="S1"),
        Table(1, [["c"]], "hücre3", sheet_name="S2"),
    ])
    chunks = chunk_document(doc, counter=WC, strategy="section", **CFG)
    assert len(chunks) == 2
    assert all(c.is_table for c in chunks)
    assert chunks[0].sheet_name == "S1" and chunks[0].char_start is None


def test_sectionless_plain_text_sliding():
    words = " ".join(f"t{i}" for i in range(130))
    doc = ParsedDocument(pages=[Page(1, [words])], sections=[])
    chunks = chunk_document(doc, counter=WC, strategy="section", **CFG)
    assert len(chunks) >= 3
    assert all(c.token_count <= CFG["max_tokens"] for c in chunks)
    assert all(c.section_title is None for c in chunks)


# --- Genel garantiler --------------------------------------------------------
def test_no_chunk_exceeds_max():
    chunks = chunk_document(_long_section_doc(), counter=WC, strategy="section", **CFG)
    assert chunks, "ön-koşul: chunk üretilmeli (boş listede all() vacuously geçer)"
    assert all(c.token_count <= CFG["max_tokens"] for c in chunks)


def test_below_min_only_when_whole_file_short():
    chunks = chunk_document(_long_section_doc(), counter=WC, strategy="section", **CFG)
    body = [c for c in chunks if not c.is_table]
    if len(body) > 1:
        assert all(c.token_count >= CFG["min_tokens"] for c in body)


def _basligi_ayri_dusen_doc(govde_kelime: int):
    """Tek sayfalık tebliğ biçimi: kurum başlığı KENDİ section'ıdır ve ardından
    gövde bloğu gelmez — bu yüzden tek başına, minik bir ilk chunk üretir.

    Canlı korpusun baskın biçimi (882/1117 dosya tek sayfa, ort 892 karakter).
    """
    return ParsedDocument(
        pages=[Page(1, ["BDDK"]),
               Page(1, ["Gövde", " ".join(f"w{i}" for i in range(govde_kelime))]),
               Page(1, ["Ek", " ".join(f"x{i}" for i in range(100))])],
        sections=[Section("BDDK", 1, 1), Section("Gövde", 1, 1), Section("Ek", 1, 1)],
    )


def test_leading_short_chunk_merges_forward():
    """REGRESYON: _min_merge yalnız GERİYE birleştiriyordu, bu yüzden bir
    dosyanın İLK chunk'ı min altındaysa birleşeceği yer olmadığından öyle
    kalıyordu. Ölçüm (2026-08-09, scripts/metin_korunumu_probe.py §5):
    min-altı 1060 chunk'ın 831'i (%78.4) chunk_index=0'daydı; 807 dosyanın
    (korpusun %72'si) TEK kusuru buydu."""
    chunks = chunk_document(_basligi_ayri_dusen_doc(40), counter=WC,
                            strategy="section", **CFG)
    body = [c for c in chunks if not c.is_table]
    assert len(body) > 1, "ön-koşul: birleşme sonrası hâlâ birden çok chunk"
    assert body[0].token_count >= CFG["min_tokens"]
    assert body[0].chunk_text.startswith("BDDK"), "başlık metni korunmalı, atılmamalı"
    assert "w0" in body[0].chunk_text, "başlık gövdeye katılmalı"
    assert all(c.token_count <= CFG["max_tokens"] for c in chunks)


def test_leading_merge_never_breaks_max_budget():
    """İleri birleşmenin BİLİNEN sınırı: sonraki chunk zaten tavandaysa
    (başlık + tam pencere > max_tokens) birleşme YAPILMAZ — max sert
    invaryanttır, min-altı ise yumuşak bulgudur. Bu yüzden düzeltme canlı
    korpusta min-altı chunk'ı olan 876 dosyanın 807'sini (%92.1) temizler,
    tamamını değil; kalanlar ilk penceresi dolu olan uzun belgelerdir."""
    chunks = chunk_document(_basligi_ayri_dusen_doc(200), counter=WC,
                            strategy="section", **CFG)
    assert all(c.token_count <= CFG["max_tokens"] for c in chunks)
    assert chunks[0].chunk_text == "BDDK", "birleşemedi -> olduğu gibi kalmalı"


def test_whole_file_shorter_than_min_stays_one_chunk():
    """İleri birleşme baştaki minik chunk'ı YUTAR, silmez: tümü min altındaki
    bir dosya tek chunk'a iner ve metnin tamamı içinde kalır."""
    doc = ParsedDocument(pages=[Page(1, ["Bir"]), Page(1, ["iki"]), Page(1, ["üç"])],
                         sections=[Section("Bir", 1, 1), Section("iki", 2, 1),
                                   Section("üç", 3, 1)])
    chunks = chunk_document(doc, counter=WC, strategy="section", **CFG)
    assert len(chunks) == 1
    for kelime in ("Bir", "iki", "üç"):
        assert kelime in chunks[0].chunk_text


def test_overlap_char_span_intersection():
    chunks = chunk_document(_long_section_doc(), counter=WC, strategy="section", **CFG)
    body = [c for c in chunks if not c.is_table and c.char_start is not None]
    pairs = [(a, b) for a, b in zip(body, body[1:]) if a.section_title == b.section_title]
    assert pairs, "en az bir ardışık aynı-section chunk çifti"
    for a, b in pairs:
        assert a.char_end > b.char_start   # span kesişimi (overlap)


def test_determinism_bit_identical():
    doc = _long_section_doc()
    s1 = _sig(chunk_document(doc, counter=WC, strategy="section", **CFG))
    s2 = _sig(chunk_document(doc, counter=WC, strategy="section", **CFG))
    assert s1 == s2


def test_chunk_text_norm_uses_shared_normalizer():
    doc = ParsedDocument(pages=[Page(1, ["  KARBON   Vergisi  "])])
    c = chunk_document(doc, counter=WC, strategy="section", **CFG)[0]
    assert c.chunk_text_norm == normalize_for_quote(c.chunk_text)


def test_table_chunk_not_split_even_if_long():
    long_table = " ".join(f"c{i}" for i in range(300))   # 300 token tek tablo
    doc = ParsedDocument(tables=[Table(0, [["x"]], long_table, page_no=1)])
    chunks = chunk_document(doc, counter=WC, strategy="section", **CFG)
    assert len(chunks) == 1                      # bölünmedi
    assert chunks[0].token_count == 300          # max aşsa bile tablo bütün

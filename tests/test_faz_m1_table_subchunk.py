"""M-1 — büyük tablo/sheet alt-chunk'lama sınır testleri.

Kapsam (kabul kriterleri):
- eşik-üstü tablo satır-gruplarına bölünür + BAŞLIK her grupta tekrar eder,
- eşik-altı tablo AYNEN tek chunk (mevcut davranış, section_title=None),
- 8192+ token sentetik tablo truncation'sız (tüm satırlar korunur, her alt-chunk ≤ eşik),
- eşik=∞ → davranış eski hâline döner (tek chunk).
"""

from __future__ import annotations

from ragintel.ingestion.chunking.chunker import chunk_document
from ragintel.ingestion.parsing.parsed_document import ParsedDocument, Table
from ragintel.ingestion.parsing.text_utils import flatten_table


class _WordCounter:
    """Deterministik token sayacı (boşlukla ayrılmış parça sayısı)."""

    def count(self, text: str) -> int:
        return len(text.split())


def _doc_with_table(rows: list[list], *, index: int = 0, sheet: str | None = None,
                    page: int | None = None) -> ParsedDocument:
    doc = ParsedDocument()
    doc.tables.append(Table(index=index, data=rows,
                            flattened_text=flatten_table(rows),
                            page_no=page, sheet_name=sheet))
    return doc


def _header_line(header: list) -> str:
    return flatten_table([header]).splitlines()[0]


# --- eşik-altı: tek chunk, eskisi gibi ---------------------------------------
def test_small_table_single_chunk():
    rows = [["Ülke", "Vergi"], ["Türkiye", "10"], ["AB", "20"]]
    doc = _doc_with_table(rows, sheet="S1")
    chunks = chunk_document(doc, counter=_WordCounter(),
                            table_subchunk_max_tokens=10_000)
    assert len(chunks) == 1
    c = chunks[0]
    assert c.is_table and c.sheet_name == "S1"
    assert c.section_title is None          # bölünmedi → kimlik etiketi yok
    assert c.chunk_text == flatten_table(rows)


# --- eşik-üstü: bölünür + başlık her grupta ----------------------------------
def test_large_table_splits_with_header_in_each_group():
    header = ["Ülke", "Yıl", "Değer"]
    body = [[f"Ü{i}", f"20{i:02d}", str(i * 7)] for i in range(1, 13)]
    rows = [header] + body
    doc = _doc_with_table(rows, index=3, page=8)
    # Her satır ~5 token; birkaç satır bir gruba sığacak şekilde küçük eşik.
    chunks = chunk_document(doc, counter=_WordCounter(),
                            table_subchunk_max_tokens=20)

    assert len(chunks) > 1                    # gerçekten bölündü
    hline = _header_line(header)
    seen_rows: list[str] = []
    for c in chunks:
        assert c.is_table and c.page_number == 8
        lines = c.chunk_text.splitlines()
        assert lines[0] == hline              # BAŞLIK her grupta tekrar
        assert c.section_title.startswith("tablo3 · satır ")  # kimlik + aralık
        seen_rows.extend(lines[1:])           # başlık dışındaki gövde satırları

    # Hiçbir satır ne kaybolur ne çift sayılır: gövde tam ve sırayla kapsanır.
    expected = [flatten_table([r]).splitlines()[0] for r in body]
    assert seen_rows == expected


# --- 8192+ token sentetik tablo: truncation YOK ------------------------------
def test_8192plus_token_table_no_truncation():
    header = ["K1", "K2", "K3", "K4"]
    # ~200 satır × ~9 token ≈ 1800+ "kelime"; eşik 512 → çok sayıda alt-chunk.
    body = [[f"hücre-{i}-a", f"hücre-{i}-b", f"hücre-{i}-c", f"hücre-{i}-d"]
            for i in range(200)]
    rows = [header] + body
    counter = _WordCounter()
    full_tokens = counter.count(flatten_table(rows))
    assert full_tokens > 512                  # tek-chunk olsaydı eşiği çok aşardı

    doc = _doc_with_table(rows)
    chunks = chunk_document(doc, counter=counter, table_subchunk_max_tokens=512)

    hline = _header_line(header)
    seen_rows: list[str] = []
    for c in chunks:
        assert c.token_count <= 512           # hiçbir alt-chunk eşiği aşmaz → embed truncation yok
        lines = c.chunk_text.splitlines()
        assert lines[0] == hline
        seen_rows.extend(lines[1:])

    # TÜM gövde satırları korunur (içerik kaybı yok).
    expected = [flatten_table([r]).splitlines()[0] for r in body]
    assert seen_rows == expected


# --- eşik=∞ → davranış eski hâline döner (tek chunk) -------------------------
def test_infinite_threshold_reverts_to_single_chunk():
    header = ["K1", "K2"]
    body = [[f"a{i}", f"b{i}"] for i in range(100)]
    rows = [header] + body
    doc = _doc_with_table(rows)
    chunks = chunk_document(doc, counter=_WordCounter(),
                            table_subchunk_max_tokens=10**9)
    assert len(chunks) == 1
    assert chunks[0].section_title is None
    assert chunks[0].chunk_text == flatten_table(rows)


# --- tek gövde satırı eşiği aşsa bile kendi chunk'ına düşer (bölünemez) -------
def test_single_oversized_row_becomes_own_chunk():
    header = ["K"]
    big = " ".join(f"w{i}" for i in range(50))     # tek hücre, ~50 token
    rows = [header, [big], ["kısa"]]
    doc = _doc_with_table(rows)
    chunks = chunk_document(doc, counter=_WordCounter(),
                            table_subchunk_max_tokens=20)
    # Büyük satır tek başına bir grup; içerik kesilmez.
    assert any(big in c.chunk_text for c in chunks)
    hline = _header_line(header)
    assert all(c.chunk_text.splitlines()[0] == hline for c in chunks)

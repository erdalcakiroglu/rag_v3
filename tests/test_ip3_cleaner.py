"""İP-3 deterministik cleaning (regresyon suiti; DB gerekmez).

v1 document_cleaning.py çalışma alanında bulunmadığından, bu testler İP-3
kurallarını (boş satır, header/footer, ftfy, gereksiz karakter, anlam korunur)
kodlar ve ileriye dönük regresyon setidir.
"""

from __future__ import annotations

from ragintel.ingestion.cleaning import clean_document
from ragintel.ingestion.parsing.parsed_document import (
    Figure,
    Page,
    ParsedDocument,
    Section,
    Table,
)


def _doc(pages_blocks, **kw):
    return ParsedDocument(pages=[Page(i + 1, b) for i, b in enumerate(pages_blocks)], **kw)


def test_blank_and_whitespace_blocks_removed():
    r = clean_document(_doc([["Gerçek metin.", "   ", "", "\t"]]))
    assert r.document.pages[0].text_blocks == ["Gerçek metin."]
    assert r.metrics["blank_blocks_removed"] == 3


def test_junk_chars_and_inline_whitespace():
    r = clean_document(_doc([["Karbon​ vergisi­  çok    boşluk"]]))
    assert r.document.pages[0].text_blocks == ["Karbon vergisi çok boşluk"]


def test_ftfy_fixes_broken_encoding():
    r = clean_document(_doc([["Ã©tude Ã¼zerine"]]))
    assert r.document.pages[0].text_blocks[0].startswith("étude üzerine")
    assert r.metrics["encoding_fixes"] == 1


def test_meaning_preserved_no_stopword_or_punctuation_removal():
    text = "Karbon vergisi nedir? Bu bir sorudur ve cevabı vardır."
    r = clean_document(_doc([[text]]))
    assert r.document.pages[0].text_blocks[0] == text   # anlam/punct korunur


def test_header_footer_repeated_across_pages_removed():
    pages = [["GIZLI - Ust Bilgi", f"Icerik {i}.", "Alt Bilgi www.x.com"] for i in range(4)]
    r = clean_document(_doc(pages))
    for p in r.document.pages:
        assert p.text_blocks == [f"Icerik {p.page_no - 1}."]
    assert r.metrics["header_footer_removed"] == 8      # 2 imza × 4 sayfa
    assert len(r.metrics["header_footer_signatures"]) == 2


def test_no_header_footer_removal_under_three_pages():
    pages = [["Tekrar Satır", "Icerik A"], ["Tekrar Satır", "Icerik B"]]
    r = clean_document(_doc(pages))
    # 3 sayfadan az -> boilerplate kaldırılmaz
    assert r.metrics["header_footer_removed"] == 0
    assert "Tekrar Satır" in r.document.pages[0].text_blocks


def test_tables_and_figures_pass_through_ip2_wins():
    doc = _doc([["gövde"]],
               tables=[Table(0, [["a", "b"]], "a | b", page_no=1)],
               figures=[Figure(0, page_no=1, caption="şekil")],
               sections=[Section("Başlık", 1, page_start=1)])
    r = clean_document(doc)
    assert len(r.document.tables) == 1 and r.document.tables[0].flattened_text == "a | b"
    assert len(r.document.figures) == 1
    assert r.document.sections[0].title == "Başlık"


def test_retention_ratio_computed():
    # 100 karakterlik gövde; header/footer yok, sadece küçük kırpma.
    doc = _doc([["A" * 100]])
    r = clean_document(doc)
    assert r.metrics["original_chars"] == 100
    assert r.metrics["retention_ratio"] == 1.0

    heavy = _doc([["kısa", "SILINECEK BOILERPLATE UZUN SATIR " * 2] for _ in range(4)])
    r2 = clean_document(heavy)
    assert r2.metrics["retention_ratio"] < 1.0

"""İP-2 fallback parse: her tip ParsedDocument üretir; tablo/sayfa/section kuralları.
(DB gerekmez; backend=fallback.)"""

from __future__ import annotations

import pytest

from ragintel.config.loader import load_config
from ragintel.ingestion.parsing import ParseAdapter, ParsedDocument, get_backend
from tests import _corpus_parse as cp


@pytest.fixture
def adapter():
    return ParseAdapter(db=None, config=load_config(db_reader=None),
                        backend=get_backend("fallback"))


def test_txt_produces_parsed_document(adapter, tmp_path):
    p = str(tmp_path / "a.txt")
    cp.make_txt(p)
    pd = adapter.parse_path(p, "txt")
    assert isinstance(pd, ParsedDocument)
    assert pd.page_count == 1
    assert pd.body_text.strip()
    assert pd.language == "tr"


def test_xlsx_content_goes_to_tables(adapter, tmp_path):
    p = str(tmp_path / "a.xlsx")
    cp.make_xlsx(p)
    pd = adapter.parse_path(p, "xlsx")
    assert len(pd.tables) == 1
    assert pd.tables[0].sheet_name == "Sayfa1"
    assert ["Ad", "Değer"] == [str(c) for c in pd.tables[0].data[0]]
    assert "Karbon" in pd.tables[0].flattened_text


def test_pdf_pages_and_sections_preserved(adapter, tmp_path):
    p = str(tmp_path / "a.pdf")
    cp.make_text_pdf(p, title="Bolum Basligi")   # ASCII (pymupdf font kısıtı)
    pd = adapter.parse_path(p, "pdf")
    assert pd.page_count == 1
    # büyük-font başlık section olarak yakalanır, page_start korunur
    assert any(s.title.startswith("Bolum Basligi") for s in pd.sections)
    assert pd.sections[0].page_start == 1
    assert pd.sections[0].char_span is not None


def test_pdf_table_separated_from_body(adapter, tmp_path):
    p = str(tmp_path / "t.pdf")
    cp.make_table_pdf(p)
    pd = adapter.parse_path(p, "pdf")
    # tablo tables[]'a düşer
    assert len(pd.tables) == 1
    assert pd.tables[0].page_no == 1
    assert ["Karbon", "42"] in [[str(c) for c in row] for row in pd.tables[0].data]
    # tablo hücreleri gövde metnine KARIŞMAZ
    body = pd.body_text
    assert "govde metni" in body.lower()
    assert "Karbon" not in body and "42" not in body


def test_docx_headings_and_table(adapter, tmp_path):
    p = str(tmp_path / "a.docx")
    cp.make_docx(p)
    pd = adapter.parse_path(p, "docx")
    titles = [s.title for s in pd.sections]
    assert "Ana Başlık" in titles and "Alt Başlık" in titles
    # heading seviyeleri stil'den
    levels = {s.title: s.level for s in pd.sections}
    assert levels["Ana Başlık"] == 1 and levels["Alt Başlık"] == 2
    # tablo ayrı, gövdede değil (tablo-özel token gövdede görünmez)
    assert len(pd.tables) == 1
    table_flat = pd.tables[0].flattened_text
    assert "TbKarbon" in table_flat
    assert "TbKarbon" not in pd.body_text
    assert pd.language == "tr"


def test_scanned_pdf_zero_coverage_but_no_crash(adapter, tmp_path):
    from ragintel.ingestion.parsing import compute_parse_metrics
    p = str(tmp_path / "scan.pdf")
    cp.make_scanned_pdf(p)
    pd = adapter.parse_path(p, "pdf")             # hatasız ParsedDocument
    assert isinstance(pd, ParsedDocument)
    m = compute_parse_metrics(pd, "pdf")
    assert m["coverage"] == 0.0                   # metin katmanı yok
    assert m["page_ratio"] >= 0.0

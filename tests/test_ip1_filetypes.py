"""İP-1 içerik-tabanlı tip tespiti (DB gerekmez)."""

from __future__ import annotations

import os

from ragintel.ingestion.filetypes import detect_type
from tests import _corpus


def test_detects_supported_types_by_content(tmp_path):
    txt = str(tmp_path / "a.txt"); _corpus.make_txt(txt)
    pdf = str(tmp_path / "a.pdf"); _corpus.make_pdf(pdf)
    docx = str(tmp_path / "a.docx"); _corpus.make_docx(docx)
    xlsx = str(tmp_path / "a.xlsx"); _corpus.make_xlsx(xlsx)

    assert detect_type(txt) == "txt"
    assert detect_type(pdf) == "pdf"
    assert detect_type(docx) == "docx"
    assert detect_type(xlsx) == "xlsx"


def test_detects_types_with_turkish_filenames(tmp_path):
    """Regresyon: Türkçe/Unicode adlı dosyalar libmagic'te 'cannot open' verip
    yanlışlıkla reddedilmemeli. Tip içerikten (buffer) tespit edilir."""
    cases = [
        ("çalışma_öğüt_İĞÜ.txt", _corpus.make_txt, "txt"),
        ("içerik_şğüöçİ.pdf", _corpus.make_pdf, "pdf"),
        ("belge_ığçöşü.docx", _corpus.make_docx, "docx"),
        ("tablo_ÇĞİÖŞÜ.xlsx", _corpus.make_xlsx, "xlsx"),
    ]
    for name, maker, expected in cases:
        p = str(tmp_path / name)
        maker(p)
        assert detect_type(p) == expected, f"Türkçe adlı {name} tespit edilemedi"


def test_type_from_content_not_extension(tmp_path):
    """Uzantı .docx ama içerik PDF -> pdf (kural: uzantıya güvenme)."""
    p = str(tmp_path / "mislabeled.docx")
    _corpus.make_pdf(p)
    assert detect_type(p) == "pdf"


def test_unsupported_type_returns_none(tmp_path):
    png = str(tmp_path / "img.png")
    _corpus.make_png(png)
    assert detect_type(png) is None


def test_real_corpus_docx_detected(tmp_path):
    """Gerçek docx (raw_files) varsa içerikten docx tespit edilir."""
    real = "raw_files/08-Karbon_vergisi_WikiPedia.docx"
    if os.path.exists(real):
        assert detect_type(real) == "docx"

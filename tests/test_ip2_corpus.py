"""İP-2 kabul: temsili korpus (min 5 pdf/1 taranmış, 3 docx, 2 xlsx, 2 txt)
hatasız ParsedDocument üretir. (fallback backend; DB gerekmez.)"""

from __future__ import annotations

from ragintel.config.loader import load_config
from ragintel.ingestion.parsing import ParseAdapter, ParsedDocument, get_backend
from tests import _corpus_parse as cp


def test_corpus_all_types_produce_parsed_document(tmp_path):
    corpus = cp.build_corpus(str(tmp_path / "corpus"))
    assert len(corpus["pdf"]) == 5 and len(corpus["scanned"]) == 1
    assert len(corpus["docx"]) == 3
    assert len(corpus["xlsx"]) == 2
    assert len(corpus["txt"]) == 2

    adapter = ParseAdapter(db=None, config=load_config(db_reader=None),
                           backend=get_backend("fallback"))

    ftype_of = {}
    for ft, paths in corpus.items():
        if ft == "scanned":
            continue
        for p in paths:
            ftype_of[p] = ft

    parsed_ok = 0
    for path, ftype in ftype_of.items():
        pd = adapter.parse_path(path, ftype)          # exception atmamalı
        assert isinstance(pd, ParsedDocument)
        parsed_ok += 1
    assert parsed_ok == 12   # 5 + 3 + 2 + 2

    # Tablolu pdf tablo üretir; taranmış pdf hatasız ama coverage 0.
    from ragintel.ingestion.parsing import compute_parse_metrics
    tbl_pdf = [p for p in corpus["pdf"] if p.endswith("table.pdf")][0]
    assert len(adapter.parse_path(tbl_pdf, "pdf").tables) == 1

    scan_pdf = corpus["scanned"][0]
    m = compute_parse_metrics(adapter.parse_path(scan_pdf, "pdf"), "pdf")
    assert m["coverage"] == 0.0

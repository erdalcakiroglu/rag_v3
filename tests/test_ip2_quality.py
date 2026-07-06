"""İP-2 parse kalite ölçümü + config-güdümlü karar (DB gerekmez).

Ek-A kuralları: coverage/garbage/table_count ölçülür; hard-fail/soft-flag/OCR
kararları YALNIZCA app_config('quality')'den okunan eşiklerle verilir.
"""

from __future__ import annotations

from ragintel.config.loader import load_config
from ragintel.ingestion.parsing import ParseAdapter, compute_parse_metrics, get_backend
from ragintel.ingestion.parsing.parsed_document import Page, ParsedDocument, Table
from ragintel.ingestion.parsing.quality import garbage_ratio


def test_coverage_and_score_full_text():
    pd = ParsedDocument(pages=[Page(1, ["Dolu metin bloğu."]), Page(2, ["Devam metni."])])
    m = compute_parse_metrics(pd, "pdf")
    assert m["coverage"] == 1.0
    assert m["parse_score"] == 100.0
    assert m["page_count"] == 2


def test_coverage_partial_scanned():
    # 1 metinli, 3 boş sayfa -> coverage 0.25
    pages = [Page(1, ["metin"]), Page(2, []), Page(3, []), Page(4, [])]
    m = compute_parse_metrics(ParsedDocument(pages=pages), "pdf")
    assert m["coverage"] == 0.25


def test_garbage_ratio_detects_mojibake():
    assert garbage_ratio("temiz metin") == 0.0
    r = garbage_ratio("bozuk���metin")
    assert r > 0.15


def test_xlsx_content_coverage_one():
    pd = ParsedDocument(tables=[Table(0, [["a", "b"]], "a | b", sheet_name="S1")])
    m = compute_parse_metrics(pd, "xlsx")
    assert m["coverage"] == 1.0
    assert m["table_count"] == 1


def test_hard_fail_decision_reads_config():
    """Eşik config'ten: coverage 0.25 varsayılanda (0.50) hard-fail, eşik
    düşürülünce hard-fail KALKAR — kodda sabit yok kanıtı."""
    from ragintel.ingestion.parsing.adapter import ParseAttempt

    scanned = ParsedDocument(pages=[Page(1, []), Page(2, []), Page(3, []), Page(4, ["x"])])
    metrics = compute_parse_metrics(scanned, "pdf")  # coverage 0.25

    # Varsayılan eşik (hard_fail_coverage=0.50) -> hard fail.
    ad_default = ParseAdapter(db=None, config=load_config(db_reader=None),
                              backend=get_backend("fallback"))
    att = ParseAttempt(1, False, 5, metrics=metrics, parsed=scanned)
    assert ad_default._judge(att, "pdf")[0] == "FAILED"

    # Eşiği ENV'den düşür (0.10) -> aynı dosya artık PARSED.
    cfg_low = load_config(db_reader=None,
                          environ={"RAGINTEL_QUALITY_PARSE": '{"hard_fail_coverage":0.10}'})
    ad_low = ParseAdapter(db=None, config=cfg_low, backend=get_backend("fallback"))
    assert ad_low._judge(att, "pdf")[0] == "PARSED"


def test_ocr_trigger_decision_reads_config():
    """coverage < ocr_fallback.trigger_coverage_below VE pdf -> tetik."""
    cfg = load_config(db_reader=None)
    q = cfg.group("quality").ocr_fallback
    assert q.enabled is True
    # 0.25 < 0.50 -> tetiklenmeli (mantık adapter.parse_file'da; burada eşik teyidi)
    assert 0.25 < q.trigger_coverage_below

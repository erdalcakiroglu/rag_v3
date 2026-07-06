"""İP-5 chunk kalite ölçümü + İP-6 metadata doluluk (DB gerekmez)."""

from __future__ import annotations

from ragintel.ingestion.chunking import compute_chunk_metrics, metadata_fill_report
from ragintel.ingestion.chunking.chunk import Chunk


def _c(idx, tokens, **kw):
    return Chunk(idx, f"t{idx}", f"t{idx}", tokens, **kw)


def test_token_distribution_and_score():
    chunks = [_c(0, 512, section_title="A"), _c(1, 512, section_title="A"),
              _c(2, 300, section_title="B"), _c(3, 100, section_title=None)]
    m = compute_chunk_metrics(chunks, max_tokens=512, min_tokens=30)
    assert m["chunk_count"] == 4
    assert m["token_max"] == 512
    assert m["truncated_ratio"] == 0.5          # 2/4 tam maxta
    assert m["below_min_ratio"] == 0.0
    assert m["section_alignment_ratio"] == 0.75  # 3/4 section'lı
    assert 0 <= m["chunk_score"] <= 100


def test_truncated_and_below_min_ratios():
    chunks = [_c(0, 512), _c(1, 10), _c(2, 20)]   # 1 truncated, 2 below min(30)
    m = compute_chunk_metrics(chunks, max_tokens=512, min_tokens=30)
    assert round(m["truncated_ratio"], 3) == round(1 / 3, 3)
    assert round(m["below_min_ratio"], 3) == round(2 / 3, 3)


def test_metadata_fill_report_ip6():
    chunks = [
        Chunk(0, "a", "a", 5, page_number=1, section_title="S", char_start=0, char_end=1),
        Chunk(1, "b", "b", 5, page_number=2, section_title=None, char_start=2, char_end=3),
        Chunk(2, "t", "t", 5, sheet_name="Sheet1", is_table=True),
    ]
    r = metadata_fill_report(chunks)
    assert r["chunk_count"] == 3
    assert round(r["page_number_fill"], 3) == round(2 / 3, 3)
    assert round(r["section_title_fill"], 3) == round(1 / 3, 3)
    assert round(r["sheet_name_fill"], 3) == round(1 / 3, 3)
    assert round(r["char_span_fill"], 3) == round(2 / 3, 3)


def test_empty_metrics_safe():
    assert compute_chunk_metrics([], max_tokens=512, min_tokens=30)["chunk_count"] == 0
    assert metadata_fill_report([])["chunk_count"] == 0

"""İP-3.4 context builder: dedup, adjacency merge, budget and quality flags."""

from __future__ import annotations

from dataclasses import dataclass

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ragintel.config.loader import load_config
from ragintel.observability.tracing import _reset_tracing_for_tests, configure_tracing, force_flush_tracing
from ragintel.retrieval import ContextBuilder


@dataclass
class _Meta:
    chunk_id: int
    file_id: int
    chunk_index: int
    token_count: int
    page_number: int | None
    sheet_name: str | None
    section_title: str | None
    quality_score: float | None


class _Store:
    def __init__(self, mapping):
        self.mapping = mapping

    def fetch(self, chunk_ids: list[int]):
        return {chunk_id: self.mapping[chunk_id] for chunk_id in chunk_ids}


class _Counter:
    def count(self, text: str) -> int:
        return len([w for w in text.split() if w])

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        return []


def _cfg(**retrieval_cfg):
    defaults = {
        "context_token_budget": 100,
        "context_token_safety_margin": 1.0,
        "context_low_quality_threshold": 70.0,
    }
    defaults.update(retrieval_cfg)
    return load_config(db_reader=lambda: {"retrieval": defaults})


def _chunk(chunk_id: int, text: str, score: float, *, file_id: int, file_name: str, page=1, section="Genel"):
    return {
        "chunk_id": chunk_id,
        "text": text,
        "score": score,
        "source": {
            "file_id": file_id,
            "file_name": file_name,
            "page": page,
            "section": section,
            "version": 1,
        },
        "retrieval_method": "hybrid",
    }


def test_dedup_and_adjacent_merge_preserve_all_citations():
    store = _Store(
        {
            10: _Meta(10, 1, 0, 5, 2, None, "Vergi", 85.0),
            11: _Meta(11, 1, 1, 6, 2, None, "Vergi", 85.0),
            20: _Meta(20, 2, 0, 4, None, "Sayfa1", "Tablo", 90.0),
        }
    )
    builder = ContextBuilder(config=_cfg(), token_counter=_Counter(), metadata_store=store)
    result = builder.build(
        [
            _chunk(10, "ilk parca", 0.8, file_id=1, file_name="a.pdf", page=2, section="Vergi"),
            _chunk(10, "ilk parca", 0.9, file_id=1, file_name="a.pdf", page=2, section="Vergi"),
            _chunk(11, "ikinci parca", 0.7, file_id=1, file_name="a.pdf", page=2, section="Vergi"),
            _chunk(20, "tablo satiri", 0.6, file_id=2, file_name="b.xlsx", page=None, section="Tablo"),
        ]
    )

    assert [block["chunk_ids"] for block in result["blocks"]] == [[10, 11], [20]]
    assert result["blocks"][0]["label"] == "[1] a.pdf, s.2, Vergi"
    assert result["blocks"][1]["label"] == "[2] b.xlsx, sheet Sayfa1, Tablo"
    assert [c["chunk_id"] for c in result["citations"] if c["n"] == 1] == [10, 11]
    assert [c["chunk_id"] for c in result["citations"]] == [10, 11, 20]


def test_budget_drops_lowest_scored_blocks_and_preserves_order():
    store = _Store(
        {
            1: _Meta(1, 1, 0, 8, 1, None, "A", 90.0),
            2: _Meta(2, 2, 0, 8, 1, None, "B", 90.0),
            3: _Meta(3, 3, 0, 8, 1, None, "C", 90.0),
        }
    )
    _reset_tracing_for_tests()
    exporter = InMemorySpanExporter()
    configure_tracing(exporter=exporter, force=True)
    builder = ContextBuilder(
        config=_cfg(context_token_budget=16, context_token_safety_margin=1.0),
        token_counter=_Counter(),
        metadata_store=store,
    )
    result = builder.build(
        [
            _chunk(1, "bir iki uc dort", 0.9, file_id=1, file_name="a.pdf"),
            _chunk(2, "bes alti yedi sekiz", 0.2, file_id=2, file_name="b.pdf"),
            _chunk(3, "dokuz on onbir oniki", 0.8, file_id=3, file_name="c.pdf"),
        ]
    )
    force_flush_tracing()

    assert [block["chunk_ids"] for block in result["blocks"]] == [[1], [3]]
    assert result["dropped_chunk_ids"] == [2]
    span = [s for s in exporter.get_finished_spans() if s.name == "retrieval.build_context"][-1]
    assert span.attributes["dropped_chunk_count"] == 1


def test_quality_flag_respects_threshold_config():
    store = _Store(
        {
            1: _Meta(1, 1, 0, 4, 1, None, "A", 65.0),
            2: _Meta(2, 2, 0, 4, 1, None, "B", 75.0),
        }
    )
    chunks = [
        _chunk(1, "dusuk kalite", 0.9, file_id=1, file_name="a.pdf"),
        _chunk(2, "iyi kalite", 0.8, file_id=2, file_name="b.pdf"),
    ]

    low = ContextBuilder(config=_cfg(context_low_quality_threshold=70.0), token_counter=_Counter(), metadata_store=store)
    high = ContextBuilder(config=_cfg(context_low_quality_threshold=60.0), token_counter=_Counter(), metadata_store=store)
    assert [b["low_quality"] for b in low.build(chunks)["blocks"]] == [True, False]
    assert [b["low_quality"] for b in high.build(chunks)["blocks"]] == [False, False]


def test_citation_map_is_lossless_for_every_block():
    store = _Store(
        {
            101: _Meta(101, 9, 0, 3, 4, None, "S1", 88.0),
            102: _Meta(102, 9, 1, 3, 4, None, "S1", 88.0),
            201: _Meta(201, 8, 0, 3, None, "Tab1", "SheetSec", 88.0),
        }
    )
    builder = ContextBuilder(config=_cfg(), token_counter=_Counter(), metadata_store=store)
    result = builder.build(
        [
            _chunk(101, "a", 0.9, file_id=9, file_name="doc.pdf", page=4, section="S1"),
            _chunk(102, "b", 0.8, file_id=9, file_name="doc.pdf", page=4, section="S1"),
            _chunk(201, "c", 0.7, file_id=8, file_name="tab.xlsx", page=None, section="SheetSec"),
        ]
    )
    by_block = {block["n"]: set(block["chunk_ids"]) for block in result["blocks"]}
    cited = {}
    for item in result["citations"]:
        cited.setdefault(item["n"], set()).add(item["chunk_id"])
    assert cited == by_block

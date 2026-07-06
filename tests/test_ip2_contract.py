"""ParsedDocument kontrat şekli (DONMUŞ alanlar) — DB gerekmez."""

from __future__ import annotations

import dataclasses

from ragintel.ingestion.parsing import Figure, Page, ParsedDocument, Section, Table


def test_parsed_document_frozen_field_set():
    fields = {f.name for f in dataclasses.fields(ParsedDocument)}
    assert fields == {"pages", "sections", "tables", "figures",
                      "language", "parse_warnings"}


def test_element_contracts():
    assert {f.name for f in dataclasses.fields(Page)} == {"page_no", "text_blocks"}
    assert {f.name for f in dataclasses.fields(Section)} == {
        "title", "level", "page_start", "char_span"}
    assert {f.name for f in dataclasses.fields(Table)} == {
        "index", "data", "flattened_text", "page_no", "sheet_name"}
    assert {f.name for f in dataclasses.fields(Figure)} == {
        "index", "page_no", "caption"}


def test_defaults_and_helpers():
    pd = ParsedDocument()
    assert pd.pages == [] and pd.parse_warnings == []
    pd.warn("uyarı")
    assert pd.parse_warnings == ["uyarı"]
    pd.pages.append(Page(1, ["a", "b"]))
    assert pd.page_count == 1
    assert pd.body_text == "a\nb"

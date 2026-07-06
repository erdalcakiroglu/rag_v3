"""normalize_for_quote — tek doğruluk kaynağı davranışı (DB gerekmez)."""

from __future__ import annotations

from ragintel.text import normalize_for_quote, normalize_for_search


def test_lowercase_and_whitespace_collapse():
    assert normalize_for_quote("  Karbon   Vergisi\t\nNedir?  ") == "karbon vergisi nedir?"


def test_nfkc_normalization():
    # NFKC: tam-genişlik ve ligatür/uyumluluk formları kanonikleşir.
    assert normalize_for_quote("ﬁle") == "file"          # ﬁ ligatürü -> fi
    assert normalize_for_quote("Ｈｅｌｌｏ") == "hello"        # tam genişlik


def test_deterministic_and_idempotent():
    s = "  ÇOK   satırlı\n\n metin  "
    once = normalize_for_quote(s)
    assert once == normalize_for_quote(s)
    assert normalize_for_quote(once) == once             # idempotent


def test_empty_and_none_safe():
    assert normalize_for_quote("") == ""


def test_single_source_import_path():
    # İP-5 (chunk_text_norm) ve FAZ 4 validation AYNI importu kullanır.
    from ragintel.text.normalize import normalize_for_quote as direct
    from ragintel.text import normalize_for_quote as pkg
    assert direct is pkg


def test_search_normalizer_has_distinct_name_same_canonicalization():
    assert normalize_for_search("  Karbon   Vergisi\t\nNedir?  ") == "karbon vergisi nedir?"
    assert normalize_for_search is not normalize_for_quote

"""İP-5 gerçek BGE-M3 tokenizer ile chunking — YAVAŞ (HF tokenizer yükleme).

Varsayılan koşuda atlanır: `pytest -m slow`. İlk çalıştırmada tokenizer
dosyaları HF'ten indirilir (internet gerekir).
"""

from __future__ import annotations

import pytest

from ragintel.ingestion.chunking import BGEM3TokenCounter, chunk_document
from ragintel.ingestion.parsing.parsed_document import Page, ParsedDocument, Section

pytestmark = pytest.mark.slow


def test_real_bge_m3_token_limits():
    counter = BGEM3TokenCounter()
    # Gerçek token sayımıyla max_tokens=512 sınırına uyum.
    big = " ".join(f"kelime{i}" for i in range(4000))
    doc = ParsedDocument(pages=[Page(1, ["Başlık", big])],
                         sections=[Section("Başlık", 1, 1)])
    chunks = chunk_document(doc, counter=counter, strategy="section",
                            max_tokens=512, overlap_tokens=64, min_tokens=30)
    assert len(chunks) > 1
    # Stored token_count (pencere boyutu) hiçbir chunk'ta max'ı aşmaz.
    for c in chunks:
        assert c.token_count <= 512
        # İzole yeniden-tokenizasyon subword sınırında küçük sapabilir;
        # gross taşma olmamalı (model 8192 limitinin çok altında).
        assert counter.count(c.chunk_text) <= 512 + 32
    # Overlap: ardışık chunk char span'leri kesişir.
    body = [c for c in chunks if c.char_start is not None]
    for a, b in zip(body, body[1:]):
        assert a.char_end > b.char_start

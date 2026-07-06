"""Token sayımı — BGE-M3'ün KENDİ tokenizer'ı (İP-5 kuralı: tiktoken YASAK).

`TokenCounter` protokolü char-span'li token sayımı sağlar (chunk sınırlarını
karaktere geri eşlemek için). Gerçek sayaç `BGEM3TokenCounter` (AutoTokenizer,
"BAAI/bge-m3", fast/offset_mapping). Testler enjekte edilebilir bir sahte sayaç
kullanarak hızlı/offline kalır; gerçek tokenizer `slow` test ile doğrulanır.
"""

from __future__ import annotations

import re
from typing import Protocol


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...
    def token_spans(self, text: str) -> list[tuple[int, int]]: ...


class BGEM3TokenCounter:
    """BGE-M3 HF tokenizer ile token sayımı (embedding modeliyle uyumlu)."""

    def __init__(self, model_name: str = "BAAI/bge-m3"):
        self.model_name = model_name
        self._tok = None

    @property
    def tok(self):
        if self._tok is None:
            from transformers import AutoTokenizer
            self._tok = AutoTokenizer.from_pretrained(self.model_name)
        return self._tok

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        if not text:
            return []
        enc = self.tok(text, add_special_tokens=False, return_offsets_mapping=True)
        return [(int(s), int(e)) for s, e in enc["offset_mapping"]]

    def count(self, text: str) -> int:
        return len(self.token_spans(text))


_WORD = re.compile(r"\S+")


class WordTokenCounter:
    """Deterministik, offline sahte sayaç (boşlukla ayrılmış her sözcük = 1 token).

    Yalnızca test/geliştirme; üretimde BGEM3TokenCounter kullanılır.
    """

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        return [(m.start(), m.end()) for m in _WORD.finditer(text)]

    def count(self, text: str) -> int:
        return len(self.token_spans(text))

"""Guard: ragintel/** içinde `tiktoken` DOĞRUDAN import edilemez (İP-5 netleştirmesi).

litellm tiktoken'ı transitif çeker; yasak "ragintel kodunda doğrudan token sayımı
için tiktoken kullanımı" olarak netleştirildi. Token sayımı BGE-M3 / Ollama meta'ya
bağlıdır. Bu test o sınırı kod düzeyinde kilitler.
"""

from __future__ import annotations

import re
from pathlib import Path

_RAGINTEL = Path(__file__).resolve().parent.parent / "ragintel"
_PATTERN = re.compile(r"^\s*(import\s+tiktoken|from\s+tiktoken\b)", re.MULTILINE)


def test_no_direct_tiktoken_import_in_ragintel():
    offenders = []
    for path in _RAGINTEL.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if _PATTERN.search(text):
            offenders.append(str(path.relative_to(_RAGINTEL.parent)))
    assert offenders == [], f"tiktoken doğrudan import edilmiş: {offenders}"

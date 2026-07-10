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


def test_scanner_would_catch_a_direct_import():
    """POZİTİF KONTROL: desen gerçekten yakalıyor mu? Yakalamıyorsa aşağıdaki tarama
    hiçbir ihlal bulamaz ve 'ihlal yok' iddiası vacuously geçer."""
    assert _PATTERN.search("import tiktoken\n")
    assert _PATTERN.search("from tiktoken import encoding_for_model\n")
    assert _PATTERN.search("    import tiktoken\n")            # girintili import da ihlaldir
    assert not _PATTERN.search("# import tiktoken (yasak)\n")  # yorum satırı ihlal değil


def test_no_direct_tiktoken_import_in_ragintel():
    offenders = []
    scanned = 0
    for path in _RAGINTEL.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        scanned += 1
        if _PATTERN.search(text):
            offenders.append(str(path.relative_to(_RAGINTEL.parent)))
    # ÖN-KOŞUL: hiç dosya taranmadıysa (yol yanlış / paket taşındı) "ihlal yok" iddiası
    # boş kümeyle geçerdi. Taramanın gerçekten kod gördüğünü kanıtla.
    assert scanned > 10, f"ragintel/ altında yalnızca {scanned} .py tarandı — yol yanlış olabilir"
    assert offenders == [], f"tiktoken doğrudan import edilmiş: {offenders}"

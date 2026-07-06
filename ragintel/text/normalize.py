"""normalize_for_quote — TEK DOĞRULUK KAYNAĞI (İP-3).

Aynı fonksiyon:
  - İP-5'te `core_chunks.chunk_text_norm` üretiminde,
  - FAZ 4 quote/grounding doğrulamasında,
  - İP-9 dosya-içi duplicate tespitinde
kullanılır. Değişirse fark migration'la görünür olmalı — bu yüzden burada tek
yerde tanımlıdır ve davranışı testlerle sabitlenmiştir.

Dönüşüm (İP-3 kuralı): unicode NFKC -> lowercase -> whitespace collapse.
Anlamı değiştirmez; yalnızca eşleştirme için kanonik biçim üretir.
"""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = t.lower()
    t = _WS.sub(" ", t)
    return t.strip()


def normalize_for_quote(text: str) -> str:
    """Metni eşleştirme için kanonik biçime indirger.

    Adımlar: NFKC normalizasyonu, küçük harf, ardışık boşlukların tek boşluğa
    indirgenmesi ve baş/son boşluk kırpımı.
    """
    return _normalize_text(text)


def normalize_for_search(text: str) -> str:
    """Arama sorgusunu hafif normalize eder.

    `normalize_for_quote` ile karıştırılmaması için ayrı adlandırılır; FAZ 3
    sparse retrieval sorgularında kullanılır.
    """
    return _normalize_text(text)

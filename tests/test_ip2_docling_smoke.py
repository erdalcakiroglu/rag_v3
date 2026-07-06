"""Docling (üretim birincil backend) smoke — YAVAŞ (torch import ~dk).

Varsayılan koşuda atlanır; çalıştırmak: `pytest -m slow tests/test_ip2_docling_smoke.py`
Docling -> ParsedDocument eşlemesinin gerçek bir PDF'te sayfa/tablo ürettiğini
doğrular (fallback testleri hızlı yol; bu gerçek backend'i kanıtlar).
"""

from __future__ import annotations

import os

import pytest

from ragintel.ingestion.parsing import compute_parse_metrics, get_backend

pytestmark = pytest.mark.slow

# Gerçek doküman kullanılır: docling layout/tablo modelleri sentetik minimal
# PDF'lerde kararsız olabilir (tensor padding hatası); gerçek metinli PDF stabil.
_REAL_PDF = "raw_files/09-KarbonVergisiNedir.pdf"


def test_docling_parses_real_pdf():
    if not os.path.exists(_REAL_PDF):
        pytest.skip(f"gerçek pdf yok: {_REAL_PDF}")

    backend = get_backend("docling")
    assert backend.name == "docling"
    pd = backend.parse(_REAL_PDF, "pdf", ocr=False)

    assert pd.page_count >= 1
    m = compute_parse_metrics(pd, "pdf")
    assert m["coverage"] > 0.0            # metin katmanı çıkarıldı
    assert pd.body_text.strip()          # gövde metni var
    # sayfa numaraları korunur (citation zinciri)
    assert all(p.page_no >= 1 for p in pd.pages)

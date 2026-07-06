"""İP-2 dosya-başına timeout sarmalayıcısı (DB gerekmez)."""

from __future__ import annotations

import time

import pytest

from ragintel.ingestion.parsing.adapter import run_with_timeout


def test_returns_value_when_fast():
    assert run_with_timeout(lambda: 21 * 2, timeout_sec=5) == 42


def test_raises_timeout_when_slow():
    with pytest.raises(TimeoutError):
        run_with_timeout(lambda: time.sleep(2), timeout_sec=0.2)

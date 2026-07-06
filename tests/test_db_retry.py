"""DB bağlantı retry/backoff testleri (İP-0: 3 deneme, exponential backoff,
anlaşılır hata). Gerçek DB gerekmez — connect/sleep enjekte edilir.
"""

from __future__ import annotations

import psycopg
import pytest

from ragintel.database.pool import DatabaseConnectionError, connect_with_retry


class _Recorder:
    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.attempts = 0
        self.sleeps: list[float] = []

    def connect(self, conninfo):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise psycopg.OperationalError("connection refused")
        return object()  # sahte bağlantı

    def sleep(self, seconds):
        self.sleeps.append(seconds)


def test_retries_three_times_then_raises_clear_error():
    """Hep başarısızsa: tam 3 deneme, aralarında 2 backoff, anlaşılır hata."""
    rec = _Recorder(fail_times=99)

    with pytest.raises(DatabaseConnectionError) as ei:
        connect_with_retry(
            "host=bad", retries=3, backoff_base=0.5,
            connect=rec.connect, sleep=rec.sleep,
        )

    assert rec.attempts == 3
    # Denemeler arası backoff: 0.5 * 2**0, 0.5 * 2**1 = [0.5, 1.0]; son denemeden sonra uyumaz.
    assert rec.sleeps == [0.5, 1.0]
    assert "3 denemede" in str(ei.value)
    # Orijinal hata zincirlenmiş olmalı.
    assert isinstance(ei.value.__cause__, psycopg.OperationalError)


def test_succeeds_on_second_attempt_no_further_retry():
    """İkinci denemede bağlanırsa: 2 deneme, 1 backoff, hata yok."""
    rec = _Recorder(fail_times=1)

    conn = connect_with_retry(
        "host=x", retries=3, backoff_base=0.5,
        connect=rec.connect, sleep=rec.sleep,
    )

    assert conn is not None
    assert rec.attempts == 2
    assert rec.sleeps == [0.5]


def test_succeeds_first_attempt_no_backoff():
    rec = _Recorder(fail_times=0)
    conn = connect_with_retry(
        "host=x", retries=3, backoff_base=0.5,
        connect=rec.connect, sleep=rec.sleep,
    )
    assert conn is not None
    assert rec.attempts == 1
    assert rec.sleeps == []


def test_invalid_retries_rejected():
    with pytest.raises(ValueError):
        connect_with_retry("host=x", retries=0, backoff_base=0.5)

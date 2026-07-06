"""PostgreSQL bağlantı yönetimi: tek connection pool (psycopg v3) + retry.

- Tek `ConnectionPool` (psycopg_pool); context manager ile connection alınır.
- İlk bağlantıda İP-0 kuralı: 3 deneme, exponential backoff, anlaşılır hata.
- `connect` ve `sleep` enjekte edilebilir → retry davranışı DB'siz test edilir.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Callable, Iterator

import psycopg
from psycopg_pool import ConnectionPool

from ..config.settings import DbSettings


class DatabaseConnectionError(RuntimeError):
    """Retry'lar tükendikten sonra bağlantı kurulamadı — anlaşılır özet taşır."""


def connect_with_retry(
    conninfo: str,
    *,
    retries: int,
    backoff_base: float,
    connect: Callable[..., psycopg.Connection] = psycopg.connect,
    sleep: Callable[[float], None] = time.sleep,
    logger=None,
) -> psycopg.Connection:
    """Bağlantıyı `retries` kez dener; exponential backoff uygular.

    Backoff bekleme süresi denemeler arası: base * 2**(deneme-1).
    Son deneme de başarısızsa `DatabaseConnectionError` fırlatır (orijinal
    hatayı zincirler).
    """
    if retries < 1:
        raise ValueError("retries en az 1 olmalı")

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return connect(conninfo)
        except psycopg.OperationalError as exc:
            last_exc = exc
            if logger is not None:
                logger.warning(
                    "db_connect_failed",
                    attempt=attempt,
                    max_attempts=retries,
                    error=str(exc),
                )
            if attempt < retries:
                sleep(backoff_base * (2 ** (attempt - 1)))

    raise DatabaseConnectionError(
        f"PostgreSQL bağlantısı {retries} denemede kurulamadı. "
        f"Son hata: {last_exc}"
    ) from last_exc


class Database:
    """Uygulama ömrü boyunca tek pool tutan bağlantı yöneticisi."""

    def __init__(self, settings: DbSettings, *, logger=None):
        self._settings = settings
        self._logger = logger
        self._pool: ConnectionPool | None = None

    def open(
        self,
        *,
        connect: Callable[..., psycopg.Connection] = psycopg.connect,
        sleep: Callable[[float], None] = time.sleep,
    ) -> "Database":
        """Pool'u açar. Önce retry'lı bir test bağlantısıyla erişilebilirliği
        doğrular (anlaşılır hata + backoff), sonra pool'u kurar."""
        s = self._settings
        # Erişilebilirlik doğrulaması (İP-0 kabul kriteri: retry + backoff).
        probe = connect_with_retry(
            s.conninfo(),
            retries=s.connect_retries,
            backoff_base=s.connect_backoff_base,
            connect=connect,
            sleep=sleep,
            logger=self._logger,
        )
        probe.close()

        self._pool = ConnectionPool(
            conninfo=s.conninfo(),
            min_size=s.pool_min_size,
            max_size=s.pool_max_size,
            open=True,
            kwargs={"autocommit": False},
        )
        if self._logger is not None:
            self._logger.info("db_pool_opened", **s.safe_summary())
        return self

    @property
    def pool(self) -> ConnectionPool:
        if self._pool is None:
            raise DatabaseConnectionError("Pool açılmadı; önce open() çağırın.")
        return self._pool

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        """Pool'dan bir connection ödünç alır (context manager)."""
        with self.pool.connection() as conn:
            yield conn

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None
            if self._logger is not None:
                self._logger.info("db_pool_closed")

    def __enter__(self) -> "Database":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

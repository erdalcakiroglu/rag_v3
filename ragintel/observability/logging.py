"""structlog tabanlı JSON loglama (İP-0).

Her kayıt JSON; `file_id` / `trace_id` alanları context'e bağlanabilir
(`bind_context`). Pipeline adımları bu alanları taşıyarak dosya bazlı izlemeyi
mümkün kılar.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog
from structlog.contextvars import (
    bind_contextvars,
    clear_contextvars,
    merge_contextvars,
)

_CONFIGURED = False


def configure_logging(*, level: str = "INFO", json: bool = True) -> None:
    """structlog + stdlib logging'i tek sefer yapılandırır."""
    global _CONFIGURED

    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=log_level)

    renderer = (
        structlog.processors.JSONRenderer()
        if json
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            merge_contextvars,  # bind_contextvars ile eklenen file_id/trace_id
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(name: str | None = None, **initial: Any) -> structlog.BoundLogger:
    """Yapılandırılmış logger döndürür (gerekirse varsayılanla yapılandırır)."""
    if not _CONFIGURED:
        configure_logging()
    logger = structlog.get_logger(name)
    return logger.bind(**initial) if initial else logger


def bind_context(**kwargs: Any) -> None:
    """Bu yürütme bağlamına alan bağlar (ör. file_id, trace_id).

    Sonraki tüm log kayıtları bu alanları taşır (context-local).
    """
    bind_contextvars(**kwargs)


def clear_context() -> None:
    """Bağlanmış context alanlarını temizler (dosya işlemi bitince)."""
    clear_contextvars()

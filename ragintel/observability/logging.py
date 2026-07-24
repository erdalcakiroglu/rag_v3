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


# M-12: anahtar-ADI sır çağrıştıran alanlar tamamen redakte edilir (değeri loglanmaz).
# Kemer+askı: uçlar şifreyi zaten event_dict'e koymaz, ama yanlışlıkla eklenirse burada tutulur.
_SECRET_KEY_HINTS = ("password", "passwd", "pwd", "secret", "token", "authorization", "api_key")
_REDACTED = "***"

# M-15: "token" ipucunun YAN HASARI — token SAYAÇLARI da redakte ediliyordu
# (`prompt_tokens`/`completion_tokens`/`tokens_per_sec` log'da "***"), bu yüzden
# latency anatomisi (prompt-eval mi üretim mi baskın?) ÖLÇÜLEMİYORDU.
# Muafiyet KASITLI OLARAK DAR: (1) anahtar adı bu listede BİREBİR olacak,
# (2) değeri SAYI olacak. Sır bir string'tir; sayaç bir sayıdır — iki koşul da
# sağlanmadıkça redaksiyon aynen sürer (M-12 kalkanı daralmaz).
_METRIC_KEY_ALLOWLIST = frozenset({
    "prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens",
    "tokens_used", "max_tokens", "tokens_per_sec", "token_count", "prompt_eval_count",
    "eval_count",
})


def _looks_secret(key: str) -> bool:
    k = key.lower()
    return any(h in k for h in _SECRET_KEY_HINTS)


def _is_metric(key: str, val: Any) -> bool:
    """Sır ipucuna takılan ama aslında SAYAÇ olan alan mı? (bkz `_METRIC_KEY_ALLOWLIST`)
    `bool` bilinçli olarak dışlanır — `int` alt sınıfıdır ama sayaç değildir."""
    return (key.lower() in _METRIC_KEY_ALLOWLIST
            and isinstance(val, (int, float))
            and not isinstance(val, bool))


def _pii_scrub_processor(logger, method_name, event_dict):
    """FAZ 6 P2 + M-12: (1) anahtar-adı sır olan alanları redakte eder (şifre/token log'a
    SIZMAZ); (2) kalan string değerlerde TCKN'yi maskeler. Lazy import — guardrails paketi
    ↔ logging dairesel import'unu önler."""
    from ..guardrails.pii import PiiPolicy, mask_pii

    pol = PiiPolicy(mask_tckn=True, mask_dates=False)  # log'da yalnızca TCKN scrub
    for key, val in list(event_dict.items()):
        if _looks_secret(key) and not _is_metric(key, val):
            event_dict[key] = _REDACTED          # M-12: sır değeri asla log'a
            continue
        if isinstance(val, str) and len(val) >= 11:
            masked, n = mask_pii(val, pol)
            if n:
                event_dict[key] = masked
    return event_dict


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
            _pii_scrub_processor,  # FAZ 6: TCKN log'a sızmaz
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

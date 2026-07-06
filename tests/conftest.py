"""Ortak test fixture'ları.

Öncelik zinciri testleri gerçek DB gerektirmez (db_reader enjekte edilir).
`db` işaretli testler canlı PostgreSQL'e bağlanır; erişilemezse atlanır.
"""

from __future__ import annotations

import os

import pytest

# Testler kendi ENV'lerini kontrol edebilsin diye RAGINTEL_* değişkenlerini
# oturum başında temizle (host makinede set olabilir).
_STALE = [k for k in os.environ if k.startswith("RAGINTEL_")]
for _k in _STALE:
    os.environ.pop(_k, None)


@pytest.fixture
def clean_env(monkeypatch):
    """RAGINTEL_* ortam değişkenlerinden arındırılmış environ döndürür."""
    for k in list(os.environ):
        if k.startswith("RAGINTEL_"):
            monkeypatch.delenv(k, raising=False)
    return {}


@pytest.fixture(scope="session")
def live_db():
    """Canlı DB `Database` nesnesi; erişilemezse testi atlar."""
    from ragintel.config.settings import DbSettings
    from ragintel.database import Database, DatabaseConnectionError

    settings = DbSettings()
    try:
        db = Database(settings).open()
    except DatabaseConnectionError as exc:
        pytest.skip(f"Canlı DB yok: {exc}")
    yield db
    db.close()

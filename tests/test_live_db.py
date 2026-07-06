"""Canlı DB entegrasyon testleri (İP-0 kabul kriteri: DB katmanı uçtan uca).

Bu testler gerçek PostgreSQL'e bağlanır; erişilemezse `live_db` fixture'ı atlar.
"""

from __future__ import annotations

import pytest

from ragintel.config.loader import load_config
from ragintel.database import make_db_reader, read_app_config

pytestmark = pytest.mark.db


def test_pool_roundtrip(live_db):
    """Pool'dan connection alınır ve basit sorgu çalışır."""
    with live_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            assert cur.fetchone()[0] == 1


def test_app_config_seed_present(live_db):
    """app_config seed grupları okunur (FAZ1_Sema.sql ile uyumlu)."""
    groups = read_app_config(live_db)
    assert {"chunking", "embedding", "ingestion"} <= set(groups)
    assert groups["embedding"]["model"] == "BAAI/bge-m3"
    assert groups["embedding"]["dim"] == 1024


def test_db_layer_wins_in_chain(live_db, clean_env):
    """DB'deki app_config değerleri efektif config'te 'db' kaynağıyla görünür."""
    cfg = load_config(db_reader=make_db_reader(live_db), environ={})
    # Seed DB'de mevcut olduğundan bu alanların kaynağı 'db' olmalı.
    assert cfg.source_of("embedding", "model") == "db"
    assert cfg.value("embedding", "dim") == 1024

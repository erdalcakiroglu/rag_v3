"""`ragintel.app_config` okuma/yazma — öncelik zincirinin DB katmanı.

`read_app_config` bir `Database` üzerinden grup->jsonb sözlüğü döndürür ve
`config.loader.load_config`'e `db_reader` olarak verilir.
"""

from __future__ import annotations

from typing import Mapping

from .pool import Database


def read_app_config(db: Database) -> Mapping[str, dict]:
    """app_config tablosundaki tüm grupları (config_key -> config_value) okur."""
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT config_key, config_value FROM app_config;")
            rows = cur.fetchall()
    return {key: value for key, value in rows}


def make_db_reader(db: Database):
    """`load_config(db_reader=...)` için bağlı bir okuyucu döndürür."""
    def _reader() -> Mapping[str, dict]:
        return read_app_config(db)
    return _reader

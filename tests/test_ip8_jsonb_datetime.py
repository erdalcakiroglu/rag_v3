"""İP-8 regresyon: XLSX tarih hücreleri (datetime) core_tables JSONB'de crash etmemeli.

BUG: 'storage write failed: Object of type datetime is not JSON serializable'.
Fix: storage_repo._jsonb, datetime/date/time'ı ISO string'e indirger.
"""

from __future__ import annotations

import datetime
import json

from ragintel.database.storage_repo import _json_default, _jsonb


def test_json_default_isoformats_temporal_types():
    assert _json_default(datetime.datetime(2026, 7, 5, 1, 0, 17)) == "2026-07-05T01:00:17"
    assert _json_default(datetime.date(2026, 7, 5)) == "2026-07-05"
    assert _json_default(datetime.time(1, 0, 17)) == "01:00:17"


def test_table_data_with_datetime_cell_serializes():
    # openpyxl'in tarih hücresi döndürdüğü tabloyu taklit et
    data = [["Server", "LastPatch"], ["ggb-01", datetime.datetime(2026, 7, 5, 1, 0, 17)]]
    out = json.dumps(data, ensure_ascii=False, default=_json_default)
    assert "2026-07-05T01:00:17" in out


def test_jsonb_wrapper_carries_datetime_safe_dumps():
    # _jsonb, Jsonb'yi crash etmeyen bir dumps ile sarmalı
    jb = _jsonb([["x", datetime.datetime(2026, 7, 5)]])
    dumped = jb.dumps(jb.obj)  # psycopg Jsonb: .obj + .dumps
    assert "2026-07-05" in dumped

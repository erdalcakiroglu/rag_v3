"""`ragintel.config show` CLI smoke testleri (İP-0 kabul kriteri)."""

from __future__ import annotations

import json

from ragintel.config import __main__ as cli
from ragintel.config.loader import load_config


def test_show_text_output_lists_values_and_sources(capsys, clean_env, monkeypatch):
    """`show` her ayarı değeri + kaynağıyla yazdırır."""
    cfg = load_config(db_reader=None, environ={"RAGINTEL_EMBEDDING_BATCH_SIZE": "8"})
    monkeypatch.setattr(cli, "_load_with_db", lambda: (cfg, None))

    rc = cli.main(["show"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "embedding" in out
    assert "batch_size" in out
    assert "kaynak: env" in out       # override edilen alan
    assert "kaynak: default" in out   # dokunulmayan alan
    assert "***" in out               # şifre maskeli


def test_show_json_output_is_valid_and_structured(capsys, clean_env, monkeypatch):
    cfg = load_config(db_reader=None, environ={})
    monkeypatch.setattr(cli, "_load_with_db", lambda: (cfg, None))

    rc = cli.main(["show", "--json"])
    out = capsys.readouterr().out

    assert rc == 0
    payload = json.loads(out)
    assert payload["priority"] == "db > env > default"
    assert payload["pipeline"]["embedding"]["dim"] == 1024
    assert payload["sources"]["embedding"]["dim"] == "default"
    assert payload["bootstrap"]["db"]["password"] == "***"


def test_show_reports_db_skip_warning(capsys, clean_env, monkeypatch):
    """DB erişilemezse uyarı gösterilir ama komut yine de başarılı döner."""
    cfg = load_config(db_reader=None, environ={})
    monkeypatch.setattr(
        cli, "_load_with_db", lambda: (cfg, "DB katmanı atlandı (test)")
    )
    rc = cli.main(["show"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "UYARI" in out and "atlandı" in out

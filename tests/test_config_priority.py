"""Config öncelik zinciri testleri (İP-0 kabul kriteri: en az 3 test).

Zincir: DB (app_config) > ENV > kod varsayılanı. Her test bir katmanın bir
üstteki katmanı override ettiğini ve kaynak izlemenin doğru olduğunu gösterir.
"""

from __future__ import annotations

from ragintel.config.loader import load_config
from ragintel.config.settings import default_pipeline_config


def test_defaults_when_no_env_no_db(clean_env):
    """Katman yoksa değerler kod varsayılanı ve kaynak 'default'."""
    cfg = load_config(db_reader=None, environ={})

    defaults = default_pipeline_config()
    assert cfg.value("embedding", "batch_size") == defaults["embedding"]["batch_size"]
    assert cfg.value("chunking", "max_tokens") == 512
    assert cfg.source_of("embedding", "batch_size") == "default"
    assert cfg.source_of("chunking", "max_tokens") == "default"
    # Seed ile birebir uyum (FAZ1_Sema.sql).
    assert cfg.value("embedding", "model") == "BAAI/bge-m3"
    assert cfg.value("ingestion", "allowed_types") == ["pdf", "docx", "xlsx", "txt"]


def test_env_overrides_default(clean_env):
    """ENV değeri varsayılanı ezer; kaynak 'env', tip koersiyonu çalışır."""
    environ = {
        "RAGINTEL_EMBEDDING_BATCH_SIZE": "8",   # str -> int
        "RAGINTEL_CHUNKING_STRATEGY": "paragraph",
    }
    cfg = load_config(db_reader=None, environ=environ)

    assert cfg.value("embedding", "batch_size") == 8
    assert isinstance(cfg.value("embedding", "batch_size"), int)
    assert cfg.source_of("embedding", "batch_size") == "env"

    assert cfg.value("chunking", "strategy") == "paragraph"
    assert cfg.source_of("chunking", "strategy") == "env"

    # Dokunulmayan alan varsayılan kalır.
    assert cfg.value("chunking", "max_tokens") == 512
    assert cfg.source_of("chunking", "max_tokens") == "default"


def test_db_overrides_env_and_default(clean_env):
    """DB katmanı hem ENV'i hem varsayılanı ezer (en yüksek öncelik)."""
    environ = {"RAGINTEL_EMBEDDING_BATCH_SIZE": "8"}

    def fake_db_reader():
        return {
            "embedding": {"batch_size": 64},          # env(8) ve default(32) üzerine
            "chunking": {"max_tokens": 256},          # default(512) üzerine
        }

    cfg = load_config(db_reader=fake_db_reader, environ=environ)

    assert cfg.value("embedding", "batch_size") == 64
    assert cfg.source_of("embedding", "batch_size") == "db"

    assert cfg.value("chunking", "max_tokens") == 256
    assert cfg.source_of("chunking", "max_tokens") == "db"


def test_partial_db_override_keeps_lower_layers(clean_env):
    """DB yalnızca bir alanı ezerse, aynı grubun diğer alanları alt katmandan gelir."""
    environ = {"RAGINTEL_EMBEDDING_NORMALIZE": "false"}  # env katmanı

    def fake_db_reader():
        return {"embedding": {"batch_size": 16}}  # yalnızca batch_size

    cfg = load_config(db_reader=fake_db_reader, environ=environ)

    assert cfg.value("embedding", "batch_size") == 16          # db
    assert cfg.source_of("embedding", "batch_size") == "db"
    assert cfg.value("embedding", "normalize") is False        # env
    assert cfg.source_of("embedding", "normalize") == "env"
    assert cfg.value("embedding", "model") == "BAAI/bge-m3"    # default
    assert cfg.source_of("embedding", "model") == "default"


def test_typed_group_access_and_validation(clean_env):
    """group() tip güvenli nesne döndürür; koersiyon uygulanmış olur."""
    environ = {"RAGINTEL_INGESTION_MAX_FILE_MB": "250"}
    cfg = load_config(db_reader=None, environ=environ)

    ingestion = cfg.group("ingestion")
    assert ingestion.max_file_mb == 250
    assert ingestion.allowed_types == ["pdf", "docx", "xlsx", "txt"]


def test_unknown_db_group_surfaced_untyped(clean_env):
    """app_config'te bilinmeyen bir grup (gelecekteki bir anahtar) yüzeye çıkar,
    kaynağı 'db' olur; bilinen grupların tiplemesi bozulmaz."""
    def fake_db_reader():
        return {"experimental": {"flag_x": True, "n": 3}}

    cfg = load_config(db_reader=fake_db_reader, environ={})
    assert cfg.pipeline["experimental"]["flag_x"] is True
    assert cfg.sources["experimental"]["n"] == "db"
    # Bilinen grup hâlâ tip güvenli.
    assert cfg.group("embedding").dim == 1024


def test_quality_group_defaults_match_ek1_seed(clean_env):
    """'quality' grubu tiplenmiş; defaultlar Ek1 seed'i ile birebir (İP-2)."""
    cfg = load_config(db_reader=None, environ={})
    q = cfg.group("quality")
    assert q.parse.hard_fail_coverage == 0.50
    assert q.parse.hard_fail_garbage == 0.20
    assert q.parse.soft_flag_coverage == 0.85
    assert q.ocr_fallback.enabled is True
    assert q.ocr_fallback.trigger_coverage_below == 0.50
    assert q.weights.parse == 0.35
    assert cfg.source_of("ingestion", "parse_timeout_sec") == "default"
    assert cfg.value("ingestion", "parse_timeout_sec") == 300

"""export-seed: deterministik, pydantic-türetimli, secret-sız DB seed üretimi."""

from __future__ import annotations

import types

import pytest
from pydantic import BaseModel

from ragintel.config.export_seed import (
    SEEDABLE_GROUPS,
    SeedSecretLeak,
    assert_no_secret_fields,
    build_seed_sql,
)
from ragintel.config.loader import load_config
from ragintel.config.settings import GROUP_MODELS


def _default_cfg():
    return load_config(db_reader=None)


def test_export_seed_is_deterministic():
    cfg = _default_cfg()
    assert build_seed_sql(cfg) == build_seed_sql(cfg)


def test_seed_covers_every_seedable_group():
    sql = build_seed_sql(_default_cfg())
    for group in SEEDABLE_GROUPS:
        assert f"('{group}'," in sql, f"grup seed'de yok: {group}"


def test_on_conflict_do_nothing_and_pydantic_description():
    sql = build_seed_sql(_default_cfg())
    assert "ON CONFLICT (config_key) DO NOTHING;" in sql
    # description hangi pydantic modelinden geldiğini söyler
    assert "AgentConfig (pydantic" in sql
    assert "RetrievalConfig (pydantic" in sql


def test_new_pydantic_field_auto_appears_in_seed():
    class FakeAgent(BaseModel):
        max_iterations: int = 4
        brand_new_experimental_field: str = "auto-derived"

    group_models = {**GROUP_MODELS, "agent": FakeAgent}
    cfg = types.SimpleNamespace(
        pipeline={
            **{g: GROUP_MODELS[g]().model_dump() for g in SEEDABLE_GROUPS if g != "agent"},
            "agent": FakeAgent().model_dump(),
        }
    )
    sql = build_seed_sql(cfg, group_models=group_models)
    assert "brand_new_experimental_field" in sql   # yeni alan otomatik göründü


def test_seed_is_model_driven_not_config_driven():
    # cfg'de fazladan (DB'den sızmış) anahtar olsa bile modelde yoksa seed'e girmez.
    class MiniAgent(BaseModel):
        only_field: int = 1

    group_models = {**GROUP_MODELS, "agent": MiniAgent}
    cfg = types.SimpleNamespace(
        pipeline={
            **{g: GROUP_MODELS[g]().model_dump() for g in SEEDABLE_GROUPS if g != "agent"},
            "agent": {"only_field": 1, "db_injected_junk": "SIZINTI"},
        }
    )
    sql = build_seed_sql(cfg, group_models=group_models)
    assert "only_field" in sql
    assert "db_injected_junk" not in sql
    assert "SIZINTI" not in sql


def test_no_secrets_or_connection_info_in_seed():
    sql = build_seed_sql(_default_cfg()).lower()
    for leak in ("password", "secret_key", "public_key", "api_base", "base_url",
                 "conninfo", "192.168.36.15", "banasor", "finagoseek", "11434", "8085"):
        assert leak not in sql, f"seed'de secret/bağlantı sızıntısı: {leak}"


def test_secret_guard_rejects_secret_like_field():
    class LeakyRetrieval(BaseModel):
        default_top_k: int = 10
        rerank_api_key: str = "sk-xxx"   # secret benzeri alan

    with pytest.raises(SeedSecretLeak):
        assert_no_secret_fields({**GROUP_MODELS, "retrieval": LeakyRetrieval})


def test_secret_guard_allows_tokenization_fields():
    # 'max_tokens'/'overlap_tokens' secret DEĞİL — guard bunları geçmeli.
    assert_no_secret_fields(GROUP_MODELS)  # varsayılan modeller temiz


def test_on_conflict_variants():
    cfg = _default_cfg()
    do_nothing = build_seed_sql(cfg, on_conflict="do-nothing")
    assert "DO NOTHING;" in do_nothing
    assert "jsonb_set" not in do_nothing            # do-nothing modunda backfill yok
    merge = build_seed_sql(cfg, on_conflict="merge")
    # merge: mevcut kazanır (canlı ezilmez), eksikler seed'den dolar
    assert "DO UPDATE" in merge
    assert "excluded.config_value || app_config.config_value" in merge
    with pytest.raises(ValueError):
        build_seed_sql(cfg, on_conflict="replace")


def test_merge_includes_guarded_nested_backfill():
    # İç-içe quality alt-anahtarları guard'lı jsonb_set ile doldurulmalı (canlı ezilmez).
    merge = build_seed_sql(_default_cfg(), on_conflict="merge")
    assert "jsonb_set(config_value, '{embed,anomaly_cosine_high}'" in merge
    # guard: yalnızca EKSİK anahtar yazılır
    assert "NOT (config_value #> '{embed}' ? 'anomaly_cosine_high')" in merge
    # canlı korunur: quality patterns/flat üst anahtarlar jsonb_set ile EZİLMEZ (yalnızca leaf backfill)
    assert "config_key = 'quality'" in merge


def test_dollar_quoting_survives_quotes_in_values():
    # injection grubu tek tırnak içeren regex kalıpları taşır; SQL bozulmamalı.
    sql = build_seed_sql(_default_cfg())
    inj = [line for line in sql.splitlines() if line.strip().startswith("('injection'")][0]
    assert "$cfg$" in inj and inj.count("$cfg$") == 2   # değer dollar-quote içinde

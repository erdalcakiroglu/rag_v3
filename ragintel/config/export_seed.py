"""Davranışsal config'in DB seed'ini (`Config_Seed.sql`) pydantic modellerinden
OTOMATİK üretir — İP-0 config zincirinin (DB > env > default) DB katmanını
eksiksiz doldurmak için.

Tasarım kararları:
- **Kaynak = pydantic `GROUP_MODELS`** (tek doğruluk kaynağı). Modele yeni alan
  eklenince seed'de otomatik görünür; ayrı liste tutulmaz.
- **Allowlist `SEEDABLE_GROUPS`** (davranışsal gruplar). Bootstrap/secret ayarları
  (`DbSettings`/`OllamaSettings`/`TeiSettings`/`LangfuseSettings`/`LiteLLMSettings`…)
  `BaseSettings`'tir, `GROUP_MODELS`'te DEĞİLDİR → yapısal olarak seed dışıdır.
  Ek güvence: `assert_no_secret_fields` seedable bir gruba secret benzeri alan
  sızarsa üretimi durdurur (yorumla değil, KODLA zorlanan ayrım).
- **Deterministik:** gruplar ve anahtarlar sıralı, JSON `sort_keys`, dosyada
  timestamp/volatile içerik YOK → iki üretim birebir aynı dosya.
- **ON CONFLICT (config_key) DO NOTHING:** mevcut DB değerlerini EZMEZ.
"""

from __future__ import annotations

import json
from typing import Mapping

from pydantic import BaseModel

from .loader import EffectiveConfig
from .settings import GROUP_MODELS, PIPELINE_GROUPS

# Seed'e giren davranışsal gruplar (açık allowlist). Bootstrap/secret buraya GİRMEZ.
# M-12: 'auth' PIPELINE_GROUPS'ta (loader tipler, panelden yönetilir) ama SEED'e GİRMEZ:
# güvenlik politikası operasyoneldir (allowlist/şifre-uzunluğu admin'in kararı; boş-allowlist
# fail-closed varsayılanı bilinçlidir) — davranışsal defaults değil. Ayrıca password_min_length
# adı secret-marker'a takılırdı (yanlış-pozitif). Seed = pipeline davranışı, güvenlik değil.
_SEED_EXCLUDED: tuple[str, ...] = ("auth",)
SEEDABLE_GROUPS: tuple[str, ...] = tuple(g for g in PIPELINE_GROUPS if g not in _SEED_EXCLUDED)

# Bir seedable alan adı bunlardan birini içeriyorsa bootstrap/secret sızıntısı sayılır.
# NOT: "token" bilinçli olarak YOK — davranışsal `max_tokens`/`overlap_tokens` ile
# çakışır. Secret'lar özel ad kalıplarıyla (password/secret/api_key/url/host…) yakalanır.
_SECRET_FIELD_MARKERS: tuple[str, ...] = (
    "password", "secret", "credential", "conninfo", "dsn",
    "api_key", "apikey", "access_key", "api_base", "base_url",
    "url", "host", "endpoint",
)

# Dollar-quote etiketi (JSONB değerinde tek tırnak/backslash kaçışı derdini önler).
_DOLLAR_TAG = "$cfg$"


class SeedSecretLeak(RuntimeError):
    """Seedable bir grupta bootstrap/secret benzeri alan tespit edildi."""


def assert_no_secret_fields(group_models: Mapping[str, type[BaseModel]] = GROUP_MODELS) -> None:
    """Allowlist'teki hiçbir grubun secret/bootstrap benzeri alan içermediğini garanti eder."""
    for group in SEEDABLE_GROUPS:
        model = group_models.get(group)
        if model is None:
            continue
        for field in model.model_fields:
            low = field.lower()
            if any(marker in low for marker in _SECRET_FIELD_MARKERS):
                raise SeedSecretLeak(
                    f"Seedable grup '{group}' alanı '{field}' secret/bootstrap görünüyor; "
                    "allowlist dışına çıkarılmalı (seed'e secret girmez)."
                )


def _iter_nested_leaves(value: dict, prefix: tuple[str, ...]):
    """İç-içe dict'in yaprak (leaf) yollarını (path, value) üretir."""
    for key in sorted(value):
        sub = value[key]
        if isinstance(sub, dict):
            yield from _iter_nested_leaves(sub, prefix + (key,))
        else:
            yield prefix + (key,), sub


def _nested_backfill_statements(cfg: EffectiveConfig, group_models: Mapping[str, type[BaseModel]]) -> list[str]:
    """İç-içe gruplarda (ör. quality) eksik alt-anahtarları guard'lı `jsonb_set` ile
    doldurur. `||` shallow olduğundan merge iç-içe boşlukları kapatmaz; bu bölüm
    kapatır. Guard (`NOT (... ? key)`) → yalnızca EKSİK anahtar yazılır, canlı EZİLMEZ.
    Yollar/değerler pydantic efektif config'ten otomatik türetilir (hardcode yok)."""
    stmts: list[str] = []
    for group in sorted(SEEDABLE_GROUPS):
        if group_models.get(group) is None:
            continue
        effective = cfg.pipeline.get(group, {})
        for top_key in sorted(effective):
            if not isinstance(effective[top_key], dict):
                continue  # yalnızca iç-içe (nested) top-level anahtarlar
            for path, leaf in _iter_nested_leaves(effective[top_key], (top_key,)):
                path_sql = "{" + ",".join(path) + "}"
                parent_sql = "{" + ",".join(path[:-1]) + "}"
                leaf_json = json.dumps(leaf, ensure_ascii=False)
                stmts.append(
                    f"UPDATE app_config SET config_value = "
                    f"jsonb_set(config_value, '{path_sql}', {_DOLLAR_TAG}{leaf_json}{_DOLLAR_TAG}::jsonb, true)\n"
                    f"    WHERE config_key = '{group}' AND NOT (config_value #> '{parent_sql}' ? '{path[-1]}');"
                )
    return stmts


def _group_rows(cfg: EffectiveConfig, group_models: Mapping[str, type[BaseModel]]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for group in sorted(SEEDABLE_GROUPS):
        model = group_models.get(group)
        if model is None:
            continue
        effective = cfg.pipeline.get(group, {})
        # Yalnızca modelin alanları, sıralı → deterministik.
        values = {field: effective[field] for field in sorted(model.model_fields) if field in effective}
        payload = json.dumps(values, ensure_ascii=False, sort_keys=True)
        if _DOLLAR_TAG in payload:
            raise ValueError(f"'{group}' değeri dollar-quote etiketiyle çakışıyor: {_DOLLAR_TAG}")
        description = f"{model.__name__} (pydantic — {model.__module__})"
        rows.append((group, payload, description))
    return rows


# ON CONFLICT davranışı:
#   do-nothing → mevcut grubu HİÇ değiştirmez (kısmi gruplarda eksik anahtarlar default kalır).
#   merge      → excluded.config_value || app_config.config_value: MEVCUT değerler kazanır
#                (canlı ayar EZİLMEZ), eksik anahtarlar seed'den DOLAR. NOT: jsonb `||`
#                sığdır (shallow) — iç-içe gruplarda (quality) alt-nesne bütün olarak korunur.
_ON_CONFLICT_SQL = {
    "do-nothing": "ON CONFLICT (config_key) DO NOTHING;",
    "merge": "ON CONFLICT (config_key) DO UPDATE\n    SET config_value = excluded.config_value || app_config.config_value;",
}


def build_seed_sql(
    cfg: EffectiveConfig,
    *,
    group_models: Mapping[str, type[BaseModel]] = GROUP_MODELS,
    on_conflict: str = "do-nothing",
) -> str:
    """Efektif config'ten deterministik `Config_Seed.sql` metni üretir."""
    if on_conflict not in _ON_CONFLICT_SQL:
        raise ValueError(f"Geçersiz on_conflict: {on_conflict!r} (do-nothing|merge)")
    assert_no_secret_fields(group_models)
    rows = _group_rows(cfg, group_models)

    header = [
        "-- =====================================================================",
        "-- Config_Seed.sql — davranışsal config'in DB seed'i (config-first, İP-0)",
        "-- OTOMATİK ÜRETİM: python -m ragintel.config export-seed  (elle DÜZENLEME).",
        "-- ELLE UYGULANIR (onay sonrası). ON CONFLICT DO NOTHING → canlı DB'yi EZMEZ.",
        "-- Kaynak: pydantic GROUP_MODELS (SEEDABLE_GROUPS allowlist).",
        "-- Bootstrap/secret (DB/Ollama/TEI/Langfuse/LiteLLM bağlantı+anahtarları) HARİÇ.",
        "-- =====================================================================",
        "",
        "INSERT INTO app_config (config_key, config_value, description) VALUES",
    ]
    value_lines = []
    for idx, (group, payload, description) in enumerate(rows):
        sep = "," if idx < len(rows) - 1 else ""
        desc_sql = description.replace("'", "''")
        value_lines.append(f"    ('{group}', {_DOLLAR_TAG}{payload}{_DOLLAR_TAG}, '{desc_sql}'){sep}")
    footer = [_ON_CONFLICT_SQL[on_conflict], ""]

    parts = [*header, *value_lines, *footer]

    # İç-içe alt-anahtar backfill (yalnızca merge; do-nothing zaten kısmi grubu atlar
    # ve flat boşluğu doldurmaz — o modda backfill yanıltıcı olurdu).
    if on_conflict == "merge":
        backfill = _nested_backfill_statements(cfg, group_models)
        if backfill:
            parts.append("-- İç-içe grup alt-anahtar backfill (guard'lı; canlı EZİLMEZ, yalnızca eksikler dolar)")
            parts.extend(backfill)
            parts.append("")

    return "\n".join(parts)

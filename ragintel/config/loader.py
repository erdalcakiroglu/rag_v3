"""Efektif konfigürasyonun öncelik zincirinden derlenmesi.

Zincir:  DB (`ragintel.app_config`)  >  ENV  >  kod varsayılanı

- Varsayılan katman: `settings.default_pipeline_config()`
- ENV katmanı: `RAGINTEL_<GRUP>__<ALAN>` biçimli ortam değişkenleri
- DB katmanı: `app_config` tablosundaki jsonb grupları (en yüksek öncelik)

Kaynak izlenebilirliği için her yaprak değerin geldiği katman kaydedilir.
DB okuyucu enjekte edilebilir (`db_reader`) — böylece öncelik zinciri testleri
gerçek DB olmadan çalışır.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from pydantic import BaseModel, ValidationError

from .resolver import merge_with_sources
from .settings import (
    GROUP_MODELS,
    PIPELINE_GROUPS,
    DbSettings,
    LogSettings,
    default_pipeline_config,
)

# app_config'ten grup sözlüğü döndüren okuyucu tipi.
DbConfigReader = Callable[[], Mapping[str, dict]]


class ConfigError(RuntimeError):
    """Konfigürasyon derleme/doğrulama hatası."""


@dataclass
class EffectiveConfig:
    """Zincir sonrası efektif config + kaynak haritası."""

    pipeline: dict[str, dict]
    sources: dict[str, dict]
    db: DbSettings
    log: LogSettings
    _typed: dict[str, BaseModel] = field(default_factory=dict)

    def group(self, name: str) -> BaseModel:
        """Doğrulanmış, tip güvenli grup nesnesi (ör. `EmbeddingConfig`)."""
        if name not in self._typed:
            raise KeyError(f"Bilinmeyen config grubu: {name!r}")
        return self._typed[name]

    def value(self, group: str, field_name: str) -> Any:
        return self.pipeline[group][field_name]

    def source_of(self, group: str, field_name: str) -> str:
        return self.sources[group][field_name]


def _collect_env_overrides(environ: Mapping[str, str]) -> dict[str, dict]:
    """`RAGINTEL_<GRUP>_<ALAN>` ortam değişkenlerini grup sözlüğüne çevirir.

    Adlandırma 7d ev-stiliyle uyumlu (tek alt çizgi). Yalnızca bilinen pipeline
    grupları dikkate alınır. Değer önce JSON olarak çözülmeye çalışılır
    (liste/sayı/bool), başarısızsa ham string bırakılır; tip zorlaması nihai
    doğrulamada pydantic tarafından yapılır.
    """
    out: dict[str, dict] = {}
    for group in PIPELINE_GROUPS:
        prefix = f"RAGINTEL_{group.upper()}_"
        for env_key, raw in environ.items():
            if not env_key.startswith(prefix):
                continue
            field_name = env_key[len(prefix):].lower()
            if not field_name:
                continue
            out.setdefault(group, {})[field_name] = _parse_env_value(raw)
    return out


def _parse_env_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def _validate_group(name: str, values: dict) -> dict:
    """Bilinen grubu pydantic modeliyle doğrular/koersiyona uğratır."""
    model = GROUP_MODELS[name]
    try:
        obj = model(**values)
    except ValidationError as exc:
        raise ConfigError(
            f"'{name}' config grubu geçersiz: {exc.errors()}"
        ) from exc
    return obj.model_dump()


def load_config(
    *,
    db_reader: DbConfigReader | None = None,
    environ: Mapping[str, str] | None = None,
) -> EffectiveConfig:
    """Efektif konfigürasyonu derler.

    Args:
        db_reader: app_config'ten grup sözlüğü döndüren okuyucu. `None` ise DB
            katmanı atlanır (yalnızca ENV > varsayılan). Testlerde sahte okuyucu
            enjekte edilir.
        environ: ortam değişkenleri kaynağı (varsayılan `os.environ`).
    """
    environ = os.environ if environ is None else environ

    defaults = default_pipeline_config()
    env_layer = _collect_env_overrides(environ)

    db_layer: dict[str, dict] = {}
    if db_reader is not None:
        db_raw = db_reader() or {}
        # app_config'teki TÜM grupları yüzeye çıkar (ör. ADR-011 'quality').
        # Bilinen pipeline grupları tiplenir; bilinmeyenler ham dict olarak geçer
        # ve `config show`'da kaynağıyla görünür (config-first, ileri-uyumlu).
        db_layer = {k: dict(v) for k, v in db_raw.items()}

    merged, sources = merge_with_sources(
        [("default", defaults), ("env", env_layer), ("db", db_layer)]
    )

    # Tip doğrulama + koersiyon (yalnızca bilinen pipeline grupları).
    typed: dict[str, BaseModel] = {}
    for name in PIPELINE_GROUPS:
        merged[name] = _validate_group(name, merged.get(name, {}))
        typed[name] = GROUP_MODELS[name](**merged[name])

    return EffectiveConfig(
        pipeline=merged,
        sources=sources,
        db=DbSettings(),
        log=LogSettings(),
        _typed=typed,
    )

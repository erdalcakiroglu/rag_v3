"""M-4 config migrasyonu: her davranışsal grubun TÜM yaprakları app_config'te olsun.

- Mevcut DB değerleri KAZANIR (ezilmez); yalnızca eksik yapraklar eklenir.
- Modelde artık olmayan ALANLAR düşer (ör. embedding.normalize — yalan alan).
- KRİTİK: yalnızca pydantic ALT-MODEL alanlarına özyinelenir. `prompts.agent_system`
  gibi serbest-biçim `dict[str, str]` alanları YAPRAK sayılır — aksi halde DB'de
  versiyonlanmış prompt'lar (v1/v2/v3) silinirdi.
- --apply verilmezse yalnızca farkı raporlar (dry-run).
"""
import json
import sys

import psycopg
from pydantic import BaseModel

from ragintel.config.settings import DbSettings, GROUP_MODELS, PIPELINE_GROUPS


def _is_submodel(model_cls, field: str) -> bool:
    ann = model_cls.model_fields[field].annotation
    return isinstance(ann, type) and issubclass(ann, BaseModel)


def deep_merge(model_cls, defaults: dict, existing: dict) -> dict:
    """defaults tabanlı; existing kazanır. Modelde olmayan ALANLAR düşer."""
    out = {}
    for k, dv in defaults.items():
        ev = existing.get(k, ...)
        if isinstance(dv, dict) and _is_submodel(model_cls, k):
            sub = model_cls.model_fields[k].annotation
            out[k] = deep_merge(sub, dv, ev if isinstance(ev, dict) else {})
        else:
            out[k] = dv if ev is ... else ev   # serbest dict/list/skaler → YAPRAK
    return out


def leaves(d, prefix=""):
    for k, v in d.items():
        p = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            yield from leaves(v, p)
        else:
            yield p


def main(apply: bool):
    with psycopg.connect(DbSettings().conninfo()) as c:
        cur = c.cursor()
        rows = dict(cur.execute("SELECT config_key, config_value FROM app_config;").fetchall())
        for g in PIPELINE_GROUPS:
            model = GROUP_MODELS[g]
            defaults = model().model_dump()
            existing = rows.get(g) or {}
            merged = deep_merge(model, defaults, existing)
            added = sorted(set(leaves(merged)) - set(leaves(existing)))
            dropped = sorted(set(leaves(existing)) - set(leaves(merged)))
            status = "YENI" if g not in rows else ("MERGE" if (added or dropped) else "GUNCEL")
            print(f"{g:12s} {status:6s} eklenen={added or '-'} dusen={dropped or '-'}")
            if apply:
                cur.execute(
                    "INSERT INTO app_config (config_key, config_value, description, updated_by) "
                    "VALUES (%s, %s::jsonb, %s, 'm4-migration') "
                    "ON CONFLICT (config_key) DO UPDATE SET config_value = EXCLUDED.config_value, "
                    "updated_by = 'm4-migration', updated_at = now();",
                    (g, json.dumps(merged, ensure_ascii=False), f"{g} (M-4 tam alan seti)"),
                )
        if apply:
            c.commit()
            print("\nUYGULANDI")
        else:
            print("\nDRY-RUN (uygulanmadi)")


if __name__ == "__main__":
    main("--apply" in sys.argv)

"""M-15 kol-1 — agent sistem prompt'unu v4'e AL / v1'e DÖN (app_config.prompts).

`load_system_prompt` aktif sürümün GÖVDESİNİ DB'den okur (prompts.py:141-152) ve
`PromptsConfig._active_version_must_exist` (settings.py:1104-1113) tanımsız bir aktif
sürümü REDDEDER. Bu yüzden gövde ile aktif seçim BİRLİKTE yazılır. Gövde koddan
(`PROMPT_VERSIONS`) alınır → DB ile git arasında sapma olamaz.

Geri alma bir revert değil, bir ANAHTAR: `--target v1` eski davranışa döner; v4
gövdesi DB'de kalır (sürüm silinmez). Karne gerilerse tek komutla geri alınır.

DİKKAT: config konteyner AÇILIŞINDA dondurulur → yazdıktan sonra RESTART şart.

KULLANIM (H200'de):
  docker exec -i ragintel-api python -u scripts/m15_prompt_v4_switch.py --target v4
  docker exec -i ragintel-api python -u scripts/m15_prompt_v4_switch.py --target v4 --apply
  docker exec -i ragintel-api python -u scripts/m15_prompt_v4_switch.py --target v1 --apply
"""

from __future__ import annotations

import argparse
import json

import psycopg

from ragintel.agents.prompts import PROMPT_VERSIONS
from ragintel.config.settings import DbSettings


def main(target: str, apply: bool) -> None:
    if target not in PROMPT_VERSIONS:
        raise SystemExit(f"--target '{target}' kodda tanımlı değil. Mevcut: {sorted(PROMPT_VERSIONS)}")

    with psycopg.connect(DbSettings().conninfo()) as c:
        cur = c.cursor()
        row = cur.execute("SELECT config_value FROM app_config WHERE config_key='prompts';").fetchone()
        current = (row[0] if row else None) or {}
        if isinstance(current, str):
            current = json.loads(current)

        versions = dict(current.get("agent_system") or {})
        before = str(current.get("agent_system_active") or "")

        # Kod ↔ DB sapmasını kapat: hedef sürümün gövdesi HER ZAMAN koddan yazılır.
        versions[target] = PROMPT_VERSIONS[target]
        merged = dict(current, agent_system=versions, agent_system_active=target)

        v1, v4 = PROMPT_VERSIONS["v1"], PROMPT_VERSIONS["v4"]
        print(f"aktif sürüm : {before or '(boş → kod varsayılanı = v1)'}  →  {target}")
        print(f"DB sürümleri: {sorted(versions)}")
        print(f"hedef gövde : {len(PROMPT_VERSIONS[target])} krk   (v4, v1'den +{len(v4) - len(v1)} krk)")

        if not apply:
            print("\nDRY-RUN (uygulanmadi) — yazmak icin --apply ekle.")
            return

        cur.execute(
            "INSERT INTO app_config (config_key, config_value, description, updated_by) "
            "VALUES ('prompts', %s::jsonb, %s, 'm15-kol1') "
            "ON CONFLICT (config_key) DO UPDATE SET config_value = EXCLUDED.config_value, "
            "updated_by = 'm15-kol1', updated_at = now();",
            (json.dumps(merged, ensure_ascii=False), f"agent sistem prompt sürümleri (aktif: {target})"),
        )
        c.commit()
        print(f"\nUYGULANDI (aktif={target}). Config açılışta dondurulur → RESTART gerekli.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="v4", help="aktif edilecek prompt sürümü (v1 = geri alma)")
    ap.add_argument("--apply", action="store_true", help="yaz (yoksa yalnız gösterir)")
    a = ap.parse_args()
    main(a.target, a.apply)

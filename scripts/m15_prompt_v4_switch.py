"""M-15 kol-1 — agent sistem prompt'unu v4'e AL / v1'e DÖN (app_config.prompts).

`load_system_prompt` aktif sürümün GÖVDESİNİ DB'den okur (prompts.py:141-152) ve
`PromptsConfig._active_version_must_exist` (settings.py:1104-1113) tanımsız bir aktif
sürümü REDDEDER. Bu yüzden gövde ile aktif seçim BİRLİKTE yazılır. Gövde koddan
(`PROMPT_VERSIONS`) alınır → DB ile git arasında sapma olamaz.

Geri alma bir revert değil, bir ANAHTAR: `--target v2` (= `V4_BASE`, CANLI sürüm) eski
davranışa döner; v4 gövdesi DB'de kalır (sürüm silinmez). Karne gerilerse tek komutla
geri alınır. GERİ ALMA HEDEFİ v1 DEĞİLDİR — v1'e dönmek v2'nin GROUNDING+REDDETME
bloklarını da düşürür.

TEK DEĞİŞKEN KORUMASI: v4 hedeflenirken DB'deki hâlihazırda AKTİF gövde ile v4'ün
türetildiği taban (`PROMPT_VERSIONS[V4_BASE]`) karşılaştırılır. Uyuşmuyorsa yazma
REDDEDİLİR — aksi hâlde karnedeki fark "tek satır"a değil, taban kaymasına ait olurdu.

DİKKAT: config konteyner AÇILIŞINDA dondurulur → yazdıktan sonra RESTART şart.

KULLANIM (H200'de): `scripts/` konteyner imajında YOKTUR — betik stdin'den beslenir,
argümanlar `-`'den sonra gelir (diğer M-15 betikleriyle aynı desen):
  docker exec -i ragintel-api python -u - --target v4         < scripts/m15_prompt_v4_switch.py
  docker exec -i ragintel-api python -u - --target v4 --apply < scripts/m15_prompt_v4_switch.py
  docker exec -i ragintel-api python -u - --target v2 --apply < scripts/m15_prompt_v4_switch.py
"""

from __future__ import annotations

import argparse
import json

import psycopg

from ragintel.agents.prompts import PROMPT_VERSIONS, V4_BASE
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
        live_body = str(versions.get(before, "") or "").strip()

        base, v4 = PROMPT_VERSIONS[V4_BASE], PROMPT_VERSIONS["v4"]
        print(f"aktif sürüm : {before or '(boş → kod varsayılanı = v1)'}  →  {target}")
        print(f"DB sürümleri: {sorted(versions)}")
        print(f"hedef gövde : {len(PROMPT_VERSIONS[target])} krk   "
              f"(v4 = {V4_BASE} + {len(v4) - len(base)} krk tek satır)")

        # TEK DEĞİŞKEN KORUMASI: v4 yalnızca CANLI gövde v4'ün tabanıyla aynıysa anlamlıdır.
        if target == "v4" and live_body != base.strip():
            raise SystemExit(
                f"\nREDDEDİLDİ: canlı aktif sürüm '{before or '(boş)'}' ile v4'ün tabanı "
                f"'{V4_BASE}' AYNI DEĞİL.\nv4'e geçmek tek satır eklemekle kalmaz, tabanı da "
                f"kaydırır → karnedeki fark 'tek satır'a ait olmaz.\nÖnce prompts.py'de "
                f"V4_BASE'i canlı sürüme çek ve v4'ü ondan türet."
            )

        # Kod ↔ DB sapmasını kapat: hedef sürümün gövdesi HER ZAMAN koddan yazılır.
        versions[target] = PROMPT_VERSIONS[target]
        merged = dict(current, agent_system=versions, agent_system_active=target)

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
    ap.add_argument("--target", default="v4",
                    help=f"aktif edilecek prompt sürümü ({V4_BASE} = geri alma; v1 DEĞİL)")
    ap.add_argument("--apply", action="store_true", help="yaz (yoksa yalnız gösterir)")
    a = ap.parse_args()
    main(a.target, a.apply)

"""M-15 kol-1 — agent sistem prompt'unu v4'e AL / tabana DÖN (app_config.prompts).

Kol-1 KOD DEĞİŞİKLİĞİ DEĞİLDİR: `load_system_prompt` aktif sürümün gövdesini DB'den
okur (prompts.py), dolayısıyla v4 saf bir config değişikliğidir — imaj yeniden
kurulmadan uygulanır. Bu yüzden betik `ragintel.agents.prompts`'a BAĞIMLI DEĞİLDİR
(konteynerdeki /app/ragintel imajdan gelir ve host'taki git'ten eski olabilir).

TEK DEĞİŞKEN GARANTİSİ: v4 gövdesi, DB'de HÂLİHAZIRDA AKTİF olan gövdeden türetilir —
tek bir satır eklenir, kalan her karakter aynen korunur. Koddan türetmek, imaj/DB
sapması hâlinde sessizce tabanı kaydırabilirdi; canlı gövdeden türetmek kaydıramaz.
Kod tarafı (prompts.py `SYSTEM_PROMPT_V4`) importlanabiliyorsa AYRICA karşılaştırılır.

Geri alma bir revert değil, bir ANAHTAR: `--target v2` (taban) eski davranışa döner;
v4 gövdesi DB'de kalır. GERİ ALMA HEDEFİ v1 DEĞİLDİR — v1'e dönmek v2'nin
GROUNDING (KATI) + REDDETME bloklarını da düşürür.

DİKKAT: config konteyner AÇILIŞINDA dondurulur → uzun süre ayakta duran API için
RESTART gerekir. Ölçüm yolları (`ragintel.eval`, anatomi betiği) yeni process açtığı
için DB'yi taze okur; onlar için restart şart değildir.

KULLANIM (H200'de): `scripts/` konteyner imajında YOKTUR — betik stdin'den beslenir,
argümanlar `-`'den sonra gelir:
  docker exec -i ragintel-api python -u - --target v4         < scripts/m15_prompt_v4_switch.py
  docker exec -i ragintel-api python -u - --target v4 --apply < scripts/m15_prompt_v4_switch.py
  docker exec -i ragintel-api python -u - --target v2 --apply < scripts/m15_prompt_v4_switch.py
"""

from __future__ import annotations

import argparse
import json

import psycopg

from ragintel.config.settings import DbSettings

# v4'ü tanımlayan TEK değişiklik. prompts.py'deki `_V4_ANCHOR`/`_V4_RULE` ile birebir
# aynı olmalı; tests/test_faz_m15_prompt_v4.py bu betiği okuyup eşitliği kilitler.
V4_ANCHOR = "- `quote` KOPYALA-YAPIŞTIR olmalı:"
V4_RULE = (
    "- Tool çağıracaksan YANINDA düz metin YAZMA: o turda yalnızca tool çağrısını üret; "
    "açıklama, özet ya da taslak cevap yazma (bu metin okunmaz, yalnızca gecikme ekler).\n"
)
EXPECTED_BASE = "v2"          # beklenen taban; farklıysa uyarır, körlemesine yazmaz


def derive_v4(base_body: str) -> str:
    """Canlı gövdeye TEK satır ekler. Çapa yoksa ya da sonuç değişmediyse AÇIK hata."""
    if V4_ANCHOR not in base_body:
        raise SystemExit(f"REDDEDİLDİ: çapa canlı gövdede yok → {V4_ANCHOR!r}\n"
                         "Taban prompt değişmiş; kural nereye ekleneceği belirsiz.")
    out = base_body.replace(V4_ANCHOR, V4_RULE + V4_ANCHOR, 1)
    if out == base_body or len(out.splitlines()) != len(base_body.splitlines()) + 1:
        raise SystemExit("REDDEDİLDİ: türetme tam olarak 1 satır eklemedi.")
    return out


def main(target: str, apply: bool) -> None:
    with psycopg.connect(DbSettings().conninfo()) as c:
        cur = c.cursor()
        row = cur.execute("SELECT config_value FROM app_config WHERE config_key='prompts';").fetchone()
        current = (row[0] if row else None) or {}
        if isinstance(current, str):
            current = json.loads(current)

        versions = dict(current.get("agent_system") or {})
        active = str(current.get("agent_system_active") or "")
        print(f"DB sürümleri : {sorted(versions)}")
        print(f"aktif sürüm  : {active or '(boş → kod varsayılanı)'}  →  {target}")

        if target == "v4":
            if active == "v4":
                raise SystemExit("Zaten v4 aktif. Taban v4'ün kendisi olurdu → yazılmadı.")
            base_body = str(versions.get(active, "") or "")
            if not base_body:
                raise SystemExit(f"REDDEDİLDİ: aktif sürüm '{active}' gövdesi DB'de yok.")
            if active != EXPECTED_BASE:
                print(f"  UYARI: taban '{active}', beklenen '{EXPECTED_BASE}'. "
                      "Türetme yine CANLI gövdeden yapılır (tek değişken korunur).")
            body = derive_v4(base_body)
            print(f"türetildi    : v4 = '{active}' + 1 satır  ({len(base_body)} → {len(body)} krk)")

            # Kod tarafı erişilebiliyorsa çapraz kontrol (imaj eskiyse sessizce atlanır).
            try:
                from ragintel.agents.prompts import SYSTEM_PROMPT_V4
                same = SYSTEM_PROMPT_V4.strip() == body.strip()
                print(f"kod/DB kontrol: prompts.SYSTEM_PROMPT_V4 ile {'AYNI' if same else 'FARKLI (imaj eski olabilir)'}")
            except ImportError:
                print("kod/DB kontrol: prompts.SYSTEM_PROMPT_V4 yok (imaj eski) — atlandı")

            versions["v4"] = body
        elif target not in versions:
            raise SystemExit(f"REDDEDİLDİ: '{target}' DB'de tanımlı değil. Mevcut: {sorted(versions)}")
        else:
            print(f"geri alma    : gövde yazılmaz, yalnız aktif seçim '{target}' olur")

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
        print(f"\nUYGULANDI (aktif={target}). Ölçüm process'leri DB'yi taze okur; "
              "ayakta duran API için restart gerekir.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="v4",
                    help=f"aktif edilecek sürüm (geri alma: {EXPECTED_BASE} — v1 DEĞİL)")
    ap.add_argument("--apply", action="store_true", help="yaz (yoksa yalnız gösterir)")
    a = ap.parse_args()
    main(a.target, a.apply)

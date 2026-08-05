"""M-18 — agent sistem prompt'unu v5'e AL (yapı/madde nudge) / tabana DÖN (app_config.prompts).

Kol KOD DEĞİŞİKLİĞİ DEĞİLDİR: `load_system_prompt` aktif sürümün gövdesini DB'den
okur (prompts.py), dolayısıyla v5 saf bir config değişikliğidir — imaj yeniden
kurulmadan uygulanır. Bu yüzden betik `ragintel.agents.prompts`'a BAĞIMLI DEĞİLDİR
(konteynerdeki /app/ragintel imajdan gelir ve host'taki git'ten eski olabilir).

TEK DEĞİŞKEN GARANTİSİ: v5 gövdesi, DB'de HÂLİHAZIRDA AKTİF olan gövdeden türetilir —
TEK bir satır (`V5_TARGET` → `V5_RULE`) DEĞİŞTİRİLİR, kalan her karakter aynen korunur.
v4'ten fark: v4 bir satır EKLİYORDU (satır sayısı +1); v5 bir satırı DEĞİŞTİRİR (satır
sayısı KORUNUR). Koddan türetmek imaj/DB sapmasında sessizce tabanı kaydırabilirdi;
canlı gövdeden türetmek kaydıramaz. Kod tarafı (prompts.py) importlanabiliyorsa AYRICA
karşılaştırılır.

Geri alma bir revert değil, bir ANAHTAR: `--target v2` (taban) eski davranışa döner;
v5 gövdesi DB'de kalır. GERİ ALMA HEDEFİ v1 DEĞİLDİR — v1'e dönmek v2'nin
GROUNDING (KATI) + REDDETME bloklarını da düşürür.

DAVRANIŞ değişikliği: v5 yapı üretimini açar → k=3 karne + honesty HARD gate (≥12/15)
ZORUNLU (bkz. docs/Brief_M18_Prompt_Yapi_Nudge.md §3-§4). Bu betik yalnız SEED/ANAHTAR;
karneyi geçmeden `--target v5 --apply` ile canlıya alma kararı VERİLMEZ.

DİKKAT: config konteyner AÇILIŞINDA dondurulur → uzun süre ayakta duran API için
RESTART gerekir. Ölçüm yolları (`ragintel.eval`) yeni process açtığı için DB'yi taze
okur; onlar için restart şart değildir (karne canlıyı etkilemeden v5'i ölçer).

KULLANIM (H200'de): `scripts/` konteyner imajında YOKTUR — betik stdin'den beslenir,
argümanlar `-`'den sonra gelir:
  docker exec -i ragintel-api python -u - --target v5         < scripts/m18_prompt_v5_switch.py
  docker exec -i ragintel-api python -u - --target v5 --apply < scripts/m18_prompt_v5_switch.py
  docker exec -i ragintel-api python -u - --target v2 --apply < scripts/m18_prompt_v5_switch.py
"""

from __future__ import annotations

import argparse
import json

import psycopg

from ragintel.config.settings import DbSettings

# v5'i tanımlayan TEK değişiklik. prompts.py'deki `_V5_TARGET`/`_V5_RULE` ile birebir
# aynı olmalı; tests/test_faz_m18_prompt_v5.py bu betiği okuyup eşitliği kilitler.
V5_TARGET = (
    "- KISA ve ÖZ yaz (3-6 cümle, düz paragraf). Başlık/madde imi KULLANMA. Her cümle bir "
    "citation ile desteklenmeli; destekleyemeyeceğin cümleyi YAZMA."
)
V5_RULE = (
    "- Cevabı OKUNUR biçimle: mantıklı yerlerde paragraflara böl; birden çok ayrı olgu "
    "sıralıyorsan madde imleri (`- `) kullan — AMA her madde TEK BAŞINA bir citation'a bağlı "
    "bir iddia olmalı. ALINTISIZ hiçbir satır yazma: başlık, liste-girişi ('şunlar önemlidir:') "
    "veya dolgu/geçiş cümlesi EKLEME. Kısa ve öz kal; destekleyemeyeceğin cümleyi/maddeyi YAZMA."
)
EXPECTED_BASE = "v2"          # beklenen taban; farklıysa uyarır, körlemesine yazmaz


def derive_v5(base_body: str) -> str:
    """Canlı gövdede TEK satırı DEĞİŞTİRİR. Hedef yoksa ya da sonuç değişmediyse AÇIK hata."""
    if V5_TARGET not in base_body:
        raise SystemExit(f"REDDEDİLDİ: hedef satır canlı gövdede yok → {V5_TARGET!r}\n"
                         "Taban prompt değişmiş; hangi satırın değişeceği belirsiz.")
    out = base_body.replace(V5_TARGET, V5_RULE, 1)
    if out == base_body or len(out.splitlines()) != len(base_body.splitlines()):
        raise SystemExit("REDDEDİLDİ: türetme tam olarak 1 satırı değiştirmedi (satır sayısı korunmalı).")
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

        if target == "v5":
            if active == "v5":
                raise SystemExit("Zaten v5 aktif. Taban v5'in kendisi olurdu → yazılmadı.")
            base_body = str(versions.get(active, "") or "")
            if not base_body:
                raise SystemExit(f"REDDEDİLDİ: aktif sürüm '{active}' gövdesi DB'de yok.")
            if active != EXPECTED_BASE:
                print(f"  UYARI: taban '{active}', beklenen '{EXPECTED_BASE}'. "
                      "Türetme yine CANLI gövdeden yapılır (tek değişken korunur).")
            body = derive_v5(base_body)
            print(f"türetildi    : v5 = '{active}' gövdesi, 1 satır değişti  "
                  f"({len(base_body)} → {len(body)} krk, satır sayısı sabit)")

            # Kod tarafı erişilebiliyorsa çapraz kontrol (imaj eskiyse sessizce atlanır).
            try:
                from ragintel.agents.prompts import SYSTEM_PROMPT_V5
                same = SYSTEM_PROMPT_V5.strip() == body.strip()
                print(f"kod/DB kontrol: prompts.SYSTEM_PROMPT_V5 ile {'AYNI' if same else 'FARKLI (imaj eski olabilir)'}")
            except ImportError:
                print("kod/DB kontrol: prompts.SYSTEM_PROMPT_V5 yok (imaj eski) — atlandı")

            versions["v5"] = body
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
            "VALUES ('prompts', %s::jsonb, %s, 'm18-v5') "
            "ON CONFLICT (config_key) DO UPDATE SET config_value = EXCLUDED.config_value, "
            "updated_by = 'm18-v5', updated_at = now();",
            (json.dumps(merged, ensure_ascii=False), f"agent sistem prompt sürümleri (aktif: {target})"),
        )
        c.commit()
        print(f"\nUYGULANDI (aktif={target}). Ölçüm process'leri DB'yi taze okur; "
              "ayakta duran API için restart gerekir.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="v5",
                    help=f"aktif edilecek sürüm (geri alma: {EXPECTED_BASE} — v1 DEĞİL)")
    ap.add_argument("--apply", action="store_true", help="yaz (yoksa yalnız gösterir)")
    a = ap.parse_args()
    main(a.target, a.apply)

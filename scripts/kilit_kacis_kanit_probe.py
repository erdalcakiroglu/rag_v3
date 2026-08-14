"""probe: zorlanmış nihai tur KİLİDİNİ bilerek tetikle, ÜRETİM kaçış yolunu ölç.

NEDEN VAR
    Kilit ölçüldü ve kaçış yolu üretime alındı (5e94f57), ama kaçışın CANLIDA
    ateşlendiği hiç GÖRÜLMEDİ: M-17 kolunu kapatan koşumda `forced_final_wedge_escape`
    sayacı 0 kaldı ve o koşumun logu üzerine yazıldı. "Kilit oluşmadı" ile "kaçış
    çalışmadı" bu sayıdan AYRILAMAZ. Elde deterministik bir tetikleyici varken
    (`var/kilit_payload.json` — dört bağımsız denemede dördü timeout) bunu varsayım
    bırakmak gereksiz: aynı yükü ÜRETİM fonksiyonundan geçirip ölçeriz.

NE ÖLÇER
    `ragintel.agents.nodes.agent._forced_final_complete` — canlı API'de her sorunun
    sonunda koşan fonksiyonun TA KENDİSİ. Sahte gateway yok, taklit yok:
      1. çağrı: tools=1 (`final_only_schemas`) + `max_retries=0` → kilit BEKLENİR
      2. taşıma hatası → kaçış: aynı mesajlar, tools=5 (`llm_tool_schemas`) → yanıt

GEÇME ÖLÇÜTÜ (üçü birden)
    - 1. çağrı taşıma hatasıyla düştü (kilit gerçekten oluştu)
    - `budget["forced_final_escaped"] is True` (kaçış ateşlendi)
    - 2. çağrı yanıt döndürdü (tool_call ya da metin) — yani ajan cevapsız kalmadı

    Kilit bu koşumda oluşmazsa probe bunu AÇIKÇA söyler ve BAŞARI SAYMAZ: kaçış yolu
    denenmemiş olur. Kilit bağlama duyarlıdır, yokluğu kanıt değildir.

MALİYET / GÜVENLİK
    DB'ye, config'e, golden'a YAZMAZ. Ama GERÇEK LLM çağrısı yapar ve kilidi bilerek
    tetikler: Ollama seri çalıştığı için ~`--timeout` saniye boyunca uç meşgul olur.
    CANLI ÖLÇÜM/karne koşarken ÇALIŞTIRMAYIN ([[kapasite-seri-lock]]).
    Yük dosyası korpus metni taşır → `var/` altında kalır, repoya girmez.

KULLANIM
    python -u scripts/ollama_kilit_probe.py --golden v1-bddk --kayit u12 --yalniz-yakala
    python -u scripts/kilit_kacis_kanit_probe.py --payload var/kilit_payload.json --timeout 90
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def _gateway_yukten(hedef: dict, timeout: float):
    """Yakalanan isteğin KENDİ parametreleriyle gerçek gateway kur (DB gerekmez).

    Model/sıcaklık/reasoning_effort yükten okunur: kilidi doğuran istek hangi zeminde
    kurulduysa kaçış da o zeminde ölçülmeli. DB'den yeniden türetmek zemini kaydırırdı.
    """
    from ragintel.config.settings import LiteLLMSettings
    from ragintel.llm.gateway import LiteLLMGateway

    ayar = LiteLLMSettings(request_timeout=timeout)
    tam_ad = str(hedef.get("model") or "")
    model = tam_ad.split("/", 1)[1] if tam_ad.startswith(f"{ayar.provider}/") else tam_ad
    effort = str((hedef.get("extra_body") or {}).get("reasoning_effort") or "default")
    return LiteLLMGateway(model=model, settings=ayar, reasoning_effort=effort,
                          temperature=float(hedef.get("temperature", 0.0))), model, effort


def _izleyen(gateway, kayit: list[dict]):
    """gateway.complete'i sarmala: her çağrının şema sayısı/süresi/sonucu kaydedilsin.
    Fonksiyonun KENDİSİ değişmez — yalnız gözlem eklenir."""
    orij = gateway.complete

    def sarmal(*, messages, tools, max_retries=None):
        t0 = time.perf_counter()
        try:
            resp = orij(messages=messages, tools=tools, max_retries=max_retries)
            kayit.append({"tools": len(tools or []), "max_retries": max_retries,
                          "sonuc": "OK", "sn": round(time.perf_counter() - t0, 1),
                          "tool_calls": len(getattr(resp, "tool_calls", None) or []),
                          "content_chars": len(getattr(resp, "content", None) or "")})
            return resp
        except Exception as exc:
            kayit.append({"tools": len(tools or []), "max_retries": max_retries,
                          "sonuc": type(exc).__name__, "sn": round(time.perf_counter() - t0, 1),
                          "hata": str(exc)[:160]})
            raise

    gateway.complete = sarmal
    return gateway


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--payload", default=os.path.join("var", "kilit_payload.json"),
                   help="ollama_kilit_probe.py'nin yakaladığı kilitlenen istek")
    p.add_argument("--timeout", type=float, default=90.0,
                   help="İstek başına saniye (gateway request_timeout)")
    args = p.parse_args(argv)

    if not os.path.exists(args.payload):
        print(f"HATA: yük yok: {args.payload}\n"
              f"Önce yakalayın: python -u scripts/ollama_kilit_probe.py --golden v1-bddk "
              f"--kayit u12 --yalniz-yakala", file=sys.stderr)
        return 2
    with open(args.payload, encoding="utf-8") as fh:
        hedef = json.load(fh)

    from ragintel.agents.nodes.agent import _forced_final_complete
    from ragintel.agents.tools import ToolRegistry

    gateway, model, effort = _gateway_yukten(hedef, args.timeout)
    registry = ToolRegistry(None)          # şemalar statik; tool YÜRÜTÜLMEZ, service gerekmez
    mesajlar = hedef["messages"]
    print(f"model={model} · reasoning_effort={effort} · timeout={gateway.settings.request_timeout:.0f} s\n"
          f"yük={args.payload} · messages={len(mesajlar)} · "
          f"prompt_chars={sum(len(str(m.get('content') or '')) for m in mesajlar)}\n"
          f"zorlanmış tur şeması={len(registry.final_only_schemas())} · "
          f"kaçış şeması={len(registry.llm_tool_schemas())}\n")

    kayit: list[dict] = []
    budget: dict = {}
    _izleyen(gateway, kayit)

    print("[1/1] _forced_final_complete koşuyor (kilit BİLEREK tetikleniyor)…")
    t0 = time.perf_counter()
    hata = None
    try:
        resp = _forced_final_complete(gateway, registry, mesajlar, budget)
    except Exception as exc:                # kaçış da düştüyse: gerçek başarısızlık
        resp, hata = None, exc
    toplam = round(time.perf_counter() - t0, 1)

    for i, c in enumerate(kayit, 1):
        ek = (f" tool_calls={c['tool_calls']} content={c['content_chars']} char"
              if c["sonuc"] == "OK" else f" — {c.get('hata', '')}")
        print(f"  çağrı-{i}  tools={c['tools']} max_retries={c['max_retries']}  "
              f"{c['sonuc']:<20} {c['sn']:>6.1f} s{ek}")
    print(f"  toplam {toplam} s · forced_final_escaped={budget.get('forced_final_escaped', False)}")

    kilitlendi = bool(kayit) and kayit[0]["sonuc"] != "OK"
    kacti = budget.get("forced_final_escaped") is True
    kurtardi = hata is None and resp is not None and len(kayit) > 1 and kayit[-1]["sonuc"] == "OK"

    print("\n--- OKUMA ---")
    if not kilitlendi:
        print("KİLİT OLUŞMADI: tek şemalı çağrı bu koşumda GEÇTİ. Kaçış yolu DENENMEDİ — "
              "bu koşum kaçış hakkında hiçbir şey söylemez (kilit bağlama duyarlı, "
              "yokluğu kanıt değil). Yükü yeniden yakalayıp tekrarlayın.")
        return 3
    if kacti and kurtardi:
        print(f"GEÇTİ: kilit {kayit[0]['sn']:.0f} s'de düştü, kaçış ateşlendi, tam listeyle "
              f"{kayit[-1]['sn']:.1f} s'de yanıt geldi. Üretimde bu satır, 12 dakikalık "
              f"servis durmasının yerini {toplam:.0f} saniyeye indiriyor.")
        return 0
    print(f"DÜŞTÜ: kilit oluştu ama kurtarma tamamlanmadı "
          f"(escaped={kacti}, hata={type(hata).__name__ if hata else None}). "
          f"Kaçış yolu üretimde İŞE YARAMIYOR — düzeltme yeniden açılmalı.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

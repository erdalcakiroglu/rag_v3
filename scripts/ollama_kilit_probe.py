"""probe: kilitlenen LLM çağrısını YAKALA, sonra dört kolda oynat (SALT-OKUNUR).

ÖLÇÜLEN OLGU (2026-08-13, M-17 dürüstlük kolu)
    `gs-bddk-u12` satırı ajan bütçesini bitirip ZORLANMIŞ nihai çağrıya geldiğinde
    Ollama kilitleniyor: istek kabul ediliyor, GPU boşta duruyor, Ollama kendi
    journal'ına tek satır yazmıyor ve istemci tam timeout'ta düşüyor. `llm_call_start`
    damgası (353a899) kilidin şeklini verdi:

        20:41:02  messages=5  prompt_chars=2378  tools=5  → 2 sn'de döndü
        20:41:04  messages=7  prompt_chars=2548  tools=5  → 2 sn'de döndü
        20:41:05  messages=8  prompt_chars=3130  tools=1  → 120 s TIMEOUT
        20:43:08  messages=8  prompt_chars=3130  tools=1  → 120 s TIMEOUT (aynı istem)

    Yani istem KÜÇÜK (~800 token) ve kilitlenen çağrı `final_only_schemas()` —
    bütçe bitince yalnız `submit_answer` sunulan tur. "İstem çok uzun", "üretim
    16000 token'a dayanıyor", "başka istemciyle çekişme" hipotezleri bu damgayla
    düştü. Geriye iki aday kaldı ve ikisi de bu probe ile ayrılır:
      (1) tek-tool'lu kısıtlı çözümleme (grammar) uçta kilitleniyor,
      (2) bu KONUŞMAYA özgü bir şey (u12'nin bağlamı) modeli kilitliyor.

NE YAPAR
    Adım-1  u12'yi ajandan geçirir, ilk `tools==1` çağrısında isteği YAKALAR ve
            çağrıyı YAPMADAN durur (kilit tetiklenmez). Tam tool listeli bir istek
            de kıyas için yakalanır.
    Adım-2  Yakalanan isteği kollarda oynatır (her kol `--timeout` saniyede kesilir):
              A  aynen (2 kez)          → kilit TEKRARLANABİLİR mi?
              B  tools = tam liste (5)  → tek-tool mu kilitliyor?
              C  reasoning_effort YOK   → düşünme kısma knob'u mu?
              D  tools YOK (serbest)    → tool şeması mı?
              E  tools = 2 (submit + search) → tetikleyici TAM OLARAK "1" mi?

ÖLÇÜLDÜ (2026-08-13, ilk koşum): A1/A2 90 s TIMEOUT · B OK 3.3 s (tool_call üretti) ·
    C 90 s TIMEOUT · D OK 3.7 s. Yani istem masum, `reasoning_effort` masum; kilidi
    doğuran şey uca TEK tool şeması gönderilmesi. E kolu bunu keskinleştirir:
    tetikleyici "tam olarak 1" ise düzeltme "zorlanmış tura asla tek şema gönderme",
    değilse "nihai tur asılırsa tam listeyle bir kez tekrarla" (B o yolun çalıştığını
    gösterdi). Tek-tool TEK BAŞINA yetmiyor: diğer 5 kaydın 15 tekrarı da aynı turdan
    geçti ve geçti — kilit tek-tool grameri ile BU konuşmanın birleşiminde.

SALT-OKUNUR: DB'ye, config'e, golden'a YAZMAZ. Yakalanan istek korpus metni taşır →
varsayılan çıktı `var/` altına yazılır (repo dışı, hassas kabul edilen dizin).

KULLANIM
    python -u scripts/ollama_kilit_probe.py --golden v1-bddk --kayit u12 --timeout 90
    python -u scripts/ollama_kilit_probe.py --payload var/kilit_payload.json --kollar E,A
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time


class _Yakalandi(Exception):
    """Hedef çağrı yakalandı — ajanı burada durdur (çağrı YAPILMAZ)."""


def _kayitlar(db, golden: str) -> list[dict]:
    from ragintel.eval import repository as repo
    with db.connection() as conn:
        return [dict(r) for r in repo.list_golden_records(conn, golden)]


def _yakala(app, rec: dict) -> tuple[dict | None, dict | None]:
    """Ajanı koştur; ilk `tools==1` isteğini (kilitlenen) ve son tam-tool isteğini al."""
    import ragintel.llm.gateway as gwmod
    from ragintel.agents.graph import run_agent

    orij = gwmod.LiteLLMGateway._completion
    kutu: dict = {}

    def sarmal(self, kwargs):
        n = len(kwargs.get("tools") or [])
        if n == 1:
            kutu["hedef"] = copy.deepcopy(kwargs)
            raise _Yakalandi()                      # kilidi TETİKLEME — burada dur
        if n > 1:
            kutu["tam"] = copy.deepcopy(kwargs)
        return orij(self, kwargs)

    gwmod.LiteLLMGateway._completion = sarmal
    try:
        run_agent(app, {
            "query": rec["question"],
            "user_ctx": {"user_id": "probe", "tenant_id": "eval", "roles": ["eval"],
                         "allowed_doc_scopes": [rec["doc_scope"]]},
            "session_id": f"probe-{rec['id']}",
            "retrieved": [],
        })
    except Exception:
        pass                                         # _Yakalandi graf içinde sarılabilir
    finally:
        gwmod.LiteLLMGateway._completion = orij
    return kutu.get("hedef"), kutu.get("tam")


def _oynat(ad: str, kwargs: dict, timeout: float) -> dict:
    """Tek kol: litellm'e DOĞRUDAN gönder (ajan/graf yok), süreyi ve sonucu ölç."""
    import litellm

    k = dict(kwargs)
    k["timeout"] = timeout
    t0 = time.perf_counter()
    try:
        resp = litellm.completion(**k)
        sn = time.perf_counter() - t0
        msg = resp.choices[0].message
        usage = getattr(resp, "usage", None)
        return {"kol": ad, "sonuc": "OK", "sn": round(sn, 1),
                "tool_calls": len(getattr(msg, "tool_calls", None) or []),
                "content_chars": len(getattr(msg, "content", None) or ""),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0)}
    except Exception as exc:
        return {"kol": ad, "sonuc": type(exc).__name__, "sn": round(time.perf_counter() - t0, 1),
                "hata": str(exc)[:160]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--golden", default="v1-bddk")
    p.add_argument("--kayit", default="u12", help="Kayıt id parçası (ör. u12)")
    p.add_argument("--timeout", type=float, default=90.0, help="Kol başına saniye")
    p.add_argument("--cikti", default=os.path.join("var", "kilit_payload.json"),
                   help="Yakalanan istek buraya yazılır (korpus metni taşır — repoya GİRMEZ)")
    p.add_argument("--yalniz-yakala", action="store_true", help="Kolları koşma, yalnız isteği yakala")
    p.add_argument("--payload", default=None,
                   help="Kaydedilmiş isteği oynat (ajanı YENİDEN KOŞTURMAZ, DB'ye bağlanmaz)")
    p.add_argument("--kollar", default="A,B,C,D,E",
                   help="Koşulacak kollar (virgüllü, ör. 'E,A'). Sıra verilen sıradır.")
    args = p.parse_args(argv)

    tam = None
    if args.payload:
        # Tekrar-oynatma: yakalama adımı atlanır → tek kolu saniyeler içinde denersin.
        with open(args.payload, encoding="utf-8") as fh:
            hedef = json.load(fh)
        model = hedef.get("model", "?")
        print(f"model={model} · payload={args.payload} (yakalama ATLANDI)")
    else:
        from ragintel.eval.harness import build_eval_app

        db, _cfg, model, app = build_eval_app()
        try:
            adaylar = [r for r in _kayitlar(db, args.golden) if args.kayit in str(r["id"])]
            if not adaylar:
                print(f"HATA: '{args.golden}' setinde '{args.kayit}' geçen kayıt yok.", file=sys.stderr)
                return 2
            rec = adaylar[0]
            print(f"model={model} · kayit={rec['id']}\nsoru: {rec['question']}\n")

            print("[1/2] kilitlenen istek yakalanıyor (çağrı YAPILMAZ)…")
            hedef, tam = _yakala(app, rec)
        finally:
            db.close()

        if hedef is None:
            print("YAKALANAMADI: ajan `tools==1` turuna hiç gelmedi (bütçe bitmeden yanıtladı). "
                  "Kilit bu koşumda TETİKLENMEDİ — tekrar deneyin.", file=sys.stderr)
            return 3

        os.makedirs(os.path.dirname(args.cikti) or ".", exist_ok=True)
        with open(args.cikti, "w", encoding="utf-8") as fh:
            json.dump(hedef, fh, ensure_ascii=False, indent=2)
        print(f"  yakalandı → {args.cikti} · messages={len(hedef['messages'])} · "
              f"prompt_chars={sum(len(str(m.get('content') or '')) for m in hedef['messages'])} · "
              f"tools={len(hedef.get('tools') or [])} · extra_body={hedef.get('extra_body')}")
        if args.yalniz_yakala:
            return 0

    # Tam tool listesi: yakalandıysa oradan, yoksa modül sabitlerinden (DB gerekmez).
    if tam is None:
        from ragintel.agents.tools import (LOOKUP_DOCUMENT_SCHEMA, RERANK_SCHEMA,
                                           SEARCH_HYBRID_SCHEMA, SUBMIT_ANSWER_SCHEMA)
        tam_tools = [SEARCH_HYBRID_SCHEMA, LOOKUP_DOCUMENT_SCHEMA, RERANK_SCHEMA,
                     SUBMIT_ANSWER_SCHEMA]
    else:
        from ragintel.agents.tools import SEARCH_HYBRID_SCHEMA
        tam_tools = tam["tools"]

    tanim: dict = {}
    tanim["A1"] = ("A1  aynen", hedef)
    tanim["A2"] = ("A2  aynen (tekrar)", hedef)
    tanim["A"] = tanim["A1"]
    tanim["B"] = (f"B   tools={len(tam_tools)} (tam liste)", dict(hedef) | {"tools": tam_tools})
    if hedef.get("extra_body"):
        tanim["C"] = ("C   reasoning_effort YOK", {k: v for k, v in hedef.items() if k != "extra_body"})
    tanim["D"] = ("D   tools YOK (serbest metin)", {k: v for k, v in hedef.items() if k != "tools"})
    # E: tetikleyici TAM OLARAK "1" mi, yoksa "az" mı? Düzeltmenin şeklini bu ayırır.
    ikili = [*(hedef.get("tools") or []), SEARCH_HYBRID_SCHEMA]
    tanim["E"] = ("E   tools=2 (submit + search)", dict(hedef) | {"tools": ikili})

    istenen = [k.strip().upper() for k in args.kollar.split(",") if k.strip()]
    if "A" in istenen:                       # 'A' = iki tekrar (kilit determinizmi)
        istenen = [x for k in istenen for x in (("A1", "A2") if k == "A" else (k,))]
    bilinmeyen = [k for k in istenen if k not in tanim]
    if bilinmeyen:
        print(f"HATA: bilinmeyen kol {bilinmeyen} (geçerli: {sorted(tanim)})", file=sys.stderr)
        return 2
    kollar = [tanim[k] for k in istenen]

    print(f"\n[2/2] {len(kollar)} kol · kol başına en fazla {args.timeout:.0f} s\n")
    sonuclar = []
    for ad, kw in kollar:
        r = _oynat(ad, kw, args.timeout)
        sonuclar.append(r)
        ek = f" tool_calls={r['tool_calls']} tokens={r['completion_tokens']}" if r["sonuc"] == "OK" \
             else f" — {r.get('hata', '')}"
        print(f"  {ad:<28} {r['sonuc']:<12} {r['sn']:>6.1f} s{ek}")

    print("\n--- OKUMA ---")
    a = [r for r in sonuclar if r["kol"].startswith("A")]
    if not a:
        print("A kolu koşulmadı → kilit determinizmi hakkında bu koşum bir şey söylemez.")
    elif all(r["sonuc"] == "OK" for r in a):
        print("A kolu GEÇTİ: kilit bu istekle TEKRARLANMIYOR → tetikleyici istem değil, "
              "uçun O ANKİ durumu (biriken slot/oturum). Sorunun adresi Ollama tarafıdır.")
    else:
        print("A kolu DÜŞTÜ: kilit istekle birlikte taşınıyor → tetikleyici bu isteğin KENDİSİ. "
              "B/C/D kollarından GEÇEN varsa fark, kilidin sebebini gösterir.")
    print(json.dumps(sonuclar, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

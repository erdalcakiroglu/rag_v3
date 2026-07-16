"""M-10/0 — DOĞRUDAN Ollama smoke + '500 JSON' suçlu tespiti (H200'de koşar).

İKİ İŞ BİRDEN:

1) SUÇLU TESPİTİ (M-9.1'den taşınan). Bulgu: `submit_answer` tool-call'unda
   "500 failed to parse JSON: invalid character 'H'" — model, citations JSON'unda
   Türkçe özel-ad quote'unda (`"quote":Hakan…` tırnaksız) bozuk çıktı üretiyordu.
   Bu HER ZAMAN değil, ARA SIRA oluyordu (aynı istek bir 200 bir 500).
   Soru: suçlu Open WebUI katmanı mı, Ollama/model mi?
     → Kayıtlı GERÇEK istek (m91_gs012_istek.json) DOĞRUDAN :11434'e N kez gider.
       TEMİZ (0 hata)  → suçlu WebUI katmanıydı; doğrudan-Ollama'ya taşınma ÇÖZER.
       HÂLÂ KIRIK      → suçlu Ollama/model; sürüm kontrolü / model kararı gerekir.

2) UYUM SMOKE'U: doğrudan Ollama'da varsayımlarımız tutuyor mu?
   - Bearer gerekmiyor mu? (auth'suz uç)
   - openai SDK boş api_key'i reddediyor mu? (compose'daki yer tutucu gerekli mi?)
   - `:latest` etiketi kabul ediliyor mu? (ollama_wire_tag zararsız mı?)
   - /v1 OpenAI-compat tool-calling çalışıyor mu?

KULLANIM (H200 üzerinde):
    python scripts/ollama_dogrudan_smoke.py [--n 20] [--base http://localhost:11434]
    # kayıtlı istek dosyası: --istek /yol/m91_gs012_istek.json (yoksa 1-2 atlanır)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

import httpx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("OLLAMA_BASE", "http://localhost:11434"))
    ap.add_argument("--n", type=int, default=20, help="kayıtlı isteğin tekrar sayısı")
    ap.add_argument("--istek", default="m91_gs012_istek.json",
                    help="M-9.1'de kaydedilen gerçek submit_answer isteği")
    ap.add_argument("--model", default="qwen3.5:35b")
    ap.add_argument("--embed-model", default="bge-m3:latest")
    a = ap.parse_args()

    base = a.base.rstrip("/")
    v1 = f"{base}/v1"
    ok = True

    print(f"=== DOĞRUDAN OLLAMA SMOKE — {base} ===\n")

    # --- 0) canlılık ---
    print("[0] canlılık (/api/tags)")
    try:
        r = httpx.get(f"{base}/api/tags", timeout=15)
        r.raise_for_status()
        adlar = [m["name"] for m in r.json().get("models", [])]
        print(f"    OK — {len(adlar)} model. Hedefler yüklü mü: "
              f"{a.model}={'EVET' if any(a.model in n for n in adlar) else 'HAYIR'} | "
              f"bge-m3={'EVET' if any('bge-m3' in n for n in adlar) else 'HAYIR'}")
    except Exception as e:
        print(f"    DÜŞTÜ: {str(e)[:120]}")
        return 1

    # --- 1) Bearer GEREKMİYOR mu? (auth'suz uç varsayımı) ---
    print("\n[1] Bearer'sız istek kabul ediliyor mu?")
    govde = {"model": a.model, "messages": [{"role": "user", "content": "tek kelime: merhaba"}],
             "max_tokens": 5, "temperature": 0}
    try:
        r = httpx.post(f"{v1}/chat/completions", json=govde, timeout=120)
        print(f"    HTTP {r.status_code} → {'OK (auth yok, Bearer GEREKMİYOR)' if r.status_code == 200 else r.text[:150]}")
        ok &= r.status_code == 200
    except Exception as e:
        print(f"    DÜŞTÜ: {str(e)[:120]}"); ok = False

    # --- 2) openai SDK boş api_key'i kabul eder mi? (compose yer tutucusu gerekli mi?) ---
    print("\n[2] litellm/openai boş api_key ile çalışır mı? (compose'da yer tutucu şart mı?)")
    for anahtar in ("", "ollama-local"):
        try:
            import litellm
            resp = litellm.completion(
                model=f"openai/{a.model}", api_base=v1, api_key=anahtar or None,
                messages=[{"role": "user", "content": "tek kelime: test"}],
                max_tokens=5, temperature=0,
            )
            print(f"    api_key={anahtar!r:16} → OK ({resp.choices[0].message.content!r})")
        except Exception as e:
            print(f"    api_key={anahtar!r:16} → HATA: {str(e)[:90]}")

    # --- 3) `:latest` etiketi kabul mü? (ollama_wire_tag zararsız mı?) ---
    print("\n[3] embedding: `:latest` etiketi kabul ediliyor mu? (damga DEĞİŞMEZ)")
    for tag in ("bge-m3", a.embed_model):
        try:
            r = httpx.post(f"{base}/api/embed", json={"model": tag, "input": ["test"]}, timeout=60)
            n = len((r.json().get("embeddings") or [[]])[0]) if r.status_code == 200 else 0
            print(f"    {tag:16} → HTTP {r.status_code}, boyut={n}")
            ok &= r.status_code == 200
        except Exception as e:
            print(f"    {tag:16} → DÜŞTÜ: {str(e)[:90]}"); ok = False

    # --- 4) SUÇLU TESPİTİ: kayıtlı GERÇEK submit_answer isteği ×N ---
    print(f"\n[4] '500 JSON' SUÇLU TESPİTİ — kayıtlı istek ×{a.n} (doğrudan Ollama)")
    if not os.path.exists(a.istek):
        print(f"    ATLANDI: {a.istek} yok. (M-9.1 tanı betiği bunu üretir.)")
        print("    NOT: Bu adım olmadan 'suçlu WebUI'ydi' SONUCU ÇIKARILAMAZ.")
        return 0 if ok else 1

    d = json.load(open(a.istek, encoding="utf-8"))
    body = {"model": a.model, "messages": d["messages"], "tools": d.get("tools"),
            "temperature": 0.0, "max_tokens": 800}
    say: Counter = Counter()
    ilk_hata = None
    for i in range(a.n):
        try:
            r = httpx.post(f"{v1}/chat/completions", json=body, timeout=180)
            say[r.status_code] += 1
            if r.status_code != 200 and ilk_hata is None:
                ilk_hata = r.text[:400]
            # 200 olsa bile tool-call arguments GEÇERLİ JSON mu? (asıl kırılma noktası)
            if r.status_code == 200:
                tc = (r.json()["choices"][0]["message"].get("tool_calls") or [])
                for c in tc:
                    try:
                        json.loads(c["function"]["arguments"])
                    except Exception as je:
                        say["bozuk_json_200"] += 1
                        if ilk_hata is None:
                            ilk_hata = f"HTTP 200 ama arguments BOZUK: {je} :: {c['function']['arguments'][:200]}"
            print(f"    [{i+1:>2}/{a.n}] {r.status_code}", end="\r", flush=True)
        except Exception as e:
            say["istisna"] += 1
            if ilk_hata is None:
                ilk_hata = str(e)[:200]

    print(" " * 30, end="\r")
    print(f"    SONUÇ: {dict(say)}")
    if ilk_hata:
        print(f"    İLK HATA: {ilk_hata}")

    temiz = say.get(200, 0) == a.n and not say.get("bozuk_json_200") and not say.get("istisna")
    print()
    if temiz:
        print(f"    >>> {a.n}/{a.n} TEMİZ → suçlu OPEN WEBUI katmanıydı.")
        print("        Doğrudan-Ollama'ya taşınma (compose) bu arızayı ÇÖZER.")
    else:
        print("    >>> HÂLÂ KIRIK → suçlu Ollama/model tarafı (WebUI değil).")
        print("        Sonraki adım AYRI KARAR: Ollama sürüm kontrolü, gerekirse")
        print("        model değişikliği (qwen3.6 vb.) — mimara gider.")
        ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

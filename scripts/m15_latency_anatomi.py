"""M-15 ADIM 1 — base-latency ANATOMİSİ (kod öncesi, ölçümle).

SORU: bir sorunun süresi NEREDE geçiyor? Hipotezler:
  (a) reasoning-token — `agent.reasoning_effort='none'` GERÇEKTEN uygulanıyor mu?
  (b) prompt-eval — tool şemaları her turda tekrar gönderiliyor (M-10: ~915 tok)
  (c) üretim — submit_answer + citation üretimi mi uzun?

ÖLÇÜM NOKTASI: `litellm.completion` sarılır. Tek yerde hem GİDEN istek (extra_body'de
reasoning_effort var mı, tools kaç bayt) hem GELEN ham yanıt (reasoning/thinking alanı
dolu mu, Ollama native süre alanları geliyor mu) görülür. Ajan koduna dokunulmaz.

AYRICA (b) için A/B NATIVE PROB: aynı mesajlar Ollama native /api/chat'e
tools İLE ve tools SİZ gönderilir → `prompt_eval_count` farkı = tool şemasının
GERÇEK token maliyeti (tahmin değil, ölçüm). Native uç yoksa AÇIKÇA raporlanır.

KRİTİK ÇELİŞKİ (bu betik çözecek): M-10 kaydı "base ~24-27s/çağrı" diyor, ama canlı
`llm_call_timing` satırları 1.7-6.3s/çağrı gösterdi. M-10'daki sayı `/ask` SORU-BAŞI
duvar saatiydi. Hangisi doğru → P95<15s hedefi zaten sağlanıyor olabilir.

KULLANIM: docker exec -i ragintel-api env GOLDEN=v0.1 N=5 python - < scripts/m15_latency_anatomi.py
"""
from __future__ import annotations

import json
import os
import time

GOLDEN = os.environ.get("GOLDEN", "v0.1")
MODEL = os.environ.get("POC_MODEL") or os.environ.get("RAGINTEL_LLM_MODEL") or "qwen3.5:35b"
N = int(os.environ.get("N", "5"))
PER_CAT = int(os.environ.get("PER_CAT", "1"))   # kategori başına kaç soru (örneklem genişletme)

# --- 1) litellm.completion sarmalayıcı (ölçüm noktası) -----------------------
import litellm  # noqa: E402

from ragintel.eval import harness  # noqa: E402
from ragintel.llm.gateway import _build_timing, _reasoning_of  # noqa: E402

CALLS: list[dict] = []
LAST_TOOLS: list = []          # (b) A/B probu için gerçek tool şeması
_orig_completion = litellm.completion


def _recording_completion(**kwargs):
    t0 = time.perf_counter()
    resp = _orig_completion(**kwargs)
    wall_ms = (time.perf_counter() - t0) * 1000.0

    msg = resp.choices[0].message
    usage = getattr(resp, "usage", None)
    timing = _build_timing(resp, wall_ms)
    tools = kwargs.get("tools") or []
    if tools:
        LAST_TOOLS[:] = tools
    CALLS.append({
        "wall_ms": round(wall_ms, 1),
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        # (a) İSTEK tarafı: reasoning_effort gövdeye gerçekten kondu mu?
        "req_reasoning_effort": (kwargs.get("extra_body") or {}).get("reasoning_effort", "— YOK"),
        "req_temperature": kwargs.get("temperature"),
        # (a) YANIT tarafı: model hâlâ düşünce üretiyor mu?
        "reasoning_chars": len(_reasoning_of(msg)),
        # (b) tool şeması her turda gidiyor mu, ne kadar?
        "n_tools": len(tools),
        "tools_bytes": len(json.dumps(tools, ensure_ascii=False)) if tools else 0,
        "n_messages": len(kwargs.get("messages") or []),
        # (c) üretim
        "has_tool_calls": bool(getattr(msg, "tool_calls", None)),
        # native süre dökümü LiteLLM'den geçiyor mu?
        "native": {k: v for k, v in timing.as_dict().items() if k != "latency_ms"},
    })
    return resp


litellm.completion = _recording_completion


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(round(p * (len(s) - 1))))], 1)


def _short(s, n=90):
    s = " ".join((str(s) or "").split())
    return s if len(s) <= n else s[:n] + "…"


db, cfg, model, app = harness.build_eval_app(MODEL)
try:
    agent_cfg = cfg.group("agent")
    print(f"# M-15 ADIM 1 — latency anatomisi — golden={GOLDEN} agent={model} N={N}")
    print(f"# config: reasoning_effort={agent_cfg.reasoning_effort!r} "
          f"temperature={agent_cfg.temperature} max_iterations={agent_cfg.max_iterations}\n")

    with db.connection() as conn:
        records = harness.repo.list_golden_records(conn, GOLDEN)
    # Kategori çeşitliliği: her kategoriden en fazla 1, N tane (tek tip soruya bakıp
    # genelleme yapmamak için — M-10'daki tek-soru ölçümünün tuzağı buydu).
    picked, cat_n = [], {}
    for r in records:
        c = r["category"]
        if cat_n.get(c, 0) < PER_CAT:
            picked.append(r); cat_n[c] = cat_n.get(c, 0) + 1
        if len(picked) >= N:
            break

    rows = []
    for rec in picked:
        CALLS.clear()
        t0 = time.perf_counter()
        row = harness.run_question(app, rec)
        total_ms = (time.perf_counter() - t0) * 1000.0
        calls = list(CALLS)
        llm_ms = sum(c["wall_ms"] for c in calls)
        rows.append({"rec": rec, "row": row, "total_ms": total_ms, "calls": calls, "llm_ms": llm_ms})

        print(f"### {rec['id']} [{rec['category']}] iter={row.get('iterations')} "
              f"conf={row.get('confidence')}")
        print(f"    SORU: {_short(rec['question'], 100)}")
        print(f"    SORU-BAŞI TOPLAM = {total_ms/1000:.1f}s   "
              f"(LLM {llm_ms/1000:.1f}s = %{100*llm_ms/total_ms:.0f}, "
              f"LLM-DIŞI {(total_ms-llm_ms)/1000:.1f}s = retrieval+embed+DB+graph)")
        for i, c in enumerate(calls, 1):
            print(f"      çağrı{i}: {c['wall_ms']/1000:6.1f}s  "
                  f"prompt_tok={c['prompt_tokens']:<6} gen_tok={c['completion_tokens']:<5} "
                  f"reasoning_chars={c['reasoning_chars']:<6} tool_call={c['has_tool_calls']}")
            print(f"                req.reasoning_effort={c['req_reasoning_effort']!r} "
                  f"temp={c['req_temperature']} tools={c['n_tools']}({c['tools_bytes']}B) "
                  f"msgs={c['n_messages']} native={c['native'] or '— YOK (LiteLLM kırpıyor)'}")
        print()

    # --- ÖZET ---------------------------------------------------------------
    q_tot = [r["total_ms"] for r in rows]
    c_all = [c["wall_ms"] for r in rows for c in r["calls"]]
    n_calls = [len(r["calls"]) for r in rows]
    gen_tok = [c["completion_tokens"] for r in rows for c in r["calls"]]
    pr_tok = [c["prompt_tokens"] for r in rows for c in r["calls"]]
    reason = [c["reasoning_chars"] for r in rows for c in r["calls"]]
    llm_share = sum(r["llm_ms"] for r in rows) / sum(q_tot) if sum(q_tot) else 0

    print("############ ÖZET ############")
    print(f"SORU-BAŞI (n={len(q_tot)}): p50={_pct(q_tot,.5)/1000:.1f}s  p95={_pct(q_tot,.95)/1000:.1f}s  "
          f"max={max(q_tot)/1000:.1f}s   → HEDEF P95<15s: "
          f"{'SAĞLANIYOR' if _pct(q_tot,.95) < 15000 else 'AŞILIYOR'}")
    print(f"ÇAĞRI-BAŞI (n={len(c_all)}): p50={_pct(c_all,.5)/1000:.1f}s  p95={_pct(c_all,.95)/1000:.1f}s")
    print(f"çağrı/soru: {n_calls}  |  LLM payı: %{100*llm_share:.0f}")
    print(f"prompt_tok: p50={_pct(pr_tok,.5):.0f} max={max(pr_tok) if pr_tok else 0}  "
          f"gen_tok: p50={_pct(gen_tok,.5):.0f} max={max(gen_tok) if gen_tok else 0}")
    print(f"(a) reasoning_chars: toplam={sum(reason)} max={max(reason) if reason else 0} "
          f"→ {'DÜŞÜNME KAPALI (token yakmıyor)' if sum(reason) == 0 else 'HÂLÂ DÜŞÜNÜYOR — (a) ADAY'}")

    # --- AYKIRI ANATOMİSİ: yavaş soruyu YAVAŞLATAN ne? tur mu, üretim mi? -------
    # (HTTP koşumunda tek soru 24.3s'ye çıkmıştı; sebep tur sayısı varyansı mı?)
    print("\n############ EN YAVAŞ 3 SORU — neden yavaş? ############")
    print(f"{'id':<12} {'sn':>6} {'çağrı':>5} {'iter':>4} {'retry':>5} {'gen_tok':>8} {'prompt_tok_max':>14}")
    for r in sorted(rows, key=lambda x: -x["total_ms"])[:3]:
        cs = r["calls"]
        print(f"{r['rec']['id']:<12} {r['total_ms']/1000:>6.1f} {len(cs):>5} "
              f"{str(r['row'].get('iterations')):>4} {str(r['row'].get('retry_count', '-')):>5} "
              f"{sum(c['completion_tokens'] for c in cs):>8} "
              f"{max((c['prompt_tokens'] for c in cs), default=0):>14}")
    if n_calls:
        per_call = sum(c_all) / len(c_all) / 1000
        print(f"  → ortalama tur maliyeti {per_call:.1f}s; çağrı sayısı {min(n_calls)}→{max(n_calls)} "
              f"aralığında ⇒ tek fazladan tur ≈ +{per_call:.1f}s "
              f"(soru-başı varyansın ana kaynağı buysa hedef 'tur azaltma'dır, 'çağrı hızlandırma' değil)")

    # --- (b) A/B NATIVE PROB: tool şemasının gerçek token maliyeti ------------
    print("\n############ (b) TOOL ŞEMASI A/B (native /api/chat) ############")
    base = (os.environ.get("RAGINTEL_LLM_API_BASE") or "").rstrip("/")
    for suf in ("/v1", "/ollama/v1", "/api"):
        if base.endswith(suf):
            base = base[: -len(suf)]
            break
    if not base:
        print("  ATLANDI: RAGINTEL_LLM_API_BASE boş — native uç türetilemedi.")
    else:
        import urllib.error
        import urllib.request

        msgs = [{"role": "user", "content": "Karbon vergisi nedir? Tek cümle."}]

        def _native(with_tools: bool):
            body = {"model": model, "messages": msgs, "stream": False,
                    "options": {"temperature": 0, "num_predict": 1}}
            if with_tools and LAST_TOOLS:
                body["tools"] = LAST_TOOLS
            req = urllib.request.Request(f"{base}/api/chat",
                                         data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            key = os.environ.get("RAGINTEL_LLM_API_KEY")
            if key:
                req.add_header("Authorization", f"Bearer {key}")
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read())

        def _line(tag, d):
            print(f"  {tag}: prompt_eval_count={d.get('prompt_eval_count')} "
                  f"prompt_eval={round((d.get('prompt_eval_duration') or 0)/1e6)}ms "
                  f"eval_count={d.get('eval_count')} "
                  f"eval={round((d.get('eval_duration') or 0)/1e6)}ms "
                  f"load={round((d.get('load_duration') or 0)/1e6)}ms")

        try:
            off = _native(False)
            _line("tools YOK ", off)
            if LAST_TOOLS:
                on = _native(True)
                _line("tools VAR ", on)
                d_tok = (on.get("prompt_eval_count") or 0) - (off.get("prompt_eval_count") or 0)
                d_ms = ((on.get("prompt_eval_duration") or 0) - (off.get("prompt_eval_duration") or 0)) / 1e6
                print(f"  → TOOL ŞEMASI MALİYETİ: +{d_tok} token, +{round(d_ms)}ms prompt-eval "
                      f"(HER TURDA tekrar) — (b) hipotezinin ölçülmüş değeri")
            else:
                print("  (tool şeması yakalanamadı — ajan hiç tools göndermemiş?)")
            print(f"  ham anahtarlar: {sorted(k for k in off if 'count' in k or 'duration' in k)}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"  ERİŞİLEMEDİ ({type(e).__name__}: {e}) — native kırılım YOK.")
            print("  Bu durumda (b) yalnız prompt_tokens üzerinden okunur (yukarıda).")

    print("\n############ OKUMA KILAVUZU ############")
    print("- SORU-BAŞI p95 < 15s ise: hedef ZATEN sağlanıyor → optimizasyon gerekmez, premis düzeltilir.")
    print("- reasoning_chars > 0 ise: (a) canlı doğrulandı → en büyük kazanç adayı.")
    print("- LLM payı < %70 ise: darboğaz LLM DEĞİL (retrieval/embed/DB) → M-15 hedefi kayar.")
    print("- prompt_tok turlar arası büyüyorsa: (b) tool şeması + geçmiş birikimi.")
finally:
    litellm.completion = _orig_completion
    try:
        db.close()
    except Exception:
        pass

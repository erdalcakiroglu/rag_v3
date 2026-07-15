"""FAZ 8 — CI eval gate karar mantığı (saf, test edilebilir).

Çıkış kodları (CI ayrımı): pass=0 / fail=1 / altyapı-hatası=2. Eşikler config-first
(app_config('eval_gates')); değişince gate davranışı değişir. DEV eşikleri regresyon
yakalar (mühürlü dilim-1 karnesinin ~%5 altı), mükemmellik dayatmaz.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GateThresholds:
    honesty_min_ratio: float = 0.80
    faithfulness_min: float = 0.70
    context_precision_min: float = 0.75


@dataclass
class GateOutcome:
    code: int                      # 0 pass, 1 fail (eşik altı), 2 altyapı hatası
    reason: str
    checks: list = field(default_factory=list)   # [(ad, değer, eşik, ok)]


def thresholds_from_config(cfg) -> GateThresholds:
    """`app_config('eval_gates')`'ten eşikler (yoksa kod varsayılanı)."""
    try:
        g = cfg.group("eval_gates")
        return GateThresholds(
            honesty_min_ratio=float(getattr(g, "honesty_min_ratio", 0.80)),
            faithfulness_min=float(getattr(g, "faithfulness_min", 0.70)),
            context_precision_min=float(getattr(g, "context_precision_min", 0.75)),
        )
    except Exception:
        return GateThresholds()


def evidence_precondition(conn, version: str) -> GateOutcome | None:
    """Judge ÇAĞRILMADAN ÖNCE koşulan ucuz, deterministik ALTYAPI ön-koşulu (M-7 son adım).

    Golden set'in `gold_evidence` alıntıları HÂLÂ geçerli korpusta çözülüyor mu? (loader'ın
    `_validate_evidence`'ı ile BİREBİR aynı mantık — `retrieval_benchmark.map_gold_chunks`
    üzerinden.) Bu bir KALİTE REGRESYONU testi DEĞİLDİR: korpus/parse (ör. docling sürüm
    yükseltmesi) golden set çıpalandığından beri değişmiş olabilir; böyle bir kaymayı kalite
    düşüşü gibi yorumlayıp pahalı judge çağrısını (token harcayarak) boşa harcamak yanlıştır.
    Bu yüzden gate'in eşik-kıyas aşamasından ÖNCE, ayrı ve ucuz bir kontrol olarak çalışır.

    Dönüş: None → ön-koşul geçti, gate normal akışına (harness.evaluate → gate_decision)
           devam edebilir. GateOutcome(2, ...) → evidence çözülemedi; çağıran BURADA
           durmalı ve judge'ı hiç çağırmamalı.
    """
    from . import repository as repo
    from .retrieval_benchmark import from_db_rows, map_gold_chunks

    records = repo.list_golden_records(conn, version)
    if not records:
        return GateOutcome(2, f"'{version}' golden set DB'de yok — önce `eval load` ile yükleyin.")

    eval_records = from_db_rows(records)
    mapping = map_gold_chunks(conn, eval_records)
    if mapping.unmapped:
        detail = "; ".join(
            f"{u['record_id']} [{u['file_name']}"
            + (f" s.{u['page']}" if u.get("page") else f" sayfa:{u.get('sheet')}")
            + f"]: \"{u['quote']}\""
            for u in mapping.unmapped
        )
        reason = (
            f"evidence çözülemedi ({len(mapping.unmapped)}/{mapping.total_evidence} alıntı) — "
            "bu bir KALİTE REGRESYONU değil, ÖLÇÜM ZEMİNİNİN KAYMASIDIR (korpus/parse değişti, "
            "golden çıpaları artık tutmuyor). Judge ÇAĞRILMADI (token harcanmadı). "
            f"Aksiyon: ilgili kayıt/alıntıyı yeni bir golden sürümüyle (ör. {version}.1) "
            "yeniden çıpalayıp `python -m ragintel.eval load <dosya> --version <yeni-sürüm>` "
            f"ile yükleyin, ardından gate'i yeni sürümle koşun. Çözülemeyenler: {detail}"
        )
        return GateOutcome(2, reason)
    return None


def effective_models(cfg, *, agent_model: str | None = None,
                     judge_model: str | None = None) -> dict[str, str]:
    """Gate'in GERÇEKTEN koşacağı agent/judge modeli + ölçüm zemini ayarları.

    Öncelik harness.build_eval_app ile BİREBİR aynı olmalı (CLI > DB > .env > kod);
    burada ayrışırsa rapor yalan söyler.
    """
    from ..config.settings import LiteLLMSettings
    from .harness import DEFAULT_AGENT_MODEL, DEFAULT_JUDGE_MODEL

    ev = cfg.group("eval")
    return {
        "agent": (agent_model or ev.agent_model or LiteLLMSettings().model
                  or DEFAULT_AGENT_MODEL),
        "judge": judge_model or ev.judge_model or DEFAULT_JUDGE_MODEL,
        # Ölçüm zemininin parçası: filtreli-ANN semantiği retrieval'ı değiştirir.
        "iterative_scan": str(cfg.group("retrieval").hnsw_iterative_scan),
        # M-9: sıcaklık ölçüm zeminidir — fallback varyansının kök kaynağıydı.
        "temperature": float(cfg.group("agent").temperature),
    }


def model_ground_precondition(cfg, *, agent_model: str | None = None,
                              judge_model: str | None = None,
                              allow_drift: bool = False) -> GateOutcome | None:
    """Judge ÇAĞRILMADAN ÖNCE: gate, KARNENİN modelleriyle mi koşuyor? (M-7)

    Karnenin zemini DB'dedir (`eval.agent_model` / `eval.judge_model`). CLI ya da `.env`
    ile başka bir model devreye girerse ölçüm KARNEYLE KIYASLANAMAZ hâle gelir — ama
    sayılar yine de üretilir ve "regresyon" sanılır. Bu tam olarak yaşandı: `.env`'de
    kalmış `RAGINTEL_LLM_MODEL=deepseek-v4-pro`, karnenin agent'ını (qwen/qwen3-32b)
    sessizce ezdi; context_precision 0.775 → 0.630 "düşüş" gibi göründü, oysa başka bir
    agent ölçülüyordu.

    Evidence ön-koşuluyla aynı sınıf: KALİTE REGRESYONU DEĞİL, ÖLÇÜM ZEMİNİNİN KAYMASI
    → exit 2 (altyapı), exit 1 değil. Bilinçli sapma için `--allow-model-drift`.
    """
    eff = effective_models(cfg, agent_model=agent_model, judge_model=judge_model)
    ev = cfg.group("eval")
    sapan = []
    if ev.agent_model and eff["agent"] != ev.agent_model:
        sapan.append(f"agent: karne='{ev.agent_model}' ≠ koşum='{eff['agent']}'")
    if ev.judge_model and eff["judge"] != ev.judge_model:
        sapan.append(f"judge: karne='{ev.judge_model}' ≠ koşum='{eff['judge']}'")
    # M-9: sıcaklık sapması da zemin kaymasıdır — deterministik zeminde mühürlenen karne,
    # sıcaklık yükseltilmiş bir koşumla kıyaslanamaz (fallback varyansı geri gelir).
    if eff["temperature"] != float(ev.agent_temperature):
        sapan.append(f"temperature: karne={ev.agent_temperature} ≠ koşum={eff['temperature']}")
    if not sapan or allow_drift:
        return None
    return GateOutcome(2, (
        "MODEL ZEMİNİ KAYDI — gate, karnenin modelleriyle koşmuyor: " + " · ".join(sapan)
        + ". Bu bir kalite regresyonu DEĞİL; üretilecek sayılar mühürlü karneyle "
        "KIYASLANAMAZ. Judge ÇAĞRILMADI (token harcanmadı). Aksiyon: ya modeli karneye "
        "döndürün (DB `eval.agent_model`/`eval.judge_model` config-first otoritedir; "
        "`.env RAGINTEL_LLM_MODEL` artık onu EZEMEZ), ya da bilinçli bir zemin değişikliği "
        "ise YENİ KARNE mühürleyip eşikleri kalibre edin. Yalnızca ölçüm amaçlı geçici "
        "sapma için: --allow-model-drift."
    ))


def gate_decision(result: dict, thr: GateThresholds, *, smoke: bool = False) -> GateOutcome:
    """Eval sonucunu eşiklerle kıyaslar. ÖNCE altyapı sağlığı (exit 2), sonra eşik (0/1).

    SMOKE'ta SİNYAL-VARYANS EŞLEMESİ (M-7) — eşik gevşetme DEĞİLDİR:
    Aynı eşikler, aynı sayılar; değişen tek şey hangi sinyalin exit kodunu taşımaya
    YETERİNCE KARARLI olduğudur.

      HARD (exit 1)     : honesty_ratio — 5 soruda deterministik bir kontroldür
                          (agent "bilmiyorum" diyebildi mi?), judge puanı değil.
      ADVISORY (exit 0) : faithfulness / context_precision — n=5 + TEK KOŞUM judge
                          puanıdır. Ölçüldü: aynı korpusta tek koşum
                          context_precision 0.630, runs=3 medyanı 0.739 verdi.
                          Bu varyans hard-fail TAŞIYAMAZ: her push'ta rastgele kırmızı
                          yanan bir gate, KURT-ÇOCUK etkisiyle korumanın kendisini
                          öldürür (kimse bakmaz olur).

    Otoriter hard gate = NIGHTLY TAM koşu (36 soru, runs=3) — judge metrikleri orada
    hard'tır, çünkü orada varyans yeterince bastırılmıştır.
    (Altyapı/evidence/model-zemini zaten exit 2'dir ve smoke'ta da HARD kalır.)
    """
    # --- altyapı hataları (exit 2): eval güvenilir çalışmadı ---
    if result.get("status") != "complete":
        return GateOutcome(2, f"eval tamamlanmadı (status={result.get('status')}) — rate-limit/kap?")
    scored = result.get("ragas", {}).get("per_question", [])
    if not scored:
        return GateOutcome(2, "hiçbir answerable skorlanmadı (retrieval/judge altyapısı?)")
    ds = result.get("dataset", {})
    total_run = int(ds.get("answerable_run", 0)) + int(ds.get("unanswerable_run", 0))
    if "answered_with_context" in ds and ds["answered_with_context"] == 0 and ds.get("answerable_run", 0) > 0:
        return GateOutcome(2, "hiçbir yanıt bağlam almadı (embedder/retrieval down)")
    if total_run and len(ds.get("errors", [])) >= total_run:
        return GateOutcome(2, f"tüm sorular hata verdi ({len(ds.get('errors', []))})")

    # --- eşik kıyası (exit 0/1) ---
    ov = result["ragas"]["overall"]
    h = result.get("honesty", {})
    hon_ratio = round(h.get("pass", 0) / h["total"], 4) if h.get("total") else 0.0
    # (ad, değer, eşik, HARD mı?) — smoke'ta judge-metrikleri advisory'ye düşer.
    raw = [
        ("faithfulness", float(ov.get("faithfulness", 0.0)), thr.faithfulness_min, not smoke),
        ("context_precision", float(ov.get("context_precision", 0.0)), thr.context_precision_min, not smoke),
        ("honesty_ratio", hon_ratio, thr.honesty_min_ratio, True),   # her modda HARD
    ]
    checks = [(n, v, t, v >= t, hard) for (n, v, t, hard) in raw]

    hard_failed = [c for c in checks if not c[3] and c[4]]
    advisory_failed = [c for c in checks if not c[3] and not c[4]]

    if hard_failed:
        reason = "eşik ALTINDA: " + ", ".join(c[0] for c in hard_failed)
        if advisory_failed:
            reason += " (ayrıca advisory: " + ", ".join(c[0] for c in advisory_failed) + ")"
        return GateOutcome(1, reason, checks)
    if advisory_failed:
        # Exit'e ETKİ ETMEZ ama SUSTURULMAZ — nightly tam koşuda hard'tır.
        return GateOutcome(0, (
            "hard eşikler geçildi · ADVISORY eşik altı: "
            + ", ".join(c[0] for c in advisory_failed)
            + " — smoke'ta (n=5, tek koşum) judge varyansı hard-fail taşıyamaz; "
              "otoriter karar nightly TAM koşudadır (36, runs=3)."
        ), checks)
    return GateOutcome(0, "tüm eşikler geçildi", checks)


def format_gate(outcome: GateOutcome, result: dict, thr: GateThresholds, *, smoke: bool) -> str:
    verdict = {0: "PASS ✓", 1: "FAIL ✗ (eşik altı)", 2: "ERROR ⚠ (altyapı)"}[outcome.code]
    m = result.get("models") or {}
    L = [f"=== EVAL GATE — {verdict} (exit {outcome.code}) ===",
         # ÖLÇÜM ZEMİNİ her koşumda görünür: hangi agent/judge/ANN semantiğiyle ölçüldü.
         # (Bu satır olmadığı için `.env`'de kalmış bir model override'ı sessizce
         #  karneyle kıyaslanamaz sayılar üretmişti — M-7.)
         f"zemin: agent={m.get('agent','-')} · judge={m.get('judge','-')} · "
         f"temp={m.get('temperature','-')} · iterative_scan={m.get('iterative_scan','-')}",
         f"mod={'smoke(5)' if smoke else 'full(36)'} · judge={result.get('judge','-')} · {outcome.reason}"]
    if outcome.checks:
        L.append("metrik              değer    eşik    sonuç      tür")
        for n, v, t, ok, hard in outcome.checks:
            sonuc = "PASS" if ok else ("FAIL" if hard else "ALTINDA")
            tur = "HARD" if hard else "ADVISORY"   # advisory: exit'e etki etmez, GİZLENMEZ
            L.append(f"  {n:18s}{v:<8.3f}{t:<8.3f}{sonuc:<11s}{tur}")
        if smoke:
            L.append("  ↳ smoke: judge metrikleri ADVISORY (n=5, tek koşum → varyans). "
                     "Otoriter hard gate = nightly TAM koşu (36, runs=3).")
    return "\n".join(L)

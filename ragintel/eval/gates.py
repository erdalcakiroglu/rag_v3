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


def gate_decision(result: dict, thr: GateThresholds) -> GateOutcome:
    """Eval sonucunu eşiklerle kıyaslar. ÖNCE altyapı sağlığı (exit 2), sonra eşik (0/1)."""
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
    raw = [
        ("faithfulness", float(ov.get("faithfulness", 0.0)), thr.faithfulness_min),
        ("context_precision", float(ov.get("context_precision", 0.0)), thr.context_precision_min),
        ("honesty_ratio", hon_ratio, thr.honesty_min_ratio),
    ]
    checks = [(n, v, t, v >= t) for (n, v, t) in raw]
    failed = [c for c in checks if not c[3]]
    if failed:
        return GateOutcome(1, "eşik ALTINDA: " + ", ".join(c[0] for c in failed), checks)
    return GateOutcome(0, "tüm eşikler geçildi", checks)


def format_gate(outcome: GateOutcome, result: dict, thr: GateThresholds, *, smoke: bool) -> str:
    verdict = {0: "PASS ✓", 1: "FAIL ✗ (eşik altı)", 2: "ERROR ⚠ (altyapı)"}[outcome.code]
    L = [f"=== EVAL GATE — {verdict} (exit {outcome.code}) ===",
         f"mod={'smoke(5)' if smoke else 'full(36)'} · judge={result.get('judge','-')} · {outcome.reason}"]
    if outcome.checks:
        L.append("metrik              değer    eşik    sonuç")
        for n, v, t, ok in outcome.checks:
            L.append(f"  {n:18s}{v:<8.3f}{t:<8.3f}{'PASS' if ok else 'FAIL'}")
    return "\n".join(L)

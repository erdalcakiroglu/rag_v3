"""Ölçülemeyen satır karneyi TAM gösteremez — `status=partial` + dar payda uyarısı.

ÖLÇÜLDÜ (2026-08-13, v1-bddk M-17 koşumu)
    18 satırlık dürüstlük kolunun 3'ü (gs-bddk-u12'nin üç tekrarı) 120 s LiteLLM
    timeout'una düştü. Karne yine `status: "complete"` ve `"15/15"` bastı: hata
    kaydı "yapıldı" sayıldığı için tamamlanma sağlandı, hatalı satırlar
    `honesty_rows`'a hiç girmediği için payda 18'den 15'e SESSİZCE indi. Yani
    kayıp, oranı YUKARI çekerek gizlendi — okuyan "6 kaydın tamamı dürüst" sanır,
    oysa altıncı kayıt hiç ölçülmemiştir.

    Aynı sınıf kusur `--limit`'in unanswerable kolunu kırpmasıydı: her ikisinde de
    hatalı olan SAYI değil PAYDA, ve her ikisi de geçerli görünen bir çıktı üretir.
"""

from __future__ import annotations

from ragintel.eval.harness import _METRICS, format_report

_BOS = {m: 0.0 for m in _METRICS} | {"n": 0}   # answerable koşulmadı: metrikler boş blok


def _karne(status: str, *, missing: list[str], total: int, expected: int) -> dict:
    return {
        "judge": "local/dev-mode", "judge_model": "j", "agent_model": "a",
        "agent_temperature": 0.0, "golden": "v1-bddk", "runs": 1, "agent_runs": 3,
        "limit": 0, "status": status, "paused_at": None, "elapsed_sec": 1.0,
        "progress": {"answered": total, "scored": 0, "queue": 6, "answerable": 0},
        "dataset": {"answerable_run": 0, "unanswerable_run": total,
                    "answered_with_context": 0,
                    "errors": [{"id": k, "error": "timeout"} for k in missing]},
        "ragas": {"overall": _BOS, "by_category": {},
                  "answered_only": {"overall": _BOS, "by_category": {}},
                  "fallback": {"count": 0, "total": 0, "rate": 0.0, "ids": []},
                  "per_question": []},
        "honesty": {"pass": total, "total": total, "score": f"{total}/{total}",
                    "expected": expected, "missing": missing,
                    "strict_pass": total, "definition": "D4",
                    "strict_score": f"{total}/{total}", "per_question": []},
        "iterations": {"mean": 4.0, "max": 4, "distribution": {}, "by_category": {}},
        "targets": {},
    }


def test_eksik_satir_karnede_gorunur():
    """Dar payda RAPORDA yazılı olmalı. JSON'a koyup metne koymamak yetmez —
    kimse 40 dakikalık koşumun JSON'ını satır satır okumaz, karneye bakar."""
    metin = format_report(_karne("partial", missing=["gs-bddk-u12#0", "gs-bddk-u12#1"],
                                 total=16, expected=18))

    assert "PARTIAL" in metin
    assert "EKSİK 2/18" in metin
    assert "gs-bddk-u12#0" in metin
    assert "tam karne değildir" in metin


def test_tam_kosum_uyari_basmaz():
    """Kayıp yokken uyarı çıkmamalı; her koşumda görünen uyarı okunmaz hâle gelir."""
    metin = format_report(_karne("complete", missing=[], total=18, expected=18))

    assert "EKSİK" not in metin
    assert "PARTIAL" not in metin


def test_partial_gate_icin_altyapi_hatasidir():
    """`gate` `status != complete` gördüğünde exit 2 (altyapı) verir — doğru sınıf.

    Satır kaybı KALİTE regresyonu değildir; exit 1 basılırsa ekip yanlış yere bakar
    (modeli suçlar), oysa ölçüm hiç tamamlanmamıştır.
    """
    from ragintel.eval.gates import GateThresholds, gate_decision

    thr = GateThresholds(faithfulness_min=0.7, context_precision_min=0.75,
                         honesty_min_ratio=0.8)
    sonuc = gate_decision(_karne("partial", missing=["x#0"], total=17, expected=18), thr)

    assert sonuc.code == 2
    assert "partial" in sonuc.reason

"""`eval run --all-unanswerable` — dürüstlük kolunun sessizce kırpılmasına karşı kapı.

NEDEN TEST
    `harness.evaluate` `--limit` verildiğinde unanswerable kolunu `[: min(2, limit)]`
    ile keser. Bu kırpma dry-run için doğrudur ama M-17 ölçümü için ÖLDÜRÜCÜDÜR:
    bankacılık korpusunda kol 6 kayıttır, kırpılırsa oran 6 yerine 2 paydadan çıkar
    ve karne "6/6 dürüst" derken aslında 2 satır görmüştür. Hata SESSİZDİR — çıktı
    yine geçerli bir karnedir, yalnız payda yanlıştır (bayat evidence dosyasıyla
    aynı sınıf: ölçüm zemini kayması).

    `evaluate` bayrağı zaten destekliyordu; eksik olan CLI'ın onu İLETMESİYDİ. Bu
    yüzden test iletimi kilitler, bayrağın varlığını değil.
"""

from __future__ import annotations

import ragintel.eval.harness as harness
from ragintel.eval.__main__ import main


def _yakala(monkeypatch) -> dict:
    """evaluate'i sahtele — DB/LLM'e gitmeden yalnız iletilen argümanları topla."""
    gelen: dict = {}

    def sahte(**kw):
        gelen.update(kw)
        return {"status": "complete", "summary": {}}

    monkeypatch.setattr(harness, "evaluate", sahte)
    monkeypatch.setattr(harness, "format_report", lambda r: "")
    return gelen


def test_bayrak_evaluate_a_ulasir(monkeypatch):
    """CLI'da verilen `--all-unanswerable`, harness'a True olarak GİTMELİ."""
    gelen = _yakala(monkeypatch)

    assert main(["run", "--golden", "v1-bddk", "--limit", "0", "--all-unanswerable"]) == 0

    assert gelen["all_unanswerable"] is True
    assert gelen["limit"] == 0        # yalnız dürüstlük kolu: sıfır answerable, sıfır judge


def test_bayraksiz_varsayilan_kirpar(monkeypatch):
    """Varsayılan davranış DEĞİŞMEMELİ: bayrak yokken kırpma yürürlükte kalır
    (dry-run'ın ucuzluğu buna bağlı)."""
    gelen = _yakala(monkeypatch)

    assert main(["run", "--golden", "v1-bddk", "--limit", "3"]) == 0

    assert gelen["all_unanswerable"] is False

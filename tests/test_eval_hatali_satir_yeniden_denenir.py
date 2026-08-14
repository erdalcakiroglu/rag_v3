"""Hataya düşen satır checkpoint'te GÖMÜLÜ kalmamalı — yeniden koşum onu tekrar denemeli.

ÖLÇÜLEN OLGU (2026-08-14, v1-bddk M-17)
    Zorlanmış nihai tur kilidi için kaçış yolu kurulduktan sonra koşum aynı
    `--out docs/m17.ckpt` ile tekrarlandı ve şunu bastı:
        elapsed_sec: 0.0 · `llm_call_start` 0 satır · `eval_answered` 0 satır
        missing: ["gs-bddk-u12#0", "gs-bddk-u12#1", "gs-bddk-u12#2"]
    Yani TEK BİR LLM çağrısı bile yapılmadı: harness 15 cevabı checkpoint'ten okuyup
    doğrudan karne bastı, eksik üç satırı kuyruğa geri KOYMADI. Kaçış yolu bu yüzden
    bir kez bile ateşlenemedi (`forced_final_wedge_escape` = 0, "kaçış çalışmadı"
    değil "çağrı yapılmadı").

KÖK
    Atlama ölçütü "yanıtlanmış OR hata kaydı var" idi. Hata kaydı, satırı KALICI olarak
    kuyruktan düşürüyordu; karne (9abb2f5) eksiği doğru ilan ediyor ama kısmi bir karne
    yeniden koşularak KAPATILAMIYORDU.

    Bu, aynı ailenin ÜÇÜNCÜ kusuru: `--limit` kırpması (1ebe9fb) ve "hata = yapıldı"
    sayımı (9abb2f5) paydayı bozuyordu; bu ise paydayı doğru bildirip onarımı
    imkânsız kılıyordu.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from ragintel.eval import harness


class _Grup:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _SahteCfg:
    def group(self, ad: str):
        return {"eval": _Grup(ctx_cap=10, judge_model="j"),
                "agent": _Grup(temperature=0.0)}[ad]


class _SahteDB:
    @contextmanager
    def connection(self):
        yield object()

    def close(self):
        pass


class _SahteJudge:
    label, model = "local/dev-mode", "j"

    def __init__(self, *a, **kw):
        pass


def _kayit(rid: str) -> dict:
    return {"id": rid, "question": f"{rid}?", "category": "unanswerable",
            "answerable": False, "doc_scope": "public", "ideal_answer": ""}


def _satir(rec: dict) -> dict:
    """run_question'ın döndürdüğü eval satırının dürüst-ret hâli (D4 → honest)."""
    return {"id": rec["id"], "category": rec["category"], "answerable": False,
            "question": rec["question"], "ground_truth": "",
            "answer": "Bu bilgi dokümanlarda bulunmamaktadır.", "contexts": [],
            "confidence": "low", "sources": [], "coverage": 0.0, "iterations": 4}


@pytest.fixture
def kur(monkeypatch, tmp_path):
    """İki unanswerable kayıtlı sahte koşum ortamı; DB/judge/agent yok."""
    kayitlar = [_kayit("u11"), _kayit("u12")]
    monkeypatch.setattr(harness, "build_eval_app", lambda m: (_SahteDB(), _SahteCfg(), "a", object()))
    monkeypatch.setattr(harness.repo, "list_golden_records", lambda conn, v: kayitlar)
    monkeypatch.setattr(harness, "Judge", _SahteJudge)
    monkeypatch.setattr(harness, "JudgeEmbedder", lambda *a, **kw: object())

    cagrilar: list[str] = []

    def kos(*, davranis):
        def _run_question(app, rec, *, ctx_cap=10):
            cagrilar.append(rec["id"])
            return davranis(rec)
        monkeypatch.setattr(harness, "run_question", _run_question)
        return harness.evaluate(version="v1-bddk", limit=0, runs=1, judge_model="j",
                                question_delay=0.0, out_path=str(tmp_path / "m17.ckpt"),
                                all_unanswerable=True, agent_runs=1)

    return kos, cagrilar


def _kilit(rec):
    if rec["id"] == "u12":
        raise RuntimeError("APIConnectionError: uç 90 s yanıt vermedi")
    return _satir(rec)


def test_hatali_satir_ikinci_kosumda_yeniden_denenir(kur):
    """Asıl kusur: ikinci koşum u12'ye HİÇ dokunmuyordu (elapsed 0.0, sıfır LLM çağrısı)."""
    kos, cagrilar = kur

    ilk = kos(davranis=_kilit)
    assert ilk["status"] == "partial"
    assert ilk["honesty"]["missing"] == ["u12"]

    cagrilar.clear()
    ikinci = kos(davranis=_satir)          # kaçış yolu devrede: bu kez uç yanıtlıyor

    assert cagrilar == ["u12"]             # yalnız eksik satır — yanıtlanmış satır tekrar koşmaz
    assert ikinci["status"] == "complete"
    assert ikinci["honesty"]["score"] == "2/2"
    assert ikinci["honesty"]["missing"] == []


def test_basari_bayat_hata_kaydini_siler(kur):
    """Karne `dataset.errors`'ı basar; satır artık ölçülmüşken eski hata orada durursa
    okuyan hâlâ bir arıza olduğunu sanır."""
    kos, _ = kur

    ilk = kos(davranis=_kilit)
    assert [e["id"] for e in ilk["dataset"]["errors"]] == ["u12"]

    ikinci = kos(davranis=_satir)

    assert ikinci["dataset"]["errors"] == []


def test_israrli_hata_karneyi_tam_gostermez(kur):
    """Yeniden deneme "sonunda geçer" demek DEĞİLDİR: uç ısrarla kilitlenirse karne
    partial kalır ve eksik satır adıyla görünür (gate → exit 2, altyapı)."""
    kos, cagrilar = kur

    kos(davranis=_kilit)
    cagrilar.clear()
    ikinci = kos(davranis=_kilit)

    assert cagrilar == ["u12"]             # her koşumda BİR kez denenir — sonsuz döngü yok
    assert ikinci["status"] == "partial"
    assert ikinci["honesty"]["missing"] == ["u12"]
    assert ikinci["honesty"]["expected"] == 2

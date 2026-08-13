"""Golden v1 unanswerable kolu — onay kapısı ve şema sözleşmesi.

NEDEN TEST
    `answerable=false` bir kayıt, M-17 dürüstlük kapısının ÖLÇÜM NESNESİDİR:
    sistem "bulunamadı" demezse ceza yer. Yokluğu kanıtlanmamış bir soru sete
    sızarsa kapı modeli değil kendi hatasını ölçer — ve bunu SESSİZCE yapar,
    çünkü çıktı yine geçerli bir golden dosyasıdır. Bu yüzden kapı
    (`karar == "onaylandi"`) koddan test edilir, gözden geçirmeye bırakılmaz.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

KOK = Path(__file__).resolve().parent.parent
ADAYLAR = KOK / "docs" / "golden_v1_unanswerable_adaylar.json"


def _uretici():
    yol = KOK / "scripts" / "golden_v1_jsonl_uret.py"
    spec = importlib.util.spec_from_file_location("golden_v1_jsonl_uret", yol)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _yaz(tmp_path: Path, adaylar: list[dict]) -> Path:
    yol = tmp_path / "adaylar.json"
    yol.write_text(json.dumps({"adaylar": adaylar}, ensure_ascii=False), encoding="utf-8")
    return yol


def _aday(kimlik: str, karar: str) -> dict:
    return {"id": kimlik, "soru": f"{kimlik} sorusu?", "terimler": ["x"],
            "gerekce": "g", "ideal_answer": "Bu soru dokumanlardan yanitlanamaz.",
            "karar": karar}


def test_yalniz_onaylanan_aday_sete_girer(tmp_path):
    """Kapı: 'beklemede' = yokluk HENÜZ gövdeden kanıtlanmadı; 'kontrol' = ölçüm
    nesnesi değil, probe'un kendi sağlamlık sınaması."""
    mod = _uretici()
    yol = _yaz(tmp_path, [_aday("U01", "onaylandi"), _aday("U02", "beklemede"),
                          _aday("U03", "reddedildi"), _aday("K01", "kontrol")])

    kayitlar = mod._unanswerable(yol, "default", "erdal")

    assert [k["id"] for k in kayitlar] == ["gs-bddk-u01"]


def test_unanswerable_kayit_semayi_gecer(tmp_path):
    """models.py sözleşmesi: kategori unanswerable ∧ answerable=false ∧ kanıt YOK.
    Üçü birlikte tutmazsa `load_golden_jsonl` yüklemede patlar."""
    from ragintel.eval.models import GoldenRecord

    mod = _uretici()
    (kayit,) = mod._unanswerable(_yaz(tmp_path, [_aday("U01", "onaylandi")]), "default", "erdal")

    dogrulanan = GoldenRecord.model_validate(kayit)
    assert dogrulanan.category.value == "unanswerable"
    assert dogrulanan.answerable is False
    assert dogrulanan.gold_evidence == []
    assert dogrulanan.doc_scope == "default"


def test_bos_ideal_answer_sessizce_gecmez(tmp_path):
    """ideal_answer boşsa şema zaten reddederdi; hata YÜKLEMEDE değil ÜRETİMDE
    verilsin ki kaynak dosya düzeltilebilsin."""
    mod = _uretici()
    aday = _aday("U01", "onaylandi") | {"ideal_answer": ""}

    with pytest.raises(SystemExit, match="ideal_answer"):
        mod._unanswerable(_yaz(tmp_path, [aday]), "default", "erdal")


def test_aday_dosyasi_kontrol_kolu_tasir():
    """Gerçek aday dosyası: kontrol kaydı olmadan probe'un 'sıfır bulgu' çıktısı
    yokluk mu arıza mı ayırt edilemez."""
    veri = json.loads(ADAYLAR.read_text(encoding="utf-8"))
    kararlar = {a["karar"] for a in veri["adaylar"]}
    assert "kontrol" in kararlar, "kontrol kolu yok — sıfır bulgu yorumlanamaz"
    for aday in veri["adaylar"]:
        assert aday["terimler"], f"{aday['id']}: terim yok, gövde taraması yapılamaz"

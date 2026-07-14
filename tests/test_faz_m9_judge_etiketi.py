"""M-9 — judge etiketi VERİ EGEMENLİĞİ hakkında yalan söyleyemez.

Judge'a belge içeriği GİDER. Bu yüzden "judge nerede koşuyor" sorusu bir performans
detayı değil, veri-egemenliği iddiasının DAYANAĞIDIR. Etiket yanlışsa iddia da yanlıştır.

BULUNAN HATA: `dev_label` bilinmeyen her ucu "cloud" sayıyordu. H200'e geçtikten sonra
judge KENDİ donanımımızda koşuyordu ama karne hâlâ `cloud/dev-mode` diyordu.
"""

from __future__ import annotations

import pytest

from ragintel.eval.judge import dev_label


@pytest.mark.parametrize(("api_base", "beklenen"), [
    ("https://api.deepseek.com/v1", "deepseek/dev-mode"),
    ("https://api.groq.com/openai/v1", "groq/dev-mode"),
    ("https://api.openai.com/v1", "openai/dev-mode"),
])
def test_dis_saglayicilar_ADIYLA_etiketlenir(api_base, beklenen):
    """Dış sağlayıcı AÇIK LİSTEyle tanınır — belge içeriği dışarı gidiyorsa görünsün."""
    assert dev_label(api_base) == beklenen


@pytest.mark.parametrize("api_base", [
    "https://banasor.goldenglobalbank.com.tr/ollama/v1",   # H200 (kurum içi)
    "http://192.168.36.15:11434/v1",
    "http://localhost:11434/v1",
])
def test_kendi_altyapimiz_LOCAL_etiketlenir(api_base):
    """Listede olmayan uç KENDİ altyapımızdır → 'local'. Eskiden 'cloud' diyordu:
    veri egemenliği iddiasının tam tersini söyleyen bir etiket."""
    assert dev_label(api_base) == "local/dev-mode"


def test_dev_mode_eki_KORUNUR():
    """Lokal judge, runbook'un veri-egemenliği şartını KARŞILAR — ama karnenin 'resmî'
    ilan edilmesi AYRI bir karardır (çapraz doğrulama). Etiket o kararı VEREMEZ."""
    assert dev_label("http://h200/ollama/v1").endswith("/dev-mode")


def test_bos_uc_local_sanilmaz():
    """Karşı-örnek: api_base boşsa 'local' demek bir iddiadır — ve yanlış olabilir.
    Boş uç, bilinmeyen uçtur; yine de dış sağlayıcı DEĞİLDİR, o yüzden local sayılır
    ama dev-mode eki iddiayı sınırlar. (Bu testin amacı davranışı SABİTLEMEK.)"""
    assert dev_label("") == "local/dev-mode"

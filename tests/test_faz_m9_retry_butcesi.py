"""M-9 — doğrulama-retry'si tool bütçesinden AYRI: tam 1 hak (Tasarim §9b revizyonu).

SÖZLEŞME İHLALİ (ölçüldü, temiz izole koşum): graph docstring'i ve Tasarim §1
"FAIL + retry hakkı → agent (feedback ile, retry=1)" vaat ediyordu. Ama `exhausted`
kontrolü `iteration >= max_iterations`'ı da kapsıyordu; tool turları (arama + lookup +
submit) `max_iterations=3` bütçesini zaten bitirdiği için validate FAIL ettiğinde retry
HİÇ ateşlenmiyordu → doğrudan fallback. Fallback'lerin 4/6'sı düzeltme turunu hiç görmedi.

Sonuç: kalite mekanizması (düzeltme turu), tool bütçesiyle aynı kasadan yiyerek aç kaldı.
Bu bir tuning sorunu DEĞİL (max_iterations tartışması M-10'da, kendi yerinde durur);
belgelenmiş tasarımın erişilebilir kılınmasıdır.
"""

from __future__ import annotations

import time

from ragintel.agents.graph import route_after_validate

_FAIL = {"passed": False, "coverage": 0.0, "issues": ["low_coverage:0.000"]}
_OK = {"passed": True, "coverage": 1.0, "issues": []}


def _state(*, iteration=3, max_iterations=3, tokens_used=5_000, max_tokens=16_000,
           retry_count=0, validation=None, deadline_ts=None):
    return {
        "validation": _FAIL if validation is None else validation,
        "retry_count": retry_count,
        "budget": {"iteration": iteration, "max_iterations": max_iterations,
                   "tokens_used": tokens_used, "max_tokens": max_tokens,
                   "deadline_ts": deadline_ts if deadline_ts is not None else time.time() + 60},
    }


# --- ASIL DÜZELTME ------------------------------------------------------------
def test_tool_butcesi_bitse_bile_retry_hakki_KULLANILIR():
    """REGRESYON KİLİDİ: tam da gerçek koşumdaki durum — iteration 3/3 (tool turları
    bütçeyi bitirmiş) + validate FAIL. Eskiden 'fallback' derdi; artık düzeltme turu."""
    assert route_after_validate(_state(iteration=3, max_iterations=3)) == "agent"


def test_retry_TEK_hak_sonsuz_dongu_YOK():
    """Karşı-örnek: hak kullanılmışsa (retry_count=1) ikinci düzeltme turu VERİLMEZ."""
    assert route_after_validate(_state(iteration=3, retry_count=1)) == "fallback"


# --- SERT DURDURUCULAR YERİNDE (retry sınırsız bir kaçış deliği DEĞİL) ---------
def test_token_butcesi_bittiyse_retry_YOK():
    """Token bütçesi SERT durdurucudur: retry hakkı onu delemez."""
    assert route_after_validate(
        _state(tokens_used=16_000, max_tokens=16_000)) == "fallback"


def test_deadline_gectiyse_retry_YOK():
    """Zaman aşımı da SERT durdurucudur — kullanıcı cevabı beklerken retry'ye girilmez."""
    assert route_after_validate(_state(deadline_ts=time.time() - 1)) == "fallback"


# --- DEĞİŞMEYEN DAVRANIŞ ------------------------------------------------------
def test_dogrulama_gectiyse_compose():
    """POZİTİF ÖN-KOŞUL: başarılı doğrulama hâlâ compose'a gider (retry değişikliği
    başarılı yolu bozmadı) — aksi hâlde yukarıdaki 'agent' iddiaları boş kalırdı."""
    assert route_after_validate(_state(validation=_OK)) == "compose"

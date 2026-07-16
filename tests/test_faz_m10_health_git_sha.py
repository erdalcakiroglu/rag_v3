"""M-10/0 — /api/health `git_sha`: KOŞAN kod hangi sürüm?

NEDEN: `deploy.sh` dağıttığı sürümü health'teki git_sha ile KIYASLIYOR. Bu alan
yanlış/eksikse dağıtım "başarılı" der ama konteyner eski (cache'li) imajı sunuyor
olabilir — sessiz sürüm kayması. Bu testler o kıyasın dayanağını sabitler.

Zemin metadata mantığının aynısı (M-7/M-9): "hangi kodla ölçtük/koştuk" görünmezse,
yanlış zemin sessizce doğru sanılır.
"""

from __future__ import annotations

from ragintel.api.runtime import RagRuntime


class _SahteRuntime:
    """health()'i gerçek bağımlılıklar (DB/Ollama) olmadan koşturmak için minimum
    ikame — testin konusu SÜRÜM DAMGASI, altyapı kontrolleri değil."""

    health = RagRuntime.health

    def __init__(self):
        from types import SimpleNamespace
        self.cfg = SimpleNamespace(group=lambda n: SimpleNamespace(health_timeout=1.0))
        self.langfuse = SimpleNamespace(enabled=False)
        self.is_warm = True

    def _check_db(self):
        return "ok"

    def _check_http(self, *a, **kw):
        return "ok"


def test_git_sha_ortamdan_okunur(monkeypatch):
    """Dockerfile ARG GIT_SHA → ENV → health. deploy.sh bu değeri kıyaslar."""
    monkeypatch.setenv("RAGINTEL_GIT_SHA", "abc1234")
    h = _SahteRuntime().health()
    assert h["git_sha"] == "abc1234"


def test_konteyner_disinda_unknown(monkeypatch):
    """Karşı-örnek: lokal koşumda damga YOK → 'unknown'. Uydurma bir sürüm
    döndürmek, yanlış bir dağıtım doğrulamasından beterdir."""
    monkeypatch.delenv("RAGINTEL_GIT_SHA", raising=False)
    assert _SahteRuntime().health()["git_sha"] == "unknown"


def test_git_sha_mevcut_alanlari_BOZMAZ(monkeypatch):
    """POZİTİF ÖN-KOŞUL: status/checks yerinde kalmalı — yoksa yukarıdaki
    git_sha iddiaları, bozuk bir health gövdesi üzerinde doğrulanmış olurdu."""
    monkeypatch.setenv("RAGINTEL_GIT_SHA", "xyz")
    h = _SahteRuntime().health()
    assert h["status"] in ("healthy", "degraded", "warming", "unhealthy")
    assert h["checks"]["db"] == "ok"
    assert "warmup" in h["checks"]

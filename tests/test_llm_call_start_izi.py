"""Asılı kalan LLM çağrısı iz bırakmalı — `llm_call_start` (çağrı ÖNCESİ boyut damgası).

NEDEN
    `llm_call_timing` yalnız çağrı BAŞARIYLA dönünce yazılır. Uçta asılı kalan istek
    hiçbir yerde görünmez; Ollama da kendi journal'ına satırı istek BİTİNCE düşürdüğü
    için iki tarafta da iz kalmaz. Ölçüldü (2026-08-13, M-17 koşumu): ajan Ollama'da
    kilitlendi, GPU %0'da bekledi, dakikalarca ne istemci ne sunucu tarafında tek satır
    üretildi — hangi çağrının, hangi tekrarında, ne büyüklükte bir istemle asıldığı
    BİLİNEMEDİ. Teşhis bu yüzden hipotez elemekle yürüdü.

    Damga çağrı ÖNCESİ basılır ve İÇERİK TAŞIMAZ: yalnız sayım (mesaj adedi, karakter
    toplamı, tool adedi, timeout). İstem gövdesi kullanıcı verisi taşır, loga girmez.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ragintel.config.settings import LiteLLMSettings
from ragintel.llm.gateway import LiteLLMGateway

GIZLI = "MUSTERI-TCKN-12345678901"


def _resp():
    msg = SimpleNamespace(
        tool_calls=None, content="ok",
        model_dump=lambda: {"role": "assistant", "content": "ok"})
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                           usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
                           _hidden_params={})


class _RecLog:
    """structlog PrintLogger stdlib'e gitmez → caplog yakalamaz; _LOG'u bununla değiştiririz."""
    def __init__(self, timeline): self.events = []; self.timeline = timeline
    def info(self, event, **kw):
        self.events.append((event, kw)); self.timeline.append(f"log:{event}")
    def warning(self, event, **kw): self.events.append((event, kw))
    def error(self, *a, **k): pass


def _kur(monkeypatch, davranislar, *, max_retries=0):
    """litellm.completion'u sahtele; log ile çağrı SIRASINI ortak zaman çizgisine yaz."""
    import litellm

    import ragintel.llm.gateway as gw

    timeline: list[str] = []
    rec = _RecLog(timeline)
    monkeypatch.setattr(gw, "_LOG", rec)
    monkeypatch.setattr("time.sleep", lambda _s: None)          # backoff'u gerçekten bekleme

    durum = {"i": 0}

    def sahte(**kwargs):
        timeline.append("call")
        b = davranislar[min(durum["i"], len(davranislar) - 1)]
        durum["i"] += 1
        if isinstance(b, BaseException):
            raise b
        return b

    monkeypatch.setattr(litellm, "completion", sahte)
    gw_obj = LiteLLMGateway(
        model="qwen3.5:35b",
        settings=LiteLLMSettings(api_base="http://x", provider="openai",
                                 max_retries=max_retries, toolcall_retries=0),
        temperature=0.0)
    return gw_obj, rec, timeline


def test_damga_cagridan_once_basilir(monkeypatch):
    """Sıra kritiktir: çağrı asılırsa SONRA basılan hiçbir satır yazılmaz. Damganın tek
    işi, dönmeyen çağrının varlığını kanıtlamaktır."""
    gw_obj, rec, timeline = _kur(monkeypatch, [_resp()])

    gw_obj.complete(messages=[{"role": "user", "content": GIZLI}], tools=[])

    assert timeline[0] == "log:llm_call_start"      # ÖNCE damga, SONRA çağrı
    assert timeline[1] == "call"

    (_, kw), = [e for e in rec.events if e[0] == "llm_call_start"]
    assert kw["messages"] == 1
    assert kw["prompt_chars"] == len(GIZLI)
    assert kw["attempt"] == 1


def test_damga_icerik_tasimaz(monkeypatch):
    """Boyut evet, gövde HAYIR: istem kullanıcı verisi taşır."""
    gw_obj, rec, _ = _kur(monkeypatch, [_resp()])

    gw_obj.complete(messages=[{"role": "system", "content": "sen bir ajansın"},
                              {"role": "user", "content": GIZLI}], tools=[])

    (_, kw), = [e for e in rec.events if e[0] == "llm_call_start"]
    assert GIZLI not in repr(kw)
    assert "ajansın" not in repr(kw)


def test_her_tekrar_ayri_damgalanir(monkeypatch):
    """Retry'ler de asılabilir (ölçülen kusurda üç deneme de 600 s'de düştü). Tek damga
    hangi denemenin asıldığını söyleyemez → her deneme kendi `attempt`'iyle basılır."""
    import litellm

    hata = litellm.Timeout(message="zaman aşımı", model="qwen3.5:35b", llm_provider="openai")
    gw_obj, rec, _ = _kur(monkeypatch, [hata, _resp()], max_retries=1)

    gw_obj.complete(messages=[{"role": "user", "content": "q"}], tools=[])

    damgalar = [kw for ev, kw in rec.events if ev == "llm_call_start"]
    assert [d["attempt"] for d in damgalar] == [1, 2]


def test_cagri_yerel_timeout_uca_gider(monkeypatch):
    """Bazı turların beklenen süresi ÖLÇÜLÜDÜR ve kilitte beklemenin bedeli o turda çok
    daha ağırdır (zorlanmış nihai tur: API tek `_lock` + seri Ollama → servis DURUR).
    Genel `request_timeout` tek sayıdır; çağrı-yerel override o turu kısar. Damga
    çağrıya giden kwargs'tan okunur, dolayısıyla uca giden değeri gösterir."""
    gw_obj, rec, _ = _kur(monkeypatch, [_resp()])

    gw_obj.complete(messages=[{"role": "user", "content": "q"}], tools=[], timeout=45.0)

    (_, kw), = [e for e in rec.events if e[0] == "llm_call_start"]
    assert kw["timeout"] == 45.0
    assert gw_obj.settings.request_timeout != 45.0     # ayardan değil, override'dan geldi


def test_asili_cagri_damgasi_kalir(monkeypatch):
    """Çağrı hiç dönmezse bile damga elde kalır — teşhisin dayandığı tek satır budur."""
    class _Asili(Exception):
        pass

    gw_obj, rec, timeline = _kur(monkeypatch, [_Asili("uc yanit vermedi")])

    with pytest.raises(_Asili):
        gw_obj.complete(messages=[{"role": "user", "content": "q"}], tools=[{"x": 1}])

    (_, kw), = [e for e in rec.events if e[0] == "llm_call_start"]
    assert kw["tools"] == 1 and kw["timeout"] == gw_obj.settings.request_timeout
    assert timeline == ["log:llm_call_start", "call"]   # timing satırı YOK, damga VAR

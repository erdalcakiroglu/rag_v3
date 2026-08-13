"""Zorlanmış nihai tur uçta kilitlenirse ajan 12 dakika asılı kalmamalı — kaçış yolu.

ÖLÇÜLEN OLGU (2026-08-13/14, qwen3.5:35b + Ollama 0.17.4, scripts/ollama_kilit_probe.py)
    Kilitlenen GERÇEK istek yakalanıp doğrudan uca oynatıldı:
        tools=1 (yalnız submit_answer) → 90 s TIMEOUT (dört bağımsız denemede dördü)
        tools=2 (submit + search)      → OK, 10.5 s
        tools=5 (tam liste)            → OK,  3.3 s
        tools=0 (serbest metin)        → OK,  3.7 s
        reasoning_effort kaldırıldı    → yine TIMEOUT
    İstem (~800 token) ve model sağlam; kilidi doğuran, uca TAM OLARAK BİR şema
    gönderilmesi. Bu tur canlı API'de de her sorunun sonunda koşar; kilit girdiğinde
    istek `request_timeout × (max_retries+1)` = 12 dakika asılı kalır ve API tek kilit
    + Ollama seri olduğu için o süre boyunca SERVİS DURUR.

NE KİLİTLENİYOR
    1. Mutlu yol DEĞİŞMEZ: tek şema + `max_retries=0` (M-9 karne mührü kayarsa
       ölçülmüş sayılar kıyaslanamaz hâle gelir).
    2. Taşıma hatasında kaçış: aynı mesajlar, TAM tool listesi (ölçülen çalışan yol).
    3. Şema/yetki hatasında kaçış YOK — hatayı gizlemek teşhisi öldürür.
    4. Kaçış bir kez ateşlendiyse aynı istek ikinci kez kilide sürülmez.
"""

from __future__ import annotations

import pytest

from ragintel.agents.nodes.agent import _forced_final_complete
from ragintel.agents.tools import ToolRegistry


class _SahteGateway:
    def __init__(self, davranislar):
        self.davranislar = davranislar
        self.cagrilar: list[dict] = []

    def complete(self, *, messages, tools, max_retries=None):
        self.cagrilar.append({"tools": len(tools), "max_retries": max_retries})
        b = self.davranislar[min(len(self.cagrilar) - 1, len(self.davranislar) - 1)]
        if isinstance(b, BaseException):
            raise b
        return b


def _kilit():
    import litellm
    return litellm.Timeout(message="90 s", model="qwen3.5:35b", llm_provider="ollama_chat")


def _kur(davranislar):
    return _SahteGateway(davranislar), ToolRegistry(None), [{"role": "user", "content": "q"}], {}


def test_mutlu_yol_degismez():
    """Kilit yoksa tek şema gider ve TEK çağrı yapılır — mühürlü davranış aynen."""
    gw, reg, msgs, budget = _kur(["YANIT"])

    assert _forced_final_complete(gw, reg, msgs, budget) == "YANIT"

    assert len(gw.cagrilar) == 1
    assert gw.cagrilar[0]["tools"] == 1               # yalnız submit_answer: yanıt ZORLANIR
    assert gw.cagrilar[0]["max_retries"] == 0         # kilitte tekrar anlamsız (ölçüldü 4/4)
    assert "forced_final_escaped" not in budget


def test_kilitte_tam_listeyle_kacar():
    """Taşıma hatası → aynı mesajlarla tam liste (ölçülen çalışan yol), yanıt KURTARILIR."""
    gw, reg, msgs, budget = _kur([_kilit(), "KACIS-YANITI"])

    assert _forced_final_complete(gw, reg, msgs, budget) == "KACIS-YANITI"

    assert [c["tools"] for c in gw.cagrilar] == [1, len(reg.llm_tool_schemas())]
    assert gw.cagrilar[1]["max_retries"] is None      # kaçış normal retry bütçesiyle koşar
    assert budget["forced_final_escaped"] is True


def test_kacis_bir_kez_ateslenir():
    """Doğrulama-retry'si aynı zorlanmış tura döner; işaret konmasaydı ikinci kilit
    bir timeout daha yakardı. İşaretliyse DOĞRUDAN tam liste gider."""
    gw, reg, msgs, budget = _kur(["YANIT"])
    budget["forced_final_escaped"] = True

    _forced_final_complete(gw, reg, msgs, budget)

    assert [c["tools"] for c in gw.cagrilar] == [len(reg.llm_tool_schemas())]


def test_sema_hatasi_gizlenmez():
    """Retry edilemeyen hata (şema/yetki) kaçışı TETİKLEMEZ — yükselir. Aksi hâlde
    gerçek bir arıza 'kaçış çalıştı' diye sessizce yutulur ve teşhis ölür."""
    gw, reg, msgs, budget = _kur([ValueError("bozuk şema")])

    with pytest.raises(ValueError):
        _forced_final_complete(gw, reg, msgs, budget)

    assert len(gw.cagrilar) == 1
    assert "forced_final_escaped" not in budget

"""M-9 — H200 geçişi: tel-etiket / kimlik-damgası ayrımı, Bearer auth, düşünen model.

Üç koruma:
  (b) `:latest` tel-etikete girer, DAMGAYA girmez. Damga (`bge-m3@ollama`) model
      KİMLİĞİDİR; değişirse korpustaki 1478 vektörle uyum kırılır ve gereksiz bir
      REPROCESS dayatılır.
  (c) Bearer başlığı auth'lu uçta (H200/Open WebUI) gönderilir, auth'suz uçta GÖNDERİLMEZ.
  (a) Düşünen model: `reasoning_effort` LiteLLM `extra_body` ile geçer (openai sağlayıcısı
      parametreyi doğrudan REDDEDER) + SESSİZ BOŞ CEVAP imkânsızlaşır.
"""

from __future__ import annotations

import pytest

from ragintel.ingestion.embedding.embedder import (
    OllamaEmbedder,
    model_stamp,
    ollama_tag,
    ollama_wire_tag,
)
from ragintel.llm.gateway import EmptyReasoningResponse, _assert_not_silently_empty


# =============================================================================
# (b) TEL-ETİKET vs KİMLİK-DAMGASI
# =============================================================================
def test_wire_tag_latest_ekler_damga_DEGISMEZ():
    """Open WebUI `bge-m3` adını tanımıyor (400), `bge-m3:latest` istiyor. Ama damga
    ETİKETTEN etkilenmemeli — yoksa `bge-m3:latest@ollama` olur ve korpus geçersizleşir."""
    assert ollama_wire_tag("BAAI/bge-m3") == "bge-m3:latest"   # isteğe giden
    assert ollama_tag("BAAI/bge-m3") == "bge-m3"               # kimlik
    assert model_stamp("BAAI/bge-m3") == "bge-m3@ollama"       # DB damgası — SABİT


def test_damga_korpusla_uyumlu_kalir():
    """REGRESYON KİLİDİ: damga değişirse `assert_corpus_model` patlar ve 1478 vektörün
    tamamı 'yabancı model' sayılır. Bu testin kırılması = sessiz reprocess dayatması.

    `:latest` DAMGAYA GİRMEZ — sürüm değil, 'varsayılan etiket' takma adıdır. (Bu açığı
    testi yazarken buldum: config'e `bge-m3:latest` yazan biri damgayı kaydırabiliyordu.)
    """
    for m in ("BAAI/bge-m3", "bge-m3", "bge-m3:latest", "BAAI/bge-m3:latest"):
        assert model_stamp(m) == "bge-m3@ollama", f"{m} damgası kaydı!"


def test_gercek_surum_etiketi_damgada_KALIR():
    """Karşı-örnek — `:latest` istisnası fazla geniş olmamalı: `bge-m3:v2` GERÇEKTEN
    başka bir modeldir. Onu `bge-m3` ile aynı damgaya indirmek iki farklı vektör uzayını
    aynı korpusta karıştırırdı (geri dönüşü reprocess)."""
    assert model_stamp("bge-m3:v2") == "bge-m3:v2@ollama"
    assert model_stamp("bge-m3:v2") != model_stamp("bge-m3")


def test_etiketli_ad_bozulmaz():
    """Zaten etiketli ad (agent modeli gibi) olduğu gibi kalır — çift etiket olmaz."""
    assert ollama_wire_tag("qwen3.5:35b") == "qwen3.5:35b"
    assert ollama_wire_tag("llama3.3:latest") == "llama3.3:latest"


# =============================================================================
# (c) BEARER AUTH
# =============================================================================
class _FakeClient:
    def __init__(self):
        self.headers = {}
        self.sent = None

    def post(self, url, json):
        self.sent = {"url": url, "json": json}

        class _R:
            status_code = 200

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"embeddings": [[0.0, 1.0]]}
        return _R()


def test_embedder_auth_varsa_bearer_gonderir(monkeypatch):
    captured = {}

    class _Httpx:
        @staticmethod
        def Client(timeout=None, headers=None):
            captured["headers"] = headers
            return _FakeClient()

    monkeypatch.setitem(__import__("sys").modules, "httpx", _Httpx)
    e = OllamaEmbedder("http://h200/ollama", model="BAAI/bge-m3", api_key="sk-test")
    _ = e.client
    assert captured["headers"] == {"Authorization": "Bearer sk-test"}


def test_embedder_auth_yoksa_baslik_GONDERMEZ(monkeypatch):
    """Geriye dönük uyum: auth'suz doğrudan Ollama'da boş Bearer göndermek 401 doğurabilir."""
    captured = {}

    class _Httpx:
        @staticmethod
        def Client(timeout=None, headers=None):
            captured["headers"] = headers
            return _FakeClient()

    monkeypatch.setitem(__import__("sys").modules, "httpx", _Httpx)
    e = OllamaEmbedder("http://ollama:11434", model="BAAI/bge-m3")   # anahtar YOK
    _ = e.client
    assert captured["headers"] == {}


def test_embedder_istekte_TEL_etiketi_kullanir():
    fake = _FakeClient()
    e = OllamaEmbedder("http://h200/ollama", model="BAAI/bge-m3", client=fake)
    e.embed_batch(["x"])
    assert fake.sent["json"]["model"] == "bge-m3:latest", "isteğe :latest gitmeli"
    assert e.model_name == "bge-m3@ollama", "damga yine de kimlik olmalı"


# =============================================================================
# (a) DÜŞÜNEN MODEL — sessiz boş cevap İMKÂNSIZ
# =============================================================================
class _Msg:
    def __init__(self, content=None, reasoning=None):
        self.content = content
        self.reasoning = reasoning


def test_toolcall_varken_bos_content_HATA_DEGIL():
    """EN KRİTİK: agent cevabı `submit_answer` TOOL argümanlarıyla teslim eder — bu yüzden
    NORMAL akışta content HER TURDA boştur ve reasoning doludur.

    Kural 'content boş + reasoning dolu → hata' diye yazılsaydı SAĞLIKLI sistemde her
    turda ateşlenirdi (ölçüldü: arama ve nihai cevap turlarında tool-call 5/5, content 0).
    Bu test o yanlış kapsamı kalıcı olarak dışarıda tutar.
    """
    _assert_not_silently_empty(_Msg(content="", reasoning="uzun düşünce..."),
                               tool_calls=[object()], model="qwen3.5:35b")   # patlamamalı


def test_metin_varken_hata_yok():
    _assert_not_silently_empty(_Msg(content="cevap", reasoning="düşünce"),
                               tool_calls=[], model="m")


def test_sadece_dusunup_hicbir_sey_uretmezse_ACIK_HATA():
    """GERÇEK arıza: ne araç çağrısı ne metin — yalnızca düşünce. Eskiden bu SESSİZCE
    boş cevaba dönüşürdü."""
    with pytest.raises(EmptyReasoningResponse) as exc:
        _assert_not_silently_empty(_Msg(content="", reasoning="sadece düşündüm"),
                                   tool_calls=[], model="qwen3.5:35b")
    assert "yalnızca düşündü" in str(exc.value)
    assert "reasoning_effort" in str(exc.value)      # eyleme dönük


def test_ne_dusunce_ne_cikti_varsa_bu_kural_KARISMAZ():
    """Karşı-örnek: reasoning de boşsa bu başka bir arızadır (üst katman ele alır);
    bu kural her boş yanıtı sahiplenmez."""
    _assert_not_silently_empty(_Msg(content="", reasoning=""), tool_calls=[], model="m")


def test_reasoning_effort_extra_body_ile_gecer(monkeypatch):
    """LiteLLM'in `openai` sağlayıcısı `reasoning_effort`'ü DOĞRUDAN reddeder
    (UnsupportedParamsError) → `extra_body` ŞART. Bu test o yolu kilitler."""
    import ragintel.llm.gateway as gw_mod
    from ragintel.config.settings import LiteLLMSettings

    captured = {}

    class _FakeLiteLLM:
        @staticmethod
        def completion(**kw):
            captured.update(kw)
            raise RuntimeError("dur")   # yanıt işlemeye gerek yok, çağrı gövdesi yeter

    monkeypatch.setitem(__import__("sys").modules, "litellm", _FakeLiteLLM)

    gw = gw_mod.LiteLLMGateway(model="qwen3.5:35b",
                               settings=LiteLLMSettings(api_base="http://h200", api_key="k", max_retries=0),
                               reasoning_effort="none")
    with pytest.raises(RuntimeError):
        gw.complete(messages=[{"role": "user", "content": "s"}], tools=[])
    assert captured["extra_body"] == {"reasoning_effort": "none"}
    assert "reasoning_effort" not in captured, "üst seviye parametre olarak GİTMEMELİ"


def test_default_ise_extra_body_EKLENMEZ(monkeypatch):
    """Karşı-örnek: 'default' iken hiçbir şey enjekte edilmez (modelin kendi davranışı)."""
    import ragintel.llm.gateway as gw_mod
    from ragintel.config.settings import LiteLLMSettings

    captured = {}

    class _FakeLiteLLM:
        @staticmethod
        def completion(**kw):
            captured.update(kw)
            raise RuntimeError("dur")

    monkeypatch.setitem(__import__("sys").modules, "litellm", _FakeLiteLLM)
    gw = gw_mod.LiteLLMGateway(model="m",
                               settings=LiteLLMSettings(api_base="http://x", max_retries=0),
                               reasoning_effort="default")
    with pytest.raises(RuntimeError):
        gw.complete(messages=[], tools=[])
    assert "extra_body" not in captured


def test_config_varsayilani_thinking_kapali():
    from ragintel.config.settings import AgentConfig
    assert AgentConfig().reasoning_effort == "none"

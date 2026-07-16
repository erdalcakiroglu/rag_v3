"""Bootstrap ayar kaynağı: `.env` ÖNCELİKLİ, OS ortamı YEDEK (M-4 PARÇA 3 + M-10/0).

ÖNCELİK: init kwarg > .env > os.environ > kod varsayılanı.

M-4'ün amacı: geliştirici makinesinde kalmış BAYAT bir `RAGINTEL_DB_PASSWORD`,
`.env`'i EZEMEZ. M-10/0: ama os.environ zincirin TAMAMEN dışındayken uygulama
konteynerde ayağa kalkamıyordu — imajda `.env` YOKTUR ve olmamalıdır (sır dosyası
imaja gömülmez). H200'de yaşandı; kanıt konteynerden:
    os.getenv('RAGINTEL_DB_HOST') -> '10.50.130.55'
    DbSettings().host             -> ''
Kararı mimar verdi: os.environ zincire eklendi, ama `.env`'in ARKASINA.

TESTLERİN ZEMİNİ — hepsi `monkeypatch.chdir(tmp_path)` ile İZOLE cwd'de koşar.
Gerekçe: `env_file=".env"` cwd'ye GÖRELİdir; repo kökünde geliştiricinin gerçek
`.env`'i durur ve testin neyi ölçtüğü makineden makineye değişirdi.

(Eski testler tam da bu yüzden SESSİZCE BOŞTU: `.env` yokken `DbSettings().password`
kod varsayılanı `''` döner ve `assert password != "BOGUS_OS_ENV_PW"` hiçbir şey
kanıtlamadan geçerdi. Boş test, olmayan testten beterdir — koruma sanılır.)

Davranışsal pipeline config bu katmandan etkilenmez (ayrı katman —
loader._collect_env_overrides).
"""

from __future__ import annotations

import pytest

from ragintel.config.settings import (
    DbSettings,
    LiteLLMSettings,
    MissingBootstrapSetting,
    OllamaSettings,
    TeiSettings,
)


def test_dotenv_VARSA_bayat_os_env_i_EZEMEZ(tmp_path, monkeypatch):
    """M-4 PARÇA 3'ün KORUNAN amacı: host'ta kalmış bayat bir değer `.env`'i ezmemeli.

    POZİTİF ÖN-KOŞUL: `.env` gerçekten OKUNDU (host == 'dotenv.example'). Bu
    doğrulanmadan "os env ezmedi" iddiası boş olurdu — kod varsayılanı da bayat
    değere eşit değildir, yani test hiçbir şey ölçmeden geçerdi.
    """
    (tmp_path / ".env").write_text(
        "RAGINTEL_DB_HOST=dotenv.example\n"
        "RAGINTEL_DB_USER=dotenv_user\n"
        "RAGINTEL_DB_PASSWORD=dotenv_pw\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RAGINTEL_DB_HOST", "bayat.host.invalid")
    monkeypatch.setenv("RAGINTEL_DB_PASSWORD", "BAYAT_PW")

    s = DbSettings()
    assert s.host == "dotenv.example"    # ← ön-koşul: .env OKUNDU
    assert s.password == "dotenv_pw"     # ← ve os.environ'u EZDİ (M-4 amacı sürüyor)


def test_dotenv_YOKSA_os_environ_OKUNUR(tmp_path, monkeypatch):
    """M-10/0 REGRESYONU (H200): konteynerde `.env` yoktur → bootstrap compose
    `environment:`/`env_file:`'dan gelmeli.

    Her alan KOD VARSAYILANINDAN FARKLI bir değerle sınanır. Gerekçe: `port=5432`
    veya `name='ragintel'` beklemek boş testtir — varsayılan da aynı değeri döner
    ve env hiç okunmasa bile assert geçerdi. (Bu tuzağı mimar canlı konteynerde
    fark etti: "port=5432 environment'ın okunduğunu göstermiyor".)
    """
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / ".env").exists()          # ön-koşul: .env gerçekten YOK

    monkeypatch.setenv("RAGINTEL_DB_HOST", "10.50.130.55")
    monkeypatch.setenv("RAGINTEL_DB_PORT", "6543")          # varsayılan 5432 DEĞİL
    monkeypatch.setenv("RAGINTEL_DB_NAME", "ragintel_alt")  # varsayılan 'ragintel' DEĞİL
    monkeypatch.setenv("RAGINTEL_DB_USER", "ragintel_app")
    monkeypatch.setenv("RAGINTEL_DB_PASSWORD", "test-secret")
    monkeypatch.setenv("RAGINTEL_DB_SCHEMA", "ragintel_alt")  # varsayılan 'ragintel' DEĞİL

    s = DbSettings()
    assert s.host == "10.50.130.55"
    assert s.port == 6543
    assert s.name == "ragintel_alt"
    assert s.user == "ragintel_app"
    assert s.password == "test-secret"
    assert s.schema_name == "ragintel_alt"           # alias: RAGINTEL_DB_SCHEMA

    # ASIL İDDİA: konteynerde patlayan çağrı artık patlamıyor.
    assert "host=10.50.130.55" in s.conninfo()


def test_dotenv_YOKSA_diger_bootstrap_siniflari_da_okur(tmp_path, monkeypatch):
    """DB tek başına yetmez: LLM/Ollama/TEI aynı tabandan gelir ve konteynerde
    hepsi compose'dan beslenir. Biri okumazsa konteyner yine ölür."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RAGINTEL_OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("RAGINTEL_TEI_RERANK_URL", "http://localhost:8085")
    monkeypatch.setenv("RAGINTEL_LLM_API_BASE", "http://localhost:11434/v1")

    assert OllamaSettings().base_url == "http://localhost:11434"
    assert TeiSettings().rerank_url == "http://localhost:8085"
    assert LiteLLMSettings().api_base == "http://localhost:11434/v1"


def test_init_kwarg_dotenv_i_de_os_env_i_de_YENER(tmp_path, monkeypatch):
    """Servis/test programatik enjeksiyonu her şeyi yenmeli (davranış değişmedi)."""
    (tmp_path / ".env").write_text(
        "RAGINTEL_TEI_RERANK_URL=http://dotenv:1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RAGINTEL_TEI_RERANK_URL", "http://os-env:9999")

    assert TeiSettings(rerank_url="http://init:1234").rerank_url == "http://init:1234"
    assert DbSettings(password="explicit").password == "explicit"


def test_hicbir_kanal_yoksa_ACIK_hata_ve_mesaj_KANALLARI_SAYAR(tmp_path, monkeypatch):
    """KARŞI-ÖRNEK: os.environ'u zincire almak, sessiz varsayılana düşmeyi
    MEŞRULAŞTIRMAZ — hiçbir kanal yoksa fail-fast sürüyor.

    Ayrıca mesaj İKİ kanalı da saymalı. M-10/0 dersi: eski metin yalnızca
    "(.env'de tanımlayın)" diyordu ve konteynerde arayanı saatlerce `.env.h200`/
    `env_file` tarafına baktırdı — oysa arıza os.environ'un okunmamasıydı. Eksik
    sayan bir hata mesajı, teşhisi kendisi saptırır.
    """
    monkeypatch.chdir(tmp_path)
    for k in ("RAGINTEL_DB_HOST", "RAGINTEL_DB_USER", "RAGINTEL_DB_PASSWORD"):
        monkeypatch.delenv(k, raising=False)

    with pytest.raises(MissingBootstrapSetting) as exc:
        DbSettings().conninfo()

    mesaj = str(exc.value)
    assert "RAGINTEL_DB_HOST" in mesaj
    assert ".env" in mesaj
    assert "ortam değişkeni" in mesaj

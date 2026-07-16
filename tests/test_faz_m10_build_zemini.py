"""M-10/0 — İMAJ/DAĞITIM ZEMİNİ: build'i ve açılışı kıran üç arızanın regresyonu.

Üçü de H200'de CANLI yakalandı, hiçbiri test paketinde görünmüyordu. Ortak
özellikleri: hepsi kodun DIŞINDA (`.dockerignore`, Dockerfile, compose, deploy.sh)
yaşıyordu ve "testler yeşil" derken imaj hiç ayağa kalkmıyordu.

  1. `.dockerignore` inline yorumu → README.md build context dışında → build kırık.
  2. transformers repo-id çağrısı → kapalı ağda OfflineModeIsEnabled → build kırık.
  3. compose'da makineye özgü DB adresi → `environment:` `env_file:`'ı ezer →
     canlı adres sessizce eskiye döner.

Bu dosya "kod dışı sözleşmeleri" test kapsamına alır: teslim edilen dağıtım
dosyaları birbirini ve kodu doğrulamadan yeşil sayılmasın.
"""

from __future__ import annotations

import inspect
import re
import subprocess
from pathlib import Path

import pytest

from ragintel.config.settings import DbSettings
from ragintel.ingestion.chunking.tokenizer import TOKENIZER_DIR_ENV, tokenizer_kaynagi

KOK = Path(__file__).resolve().parents[1]


def _desen_satirlari(ad: str) -> list[str]:
    """Yorum ve boş satırlar hariç, gerçek desen satırları."""
    metin = (KOK / ad).read_text(encoding="utf-8")
    return [s.strip() for s in metin.splitlines() if s.strip() and not s.strip().startswith("#")]


# ---------------------------------------------------------------- .dockerignore
def test_dockerignore_desen_satirlarinda_INLINE_YORUM_YOK():
    """Docker `.dockerignore`'da inline yorum TANIMAZ — `#` yalnızca satır başında.

    `!README.md   # açıklama` satırını Docker, adı tam olarak o metin olan bir
    dosyanın deseni sanar; istisna kurulmaz, README.md `*.md` tarafından dışlanmış
    kalır ve `COPY ... README.md` şu hatayla build'i kırar:
        failed to calculate checksum: "/README.md": not found
    (H200'de yaşandı.)
    """
    for i, satir in enumerate((KOK / ".dockerignore").read_text(encoding="utf-8").splitlines(), 1):
        s = satir.strip()
        if not s or s.startswith("#"):
            continue
        assert "#" not in s, (
            f".dockerignore:{i} desen satırında inline yorum var: {satir!r}. "
            "Docker bunu desenin PARÇASI sayar — yorumu satırın ÜSTÜNE taşıyın."
        )


def test_README_build_baglaminda_KALIR():
    """`!README.md` TAM eşleşme olmalı ve `*.md`'den SONRA gelmeli.

    Tam eşleşme şart: yorumlu hâli (`!README.md   # ...`) bu assert'i düşürür —
    yani tek başına bu test de yukarıdaki arızayı yakalardı.
    """
    desenler = _desen_satirlari(".dockerignore")
    assert "*.md" in desenler
    assert "!README.md" in desenler, (
        "`!README.md` istisnası TAM eşleşmiyor (yanında yorum mu var?) — README.md "
        "build context'e giremez ve build kırılır."
    )
    assert desenler.index("!README.md") > desenler.index("*.md"), (
        "İstisna `*.md`'den ÖNCE geliyor — sonraki kural onu ezer."
    )


def test_pyproject_README_ye_GERCEKTEN_bagimli():
    """POZİTİF ÖN-KOŞUL: yukarıdaki iki test, README.md build'de GEREKLİ olduğu için
    anlamlı. Gereksiz olsaydı `*.md` istisnası da gereksizdi ve testler boş olurdu."""
    metin = (KOK / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^\s*readme\s*=\s*"README\.md"', metin, re.M), (
        "pyproject `readme` alanı README.md'yi göstermiyor — .dockerignore istisnası "
        "gerekçesini kaybetti, iki testi de gözden geçirin."
    )
    assert (KOK / "README.md").is_file()


# ------------------------------------------------------------------- tokenizer
def test_env_yoksa_repo_id_ile_yuklenir(monkeypatch):
    """Lokal geliştirme yolu DEĞİŞMEDİ: env yoksa eski davranış (HF cache)."""
    monkeypatch.delenv(TOKENIZER_DIR_ENV, raising=False)
    kaynak, yerel = tokenizer_kaynagi("BAAI/bge-m3")
    assert kaynak == "BAAI/bge-m3"
    assert yerel is False


def test_env_varsa_YEREL_dizinden_ve_local_files_only(tmp_path, monkeypatch):
    """Konteyner yolu: repo-id çağrısı kapalı ağda OfflineModeIsEnabled veriyor
    (transformers 4.57.3'te ölçüldü; `local_files_only=True` de kurtarmıyor).
    Tek çalışan yol yerel dizin."""
    monkeypatch.setenv(TOKENIZER_DIR_ENV, str(tmp_path))
    kaynak, yerel = tokenizer_kaynagi("BAAI/bge-m3")
    assert kaynak == str(tmp_path)
    assert yerel is True


def test_env_var_ama_dizin_YOKSA_ACIK_hata(tmp_path, monkeypatch):
    """KARŞI-ÖRNEK — sessiz fallback YASAK: repo-id'ye düşseydik kapalı ağda
    anlaşılmaz bir OfflineModeIsEnabled alırdık ve arızayı tokenizer'da arardık.
    Asıl arıza imajın bozukluğudur; hata onu söylemeli."""
    monkeypatch.setenv(TOKENIZER_DIR_ENV, str(tmp_path / "yok"))
    with pytest.raises(RuntimeError) as exc:
        tokenizer_kaynagi("BAAI/bge-m3")
    assert TOKENIZER_DIR_ENV in str(exc.value)


def test_yerel_dizin_KIMLIGI_DEGISTIRMEZ(tmp_path, monkeypatch):
    """En sessiz felaket buydu: yükleme yolunu `embedding.model` otoritesine
    yazsaydık damga (`bge-m3@ollama`) kayardı ve korpustaki vektörler geçersiz
    olurdu. Kanal AYRI — kimlik argümanı geri döndürülmez, dokunulmaz."""
    from ragintel.ingestion.embedding.embedder import model_stamp

    monkeypatch.setenv(TOKENIZER_DIR_ENV, str(tmp_path))
    kaynak, _ = tokenizer_kaynagi("BAAI/bge-m3")
    assert kaynak != "BAAI/bge-m3"                       # yükleme yolu değişti
    assert model_stamp("BAAI/bge-m3") == "bge-m3@ollama"  # ama DAMGA sabit


def test_dockerfile_yerel_tokenizer_SOZLESMESI():
    """Dockerfile ile kod aynı dizinde buluşmalı. Env adı kodda değişirse bu test
    Dockerfile'ı da değiştirmeye ZORLAR (yoksa imaj sessizce repo-id'ye düşer)."""
    d = (KOK / "Dockerfile").read_text(encoding="utf-8")
    assert "save_pretrained('/opt/models/bge-m3-tokenizer')" in d, "builder yerel dizine yazmıyor"
    assert "COPY --from=builder /opt/models/bge-m3-tokenizer /opt/models/bge-m3-tokenizer" in d
    assert f"{TOKENIZER_DIR_ENV}=/opt/models/bge-m3-tokenizer" in d, (
        f"runtime imajı {TOKENIZER_DIR_ENV} vermiyor — tokenizer repo-id'ye düşer ve "
        "kapalı ağda ölür."
    )


# ------------------------------------------------------------- compose / deploy
def test_compose_MAKINEYE_OZGU_DB_degeri_TASIMAZ():
    """compose'da `environment:` her zaman `env_file:`'ı EZER. Buraya yazılan bir
    RAGINTEL_DB_*, `.env.h200`'deki canlı değeri sessizce eskiye döndürür —
    adres 192.168.36.15 → 10.50.130.55 taşınırken tam da bu yakalandı."""
    for satir in (KOK / "docker-compose.h200.yml").read_text(encoding="utf-8").splitlines():
        s = satir.strip()
        if not s or s.startswith("#"):
            continue
        assert not s.startswith("RAGINTEL_DB_"), (
            f"compose'da DB ataması var: {s!r} — bağlantı bilgisi .env.h200'e ait."
        )


def test_deploy_zorunlu_sirlar_KODLA_ESLESIR():
    """deploy.sh'ın ön-doğrulaması, kodun GERÇEKTEN zorunlu tuttuklarıyla aynı olmalı.

    Ayrışırsa iki yönde de yalan: fazla sayarsa var olmayan bir zorunluluk uydurup
    dağıtımı boşuna engeller (PORT/NAME/SCHEMA'nın kod varsayılanı VAR); eksik
    sayarsa konteyner açılışta ölür ve biz 180 sn health bekleriz.
    """
    kod_zorunlu = set(re.findall(r'_require\([^,]+,\s*"([^"]+)"\)',
                                 inspect.getsource(DbSettings.conninfo)))
    assert kod_zorunlu, "ön-koşul: conninfo() içinde _require çağrısı bulunamadı"

    m = re.search(r"ZORUNLU_SIRLAR=\(([^)]*)\)", (KOK / "deploy.sh").read_text(encoding="utf-8"))
    assert m, "deploy.sh'ta ZORUNLU_SIRLAR dizisi yok"
    deploy_zorunlu = set(m.group(1).split())

    assert deploy_zorunlu == kod_zorunlu, (
        f"deploy.sh listesi kodla ayrıştı: deploy={sorted(deploy_zorunlu)} "
        f"kod={sorted(kod_zorunlu)}"
    )


def test_deploy_taze_konteyner_zorlar():
    """`--force-recreate` olmadan compose konteyneri 'Started' deyip ESKİ env'le
    yeniden başlatabiliyor → dağıtım 'başarılı' der, eski ayar koşar."""
    assert "--force-recreate" in (KOK / "deploy.sh").read_text(encoding="utf-8")


# ------------------------------------------------------------------- güvenlik
def test_env_h200_git_tarafindan_IGNORE_EDILIYOR():
    """`.env.h200` DB parolasını ve Langfuse anahtarlarını taşır. `.gitignore`'daki
    `.env` deseni onu KAPSAMAZ (yalnızca tam adı `.env` olanı eşler) — desen
    eklenmeseydi tek `git add -A` ile sırlar repoya girerdi."""
    onkosul = subprocess.run(["git", "check-ignore", "-q", "README.md"], cwd=KOK)
    assert onkosul.returncode == 1, "ön-koşul: git check-ignore beklendiği gibi çalışmıyor"

    r = subprocess.run(["git", "check-ignore", "-q", ".env.h200"], cwd=KOK)
    assert r.returncode == 0, ".env.h200 git tarafından ignore EDİLMİYOR — sır sızma riski."


def test_dockerignore_sir_dosyalarini_disliyor():
    """İmaja sır girmemeli. (Dockerfile build doğrulaması ayrıca /app/.env yokluğunu
    assert eder — bu iki katman birbirini yedekler.)"""
    desenler = _desen_satirlari(".dockerignore")
    assert ".env" in desenler
    assert ".env.*" in desenler

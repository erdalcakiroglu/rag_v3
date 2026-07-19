"""Konfigürasyon modelleri: bootstrap ayarları (DB/log) ve pipeline grup
varsayılanları.

- `DbSettings` / `LogSettings` / … : bootstrap (bağlantı/secret) ayarları.
  KARAR: öncelik `init kwarg > .env > OS ortam değişkeni > kod varsayılanı`
  (bkz. `DotenvFirstSettings`). Bunlar app_config'ten OKUNMAZ (DB'ye bağlanmak
  için gerekliler — tavuk/yumurta).
- Pipeline grupları (`chunking`, `embedding`, `ingestion`): varsayılanları burada
  tanımlı; efektif değer öncelik zincirinden gelir (DB > ENV > varsayılan),
  bkz. `resolver.py` / `loader.py`. Varsayılanlar `FAZ1_Sema.sql` app_config
  seed'i ile birebir aynıdır.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

# Öncelik zincirinden yönetilen pipeline config grupları.
PIPELINE_GROUPS = ("chunking", "embedding", "ingestion", "quality", "injection", "retrieval",
                   "agent", "prompts", "pii", "eval_gates", "storage", "api", "eval", "auth")

# --------------------------------------------------------------------------
# M-5: alan metadata'sı MODELDE yaşar (tek doğruluk kaynağı).
#   - `description`  → admin UI'da alanın altındaki TR yardım metni.
#   - `json_schema_extra={"danger": ...}` → UI'da turuncu rozet. Bu alanı
#     değiştirmek MEVCUT veriyi geçersizleştirir: yalnız config yazmak yetmez,
#     korpus yeniden işlenmeli / index yeniden kurulmalıdır. Rozet metni burada
#     durur ki UI ile model ayrışamasın.
#   - `ge`/`le` → hem sunucuda doğrulama hem istemcide sayı girişi sınırı.
# UI şeması bunlardan OTOMATİK türer (bkz. config/ui_schema.py) — ayrı liste yok.
# --------------------------------------------------------------------------
DANGER_REPROCESS = "Değişiklik korpusun yeniden işlenmesini gerektirir"
DANGER_REINDEX = "Değişiklik vektör index'inin yeniden kurulmasını gerektirir"


class MissingBootstrapSetting(RuntimeError):
    """Zorunlu bootstrap ayarı tanımsız — fail-fast, sessiz varsayılan YOK."""


def _require(value: str, env_name: str) -> str:
    """M-4 PARÇA 3: iç altyapı bilgisi koda gömülmez. Boşsa AÇIK hata.

    Mesaj GEÇERLİ KANALLARIN HEPSİNİ sayar. M-10/0 dersi: eski metin yalnızca
    "(.env'de tanımlayın)" diyordu; konteynerde `.env` yoktur ve olmamalıdır, o
    yüzden bu mesaj arayanı saatlerce yanlış yere (compose env_file/`.env.h200`)
    baktırdı — gerçek arıza os.environ'un okunmamasıydı. Hata mesajı eksik
    sayarsa, teşhisi kendisi saptırır.
    """
    if not value:
        raise MissingBootstrapSetting(
            f"{env_name} tanımsız — `.env` dosyasında ya da ortam değişkeni olarak "
            f"tanımlayın (konteynerde: compose `environment:` / `env_file:`)."
        )
    return value


# --------------------------------------------------------------------------
# Bootstrap ayarları — bağlantı/secret katmanı.
#
# KARAR (M-4 PARÇA 3; M-10/0'da GENİŞLETİLDİ — mimar onayı):
#     init kwarg > .env > OS ortam değişkeni > kod varsayılanı
#
# M-4'ün AMACI korunuyor: host makinede kalmış BAYAT bir `RAGINTEL_DB_*`,
# `.env`'i EZEMEZ — çünkü `.env` zincirde ÖNDE. Değişen tek şey, `.env`'in
# BULUNMADIĞI durumda ne olacağı.
#
# NEDEN genişletildi (M-10/0, H200'de yaşandı): os.environ zincirin TAMAMEN
# dışındayken bootstrap konteynerde ULAŞILAMAZ hâle geliyordu. İmajda `.env`
# YOKTUR ve OLMAMALIDIR (sır dosyası imaja gömülmez — .dockerignore onu bilerek
# dışlar), dolayısıyla compose `environment:`/`env_file:` ile geçirilen
# `RAGINTEL_DB_HOST` okunmadı → MissingBootstrapSetting → konteyner hiç ayağa
# kalkamadı. Kanıt: konteynerde `os.getenv('RAGINTEL_DB_HOST')` doluyken
# `DbSettings().host == ''`.
#
# Özetle: `.env` VARSA tekel onundur; `.env` YOKSA (konteyner) ortam konuşur.
#
# (Davranışsal pipeline config DB'den gelir; onun env katmanı ayrıdır —
# bkz. loader._collect_env_overrides, bu değişiklikten etkilenmez.)
# --------------------------------------------------------------------------
class DotenvFirstSettings(BaseSettings):
    """Bootstrap ayar tabanı: `.env` ÖNCELİKLİ, OS ortamı YEDEK (.env-first).

    Ad M-10/0'da değişti (eski: `DotenvOnlySettings`). os.environ artık zincirde
    olduğu için "Only" demek config'i yalancı yapardı.
    """

    # populate_by_name: aliased alanlar (schema_name/json_logs/rerank_url) init kwarg'ı
    # ALAN ADIYLA da kabul etsin (servis/test programatik enjeksiyonu için). Alt
    # sınıfların model_config'i MRO boyunca bununla birleşir.
    model_config = SettingsConfigDict(populate_by_name=True)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # SIRA = ÖNCELİK. env_settings (os.environ) zincirde, ama dotenv_settings'in
        # ARKASINDA: `.env` varsa bayat host env'i ezemez (M-4 amacı); `.env` yoksa
        # — yani konteynerde — ortam okunur ve uygulama ayağa kalkar.
        return (init_settings, dotenv_settings, env_settings, file_secret_settings)


class DbSettings(DotenvFirstSettings):
    """PostgreSQL bağlantı ve pool ayarları (7d)."""

    # Ortam değişkeni adları RAG_v2 Proje Dokümanı 7d ile birebir (tek alt çizgi).
    model_config = SettingsConfigDict(
        env_prefix="RAGINTEL_DB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # M-4 PARÇA 3: gerçek host/kullanıcı/parola KOD DEFAULT'U DEĞİL — yalnızca .env.
    # (Dünkü push'un dersi: iç altyapı bilgisi repoya gömülmez.)
    host: str = ""
    port: int = 5432
    name: str = "ragintel"
    schema_name: str = Field(default="ragintel", alias="RAGINTEL_DB_SCHEMA")
    user: str = ""
    password: str = ""
    pool_min_size: int = 1
    pool_max_size: int = 8
    connect_timeout: int = 10
    connect_retries: int = 3          # İP-0: 3 deneme
    connect_backoff_base: float = 0.5  # İP-0: exponential backoff taban (sn)

    def conninfo(self) -> str:
        """psycopg conninfo string (search_path şemayı hedefler). Eksik ayarda AÇIK hata."""
        _require(self.host, "RAGINTEL_DB_HOST")
        _require(self.user, "RAGINTEL_DB_USER")
        _require(self.password, "RAGINTEL_DB_PASSWORD")
        return (
            f"host={self.host} port={self.port} dbname={self.name} "
            f"user={self.user} password={self.password} "
            f"connect_timeout={self.connect_timeout} "
            # public: pgvector uzantı tipleri (vector) burada — register_vector/COPY için gerekli.
            f"options=-csearch_path={self.schema_name},public"
        )

    def safe_summary(self) -> dict:
        """Şifreyi maskeleyen özet (log/CLI için)."""
        return {
            "host": self.host,
            "port": self.port,
            "name": self.name,
            "schema": self.schema_name,
            "user": self.user,
            "password": "***",
            "pool_min_size": self.pool_min_size,
            "pool_max_size": self.pool_max_size,
            "connect_timeout": self.connect_timeout,
            "connect_retries": self.connect_retries,
            "connect_backoff_base": self.connect_backoff_base,
        }


class LogSettings(DotenvFirstSettings):
    """structlog ayarları."""

    model_config = SettingsConfigDict(
        env_prefix="RAGINTEL_LOG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    level: str = "INFO"
    json_logs: bool = Field(default=True, alias="RAGINTEL_LOG_JSON")


class StorageSettings(DotenvFirstSettings):
    """Raw dosya deposu (İP-1). MVP: yerel dosya sistemi (7b; MinIO FAZ 2)."""

    model_config = SettingsConfigDict(
        env_prefix="RAGINTEL_STORAGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    root: str = "./var/storage"
    # İP-8: toplu ilk yükte HNSW index'i drop/recreate stratejisi (varsayılan kapalı).
    hnsw_bulk_reindex: bool = False


class ParsingSettings(DotenvFirstSettings):
    """Parse backend seçimi (İP-2). 'auto' = docling varsa docling, yoksa fallback."""

    model_config = SettingsConfigDict(
        env_prefix="RAGINTEL_PARSE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    backend: str = "auto"   # auto | docling | fallback


class OllamaSettings(DotenvFirstSettings):
    """Embedding backend (İP-7 / ADR-012): remote Ollama HTTP /api/embed."""

    model_config = SettingsConfigDict(
        env_prefix="RAGINTEL_OLLAMA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    base_url: str = ""          # M-4: iç host koda gömülmez → .env zorunlu
    # M-9: auth'lu Ollama ucu (H200 / Open WebUI proxy) → Bearer token. Boşsa başlık
    # GÖNDERİLMEZ (auth'suz doğrudan Ollama ile geriye dönük uyumlu).
    api_key: str = ""
    # M-4 PARÇA 1: model artık `embedding.model` (DB) otoritesinde. Bu alan yalnızca
    # bootstrap-fallback (DB/config erişilemezse). Boş bırakılması normaldir.
    model: str = ""
    timeout: float = 30.0
    retries: int = 3            # timeout/5xx için retry sayısı
    backoff_base: float = 0.5   # exponential backoff taban (sn)

    def require_base_url(self) -> str:
        return _require(self.base_url, "RAGINTEL_OLLAMA_BASE_URL")


class TeiSettings(DotenvFirstSettings):
    """TEI rerank backend ayarları (ADR-014). URL bootstrap katmanından gelir."""

    model_config = SettingsConfigDict(
        env_prefix="RAGINTEL_TEI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # M-4: iç host koda gömülmez. TEI OPSİYONEL (rerank_backend=passthrough varsayılan) —
    # boşsa health "disabled" der, hata fırlatmaz; `rerank_backend='tei'` ise kullanan taraf zorunlu kılar.
    rerank_url: str = Field(default="", alias="RAGINTEL_TEI_RERANK_URL")

    def require_rerank_url(self) -> str:
        return _require(self.rerank_url, "RAGINTEL_TEI_RERANK_URL")


class RedisSettings(DotenvFirstSettings):
    """M-10/0 EK: oturum-token cache'i için Redis bağlantısı. Bağlantı bilgisi (parola dahil)
    bootstrap katmanından — makineye özgü, .env.h200'de (repoda YOK). TEI ile aynı desen:
    OPSİYONEL — boşsa cache devre dışı, resolver DB'ye düşer (Bearer yolu regresyonsuz)."""

    model_config = SettingsConfigDict(
        env_prefix="RAGINTEL_REDIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # redis://:<parola>@localhost:6379/0  — boşsa cache YOK (opsiyonel hızlandırıcı).
    url: str = Field(default="", alias="RAGINTEL_REDIS_URL")


class LiteLLMSettings(DotenvFirstSettings):
    """FAZ 4 agent LLM bağlantısı (ADR-003: LiteLLM → Ollama). Bootstrap katmanı
    (yalnızca ENV); MODEL ADI burada DEĞİL — o config-first `agent.model`'den gelir.
    Prefix `RAGINTEL_LLM_`, pipeline grup tarayıcısıyla (RAGINTEL_AGENT_) çakışmaz."""

    model_config = SettingsConfigDict(
        env_prefix="RAGINTEL_LLM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    provider: str = "ollama_chat"   # litellm sağlayıcı öneki (auth'lu OpenAI-compat için 'openai')
    api_base: str = ""              # M-4: iç host koda gömülmez → .env zorunlu
    api_key: str = ""               # OpenAI-compat uç (ör. Open WebUI /ollama/v1) Bearer token'ı
    request_timeout: float = 120.0
    # Rate-limit/geçici hata için Retry-After'a uyan backoff'lu retry sayısı
    # (Groq free-tier TPM/günlük kap gözetimi; RAGINTEL_LLM_MAX_RETRIES).
    max_retries: int = 5
    # Provider-swap kolaylığı (ADR-003 ek): model adını .env'den de override et
    # (RAGINTEL_LLM_MODEL). Boşsa config-first DB app_config('agent').model kazanır.
    # Öncelik (build_default_runtime): OS env RAGINTEL_AGENT_MODEL > .env RAGINTEL_LLM_MODEL > DB.
    model: str = ""


class LangfuseSettings(DotenvFirstSettings):
    """OTel exporter ayarları (İP-2.2). Bootstrap katmanı: yalnızca ENV."""

    model_config = SettingsConfigDict(
        env_prefix="RAGINTEL_LANGFUSE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = ""
    public_key: str = ""
    secret_key: str = ""
    service_name: str = "ragintel-ingestion"
    export_timeout_ms: int = 3000
    schedule_delay_ms: int = 500
    max_queue_size: int = 2048
    max_export_batch_size: int = 512

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.public_key and self.secret_key)


# --------------------------------------------------------------------------
# Pipeline grup varsayılanları (FAZ1_Sema.sql app_config seed'i ile aynı)
# --------------------------------------------------------------------------
class ChunkingConfig(BaseModel):
    """Chunk'lama — TÜM alanları reprocess-gerektiren (mevcut chunk'lar bu ayarlarla
    üretildi; değiştirmek yalnız yeni belgeleri etkiler, korpus ayrışır)."""

    strategy: Literal["section", "paragraph", "sliding"] = Field(
        default="section",
        description="Chunk sınırlarının nasıl seçileceği. 'section': başlıklara göre böler "
                    "(başlık yoksa otomatik paragraph'a düşer) · 'paragraph': paragrafları "
                    "token sınırına kadar paketler · 'sliding': başlık/paragraf gözetmeden "
                    "kaydırmalı pencere. Belgeler başlıklıysa 'section' en iyi sonucu verir.",
        json_schema_extra={"danger": DANGER_REPROCESS},
    )
    max_tokens: int = Field(
        default=512, ge=1, le=2048,
        description="Bir chunk'ın azami token sayısı. Büyütmek bağlamı korur ama getirilen "
                    "parça kabalaşır (isabet düşer); küçültmek isabeti artırır, bağlamı böler. "
                    "Embedding modelinin penceresini (bge-m3: 8192) aşmamalı.",
        json_schema_extra={"danger": DANGER_REPROCESS},
    )
    overlap_tokens: int = Field(
        default=64, ge=0, le=512,
        description="Ardışık chunk'ların paylaştığı token sayısı — sınırda kesilen cümlenin "
                    "iki chunk'ta da bulunmasını sağlar. max_tokens'ın ~%10-20'si tipiktir; "
                    "büyütmek tekrarı ve depolama maliyetini artırır.",
        json_schema_extra={"danger": DANGER_REPROCESS},
    )
    min_tokens: int = Field(
        default=30, ge=0, le=512,
        description="Bu token sayısının altındaki parçalar chunk olarak üretilmez (başlık "
                    "artıkları, tek satırlık kırıntılar). Yükseltmek gürültüyü azaltır ama "
                    "kısa ama anlamlı pasajları (tanımlar, madde başları) eleyebilir.",
        json_schema_extra={"danger": DANGER_REPROCESS},
    )
    # M-1: tablo chunk'ı bu token'ı aşarsa satır-gruplarına bölünür (her alt-chunk
    # başlık satırını tekrar taşır). Eşik-altı tablolar AYNEN tek chunk (çoğunluk).
    table_subchunk_max_tokens: int = Field(
        default=512, ge=1, le=8192,
        description="Tablo bu token'ı aşarsa satır-gruplarına bölünür; her alt-parça tablo "
                    "başlık satırını tekrar taşır. Eşiğin altındaki tablolar tek chunk kalır "
                    "(çoğunluk). Bölmeyi tamamen kapatmak için çok büyük bir değer verin.",
        json_schema_extra={"danger": DANGER_REPROCESS},
    )


class EmbeddingConfig(BaseModel):
    """M-4 PARÇA 1: `normalize` alanı KALDIRILDI (bkz. embedder.l2_normalize).

    L2-normalize DAİMA uygulanır ve kapatılamaz — bu bir düğme değil, değişmezdir.
    Gerekçe: normalize'lı ve normalize'sız vektörlerin aynı korpusta karışması
    kosinüs benzerliğini bozar ve geri dönüşü reprocess'tir. Var olmayan düğme,
    yanlış çevrilemeyen düğmedir. (Eskiden alan vardı ama hiçbir yerde OKUNMUYORDU —
    config yalan söylüyordu.)

    `model` TEK OTORİTEDİR: HF repo id (tokenizer için zorunlu). Ollama etiketi ve
    `core_vectors.model_name` damgası bundan türer (embedder.ollama_tag/model_stamp).
    """

    model: str = Field(
        default="BAAI/bge-m3",
        description="Embedding modelinin HF repo id'si (tokenizer için zorunlu). TEK OTORİTE: "
                    "Ollama etiketi ve core_vectors.model_name damgası bundan türer. Değiştirmek "
                    "mevcut vektörleri geçersizleştirir — farklı modellerin vektörleri aynı uzayda "
                    "karşılaştırılamaz; korpus baştan embed edilmeli.",
        json_schema_extra={"danger": DANGER_REPROCESS},
    )
    dim: int = Field(
        default=1024, ge=1, le=4096,
        description="Vektör boyutu — SEÇİLEN MODELİN boyutuyla birebir aynı olmalı (bge-m3: 1024). "
                    "Uyumsuz değer DB'deki vector(1024) sütunuyla çakışır ve yazma başarısız olur.",
        json_schema_extra={"danger": DANGER_REPROCESS},
    )
    batch_size: int = Field(
        default=32, ge=1, le=256,
        description="Embedding sunucusuna istek başına gönderilen chunk sayısı. Yalnızca hızı ve "
                    "bellek kullanımını etkiler — üretilen vektörler değişmez, reprocess gerekmez. "
                    "Büyütmek GPU'da hızlandırır; zaman aşımı alıyorsanız küçültün.",
    )


class IngestionConfig(BaseModel):
    allowed_types: list[str] = Field(
        default_factory=lambda: ["pdf", "docx", "xlsx", "txt"],
        description="Yüklemeye izin verilen dosya uzantıları (noktasız, küçük harf). Listede "
                    "olmayan uzantı yükleme anında reddedilir.",
    )
    max_file_mb: int = Field(
        default=100, ge=1, le=2048,
        description="Tek dosya için azami boyut (MB). Aşan dosya yüklenmeden reddedilir.",
    )
    # İP-2: dosya başına parse zaman aşımı. DB app_config('ingestion')'da yoksa
    # bu default zincirden gelir; DB'de tanımlanırsa DB kazanır.
    parse_timeout_sec: int = Field(
        default=300, ge=1, le=3600,
        description="Dosya başına parse zaman aşımı (saniye). Aşılırsa dosya FAILED olur. "
                    "Taranmış/OCR'lı büyük PDF'ler uzun sürer — bu tip belgelerde yükseltin.",
    )
    # İP-10: PROCESSING'de bu süreden uzun takılı dosyalar açılışta RETRY'a çekilir.
    stuck_processing_minutes: int = Field(
        default=30, ge=0, le=1440,     # 0 = her PROCESSING dosyayı anında takılı say
        description="PROCESSING durumunda bu süreden uzun takılı kalan dosyalar (ör. servis "
                    "çökmesi) açılışta RETRY'a çekilir. parse_timeout_sec'ten belirgin şekilde "
                    "büyük olmalı, yoksa hâlâ işlenen dosyalar boşuna yeniden kuyruğa girer.",
    )
    # İP-10: eşzamanlı işleme (MVP sıralı; config'te hazır).
    ingest_parallelism: int = Field(
        default=1, ge=1, le=16,
        description="Aynı anda işlenen dosya sayısı. 1 = sıralı (MVP varsayılanı). Yükseltmek "
                    "embedding sunucusundaki yükü de doğru orantılı artırır.",
    )
    # M-4: FAILED dosya için azami yeniden deneme (eskiden orchestrator.MAX_RETRY sabiti).
    max_retry: int = Field(
        default=3, ge=0, le=10,
        description="Başarısız (FAILED) bir dosya için azami yeniden deneme sayısı. Bu sayı "
                    "aşılınca dosya kalıcı FAILED sayılır ve otomatik denenmez.",
    )
    # M-7: PDF/DOCX görsellerinin çıkarılıp dosya deposuna yazılması.
    figure_images: bool = Field(
        default=True,
        description="Belgelerdeki görseller (şekil/grafik) çıkarılıp dosya deposuna kaydedilsin mi? "
                    "Kapalıyken görseller yalnızca kayıt olarak (sayfa/başlık) tutulur, görüntü "
                    "saklanmaz ve kaynak panelinde gösterilemez. Ölçüldü: parse süresine "
                    "ölçülebilir maliyeti YOK; görsel başına ~10-20 KB disk.",
        json_schema_extra={"danger": DANGER_REPROCESS},
    )
    figure_image_scale: float = Field(
        default=2.0, ge=0.5, le=4.0,
        description="Görsel çözünürlük çarpanı (Docling images_scale). 1.0 = sayfa çözünürlüğü "
                    "(~10 KB/görsel, ekranda bulanık olabilir) · 2.0 = iki katı (~20 KB/görsel, "
                    "önerilen). Süreyi etkilemez, yalnızca diski ve okunabilirliği.",
        json_schema_extra={"danger": DANGER_REPROCESS},
    )


# --- Kalite skorlama (ADR-011 / Ek1). Defaultlar FAZ1_Sema_Ek1_Kalite.sql
#     seed'i ile BİREBİR; Ek1 uygulanınca DB değerleri kazanır. -----------------
class QualityWeights(BaseModel):
    """Kalite skorunun ağırlıkları — toplamı 1.0 olmalı (aksi hâlde skor 100 üzerinden okunamaz)."""

    parse: float = Field(default=0.35, ge=0.0, le=1.0,
                         description="Parse aşamasının kalite skoruna katkı ağırlığı.")
    clean: float = Field(default=0.20, ge=0.0, le=1.0,
                         description="Temizleme aşamasının kalite skoruna katkı ağırlığı.")
    chunk: float = Field(default=0.25, ge=0.0, le=1.0,
                         description="Chunk'lama aşamasının kalite skoruna katkı ağırlığı.")
    embed: float = Field(default=0.20, ge=0.0, le=1.0,
                         description="Embedding aşamasının kalite skoruna katkı ağırlığı.")

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> QualityWeights:
        total = self.parse + self.clean + self.chunk + self.embed
        if abs(total - 1.0) > 1e-6:      # float toleransı
            raise ValueError(
                f"Ağırlıkların toplamı 1.0 olmalı; şu an {total:.4f} "
                f"(parse={self.parse}, clean={self.clean}, chunk={self.chunk}, embed={self.embed}). "
                "Tek bir ağırlığı değiştirdiyseniz diğerlerini de düzeltin."
            )
        return self


class ParseThresholds(BaseModel):
    hard_fail_coverage: float = Field(
        default=0.50, ge=0.0, le=1.0,
        description="Metin çıkarma kapsamı bu oranın ALTINDAysa dosya FAILED olur (sert hata). "
                    "Taranmış/görüntü PDF'lerde kapsam düşüktür — OCR fallback bu yüzden var.",
    )
    hard_fail_garbage: float = Field(
        default=0.20, ge=0.0, le=1.0,
        description="Çıkarılan metnin bozuk/anlamsız karakter oranı bu değeri AŞARSA dosya FAILED "
                    "olur (kırık font/kodlama işareti).",
    )
    soft_flag_coverage: float = Field(
        default=0.85, ge=0.0, le=1.0,
        description="Kapsam bu oranın altındaysa dosya işlenmeye devam eder ama QC'de "
                    "'low_coverage' bulgusu açılır. hard_fail_coverage'tan büyük olmalı.",
    )

    @model_validator(mode="after")
    def _soft_above_hard(self) -> ParseThresholds:
        if self.soft_flag_coverage <= self.hard_fail_coverage:
            raise ValueError(
                f"soft_flag_coverage ({self.soft_flag_coverage}) > hard_fail_coverage "
                f"({self.hard_fail_coverage}) olmalı — yumuşak uyarı eşiği sert hata eşiğinin "
                "altına inerse uyarı hiç üretilemez (dosya zaten FAILED olur)."
            )
        return self


class CleanThresholds(BaseModel):
    hard_fail_retention: float = Field(
        default=0.60, ge=0.0, le=1.0,
        description="Temizleme sonrası metnin korunan oranı bu değerin altındaysa FAILED "
                    "(temizleme metnin çoğunu silmiş — kural fazla agresif).",
    )
    soft_flag_retention_low: float = Field(
        default=0.80, ge=0.0, le=1.0,
        description="Korunan oran bunun altındaysa 'low_retention' bulgusu (soft).",
    )
    soft_flag_retention_high: float = Field(
        default=0.999, ge=0.0, le=1.0,
        description="Korunan oran bunun ÜSTÜNDEyse 'no_cleaning_effect' bulgusu — temizleme "
                    "hiçbir şey yapmamış demektir (kural devre dışı kalmış olabilir).",
    )


class ChunkThresholds(BaseModel):
    soft_flag_truncated_ratio: float = Field(
        default=0.30, ge=0.0, le=1.0,
        description="max_tokens sınırına dayanıp kesilen chunk'ların oranı bunu aşarsa "
                    "'chunk_truncation_high' bulgusu — chunking.max_tokens küçük kalıyor olabilir.",
    )
    target_token_p95: int = Field(
        default=512, ge=1, le=4096,
        description="Chunk token dağılımının p95 hedefi (kalite skoru bu hedefe göre puanlar). "
                    "Genelde chunking.max_tokens ile aynı tutulur.",
    )


class EmbedThresholds(BaseModel):
    hard_fail_nan: int = Field(
        default=0, ge=0, le=100,
        description="Tolere edilen NaN'lı vektör sayısı. Bu sayıyı AŞARSA dosya FAILED. "
                    "0 = hiç tolerans yok (önerilen; NaN vektör aramayı sessizce bozar).",
    )
    # İP-7: doc-içi ortalama ikili kosinüs bu eşiği aşarsa embed_anomaly (soft).
    anomaly_cosine_high: float = Field(
        default=0.98, ge=0.0, le=1.0,
        description="Bir belgedeki chunk'ların ortalama ikili kosinüs benzerliği bunu aşarsa "
                    "'embed_anomaly' bulgusu — tüm chunk'lar birbirinin aynı çıkmış demektir "
                    "(embedder bozuk ya da belge tekrar eden şablondan ibaret).",
    )


class OcrFallback(BaseModel):
    enabled: bool = Field(
        default=True,
        description="Kapsam düşük çıktığında OCR ile yeniden parse denensin mi? Kapatmak taranmış "
                    "PDF'lerin doğrudan FAILED olması demektir.",
    )
    trigger_coverage_below: float = Field(
        default=0.50, ge=0.0, le=1.0,
        description="Metin kapsamı bu oranın altına düşerse OCR fallback tetiklenir.",
    )
    max_retry: int = Field(
        default=1, ge=0, le=5,
        description="Dosya başına azami OCR denemesi. OCR pahalıdır — yükseltmek ingest süresini "
                    "belirgin şekilde uzatır.",
    )


class QualityConfig(BaseModel):
    weights: QualityWeights = Field(default_factory=QualityWeights,
                                    description="Kalite skorunun aşama ağırlıkları (toplam 1.0).")
    parse: ParseThresholds = Field(default_factory=ParseThresholds,
                                   description="Parse aşamasının sert/yumuşak eşikleri.")
    clean: CleanThresholds = Field(default_factory=CleanThresholds,
                                   description="Temizleme aşamasının eşikleri.")
    chunk: ChunkThresholds = Field(default_factory=ChunkThresholds,
                                   description="Chunk'lama aşamasının eşikleri.")
    embed: EmbedThresholds = Field(default_factory=EmbedThresholds,
                                   description="Embedding aşamasının eşikleri.")
    ocr_fallback: OcrFallback = Field(default_factory=OcrFallback,
                                      description="Düşük kapsamda OCR ile yeniden parse davranışı.")
    # M-4: ingest raporunun parse başarı hedefi (eskiden report.PARSE_SUCCESS_TARGET sabiti).
    parse_success_target: float = Field(
        default=0.95, ge=0.0, le=1.0,
        description="Ingest raporunun parse başarı hedefi — rapor bu oranın altındaki partileri "
                    "hedefin altında sayar. Yalnızca raporlamayı etkiler, pipeline'ı durdurmaz.",
    )


# --- İP-4 injection taraması (kural tabanlı; llm-guard/torch YOK). Kalıplar
#     kodda varsayılan; app_config('injection') ile override edilebilir. ---------
_DEFAULT_INJECTION_PATTERNS = [
    # EN
    r"ignore\s+(all\s+|the\s+)?(previous|prior|above|earlier|preceding)\s+"
    r"(instructions?|prompts?|messages?|context|rules?)",
    r"disregard\s+(all\s+|the\s+|any\s+)?(previous|prior|above|earlier|preceding)",
    r"forget\s+(everything|all|(the\s+)?(previous|prior)\s+(instructions?|context))",
    r"you\s+are\s+now\s+(a|an|the)?\s*\w+",
    r"act\s+as\s+(a|an|if)\b",
    r"pretend\s+(to\s+be|you\s+are)\b",
    r"system\s+prompt\b",
    r"new\s+instructions?\s*[:\-]",
    r"override\s+(the\s+)?(system|instructions?|previous)",
    r"do\s+not\s+follow\s+(the\s+)?(previous|above|system)",
    # TR
    r"(önceki|yukarıdaki|tüm|bütün)\s+(talimatlar[ıi]?|komutlar[ıi]?|"
    r"yönergeleri?|kurallar[ıi]?)\s*(['’]?[ıi]?)?\s*"
    r"(yoksay|görmezden\s+gel|unut|dikkate\s+alma|umursama)",
    r"(bundan\s+sonra|artık)\s+(sen|şu\s+şekilde|şöyle)\b",
    r"rol(ün[üu])?\s+değiştir",
    r"sistem\s+(istemi|komutu|talimat[ıi]|mesaj[ıi])",
    r"yeni\s+talimat(lar)?\s*[:\-]",
    r"(şu\s+şekilde|şöyle|.+?\s+gibi)\s+davran",
    r"talimatlar[ıi]\s+unut",
]


def _validated_regexes(value: list[str], *, field: str) -> list[str]:
    """Her kalıbın DERLENEBİLİR bir regex olduğunu garanti eder. Bozuk regex DB'ye
    yazılırsa tarama/maskeleme çalışma-zamanında patlar (ya da sessizce atlanır) —
    hata, yazılırken ve alan-bazlı mesajla verilmeli."""
    for idx, pattern in enumerate(value):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"{field}[{idx}] geçersiz regex: {pattern!r} — {exc}") from exc
    return value


class InjectionConfig(BaseModel):
    enabled: bool = Field(
        default=True,
        description="Belge metninde prompt-injection taraması yapılsın mı? Kapatmak, yüklenen "
                    "belgedeki 'önceki talimatları yoksay' türü metinlerin agent'a bağlam olarak "
                    "sessizce ulaşması demektir.",
    )
    patterns: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_INJECTION_PATTERNS),
        description="Şüpheli sayılan regex kalıpları (TR+EN). Her satır bir Python regex'idir ve "
                    "büyük/küçük harf duyarsız eşleşir. Geçersiz regex kaydetmede reddedilir.",
    )
    hidden_unicode_min_count: int = Field(
        default=1, ge=1, le=100,
        description="Görünmez/sıfır-genişlikli unicode karakter sayısı bu değere ULAŞIRSA şüphe "
                    "işaretlenir (gizlenmiş talimat klasiği). 1 = ilk karakterde yakala.",
    )
    base64_min_run: int = Field(
        default=40, ge=1, le=1000,
        description="Bu uzunlukta kesintisiz base64 dizisi görülürse şüphe (kodlanmış talimat). "
                    "Düşürmek normal metindeki uzun kimlik/hash dizilerinde yanlış alarm üretir.",
    )
    homoglyph_min_count: int = Field(
        default=2, ge=1, le=100,
        description="Karışık-alfabe (Latin+Kiril gibi) sözcük sayısı bu değere ULAŞIRSA şüphe — "
                    "filtre atlatmak için benzer görünen harflerle yazılmış metin.",
    )

    @field_validator("patterns")
    @classmethod
    def _patterns_must_compile(cls, value: list[str]) -> list[str]:
        return _validated_regexes(value, field="patterns")


class RetrievalConfig(BaseModel):
    default_top_k: int = Field(
        default=10, ge=1, le=100,
        description="İstemci belirtmezse getirilecek chunk sayısı. Büyütmek bağlamı zenginleştirir "
                    "ama gürültüyü ve LLM maliyetini artırır.",
    )
    max_top_k: int = Field(
        default=20, ge=1, le=200,
        description="İstemcinin isteyebileceği azami chunk sayısı (üst sınır). default_top_k'dan "
                    "küçük olmamalı; daha büyük istekler bu değere kırpılır.",
    )
    vector_ef_search: int = Field(
        default=80, ge=1, le=1000,
        description="HNSW ARAMA-zamanı gezinme genişliği. Büyütmek isabeti (recall) artırır, "
                    "aramayı yavaşlatır. Index'i YENİDEN KURMAZ — anında etkilidir "
                    "(index BUILD parametreleri: Depolama sekmesi).",
    )
    hnsw_iterative_scan: Literal["off", "relaxed_order", "strict_order"] = Field(
        default="relaxed_order",
        description="Filtreli aramada (scope/dosya süzgeci) HNSW aday tükenmesini önler. "
                    "Kapalıyken index önce ef_search kadar aday bulur, süzgeç SONRA uygulanır — "
                    "adayların hepsi kullanıcının scope'u DIŞINDAysa sonuç BOŞ döner (belge var "
                    "ama getirilemez). 'relaxed_order': aday tükenirse index taramaya devam eder "
                    "(önerilen; ölçülen ek gecikme ~0) · 'strict_order': sıra garantisi daha katı, "
                    "daha yavaş · 'off': eski davranış — scope'lu sorgularda SONUÇ KAYBINA yol açar. "
                    "pgvector ≥ 0.8 gerektirir.",
    )
    lookup_window: int = Field(
        default=2, ge=0, le=20,
        description="Bir chunk'ın kaynağı görüntülenirken çevresinden kaç komşu chunk getirilir "
                    "(öncesi+sonrası). Alıntının bağlamını okunur kılar.",
    )
    hybrid_fusion: Literal["rrf", "weighted"] = Field(
        default="rrf",
        description="Vektör (dense) ve kelime (sparse) sonuçlarının nasıl birleştirileceği. "
                    "'rrf': sıralamaları birleştirir, skor ölçekleri farklı olsa da sağlamdır "
                    "(önerilen) · 'weighted': ham skorları ağırlıklarla toplar — dense/sparse "
                    "ağırlıklarını elle ayarlamak isterseniz.",
    )
    hybrid_rrf_k: int = Field(
        default=60, ge=1, le=1000,
        description="RRF sabiti: 1/(k+sıra). Yalnızca hybrid_fusion='rrf' iken kullanılır. "
                    "Küçültmek ilk sıraların ağırlığını sertçe artırır (60 literatür varsayılanı).",
    )
    hybrid_dense_weight: float = Field(
        default=1.0, ge=0.0, le=10.0,
        description="Vektör (anlamsal) skorunun ağırlığı. Yalnızca hybrid_fusion='weighted' iken "
                    "kullanılır. Eş anlamlı/parafraz sorgularda yükseltin.",
    )
    hybrid_sparse_weight: float = Field(
        default=1.0, ge=0.0, le=10.0,
        description="Kelime eşleşmesi skorunun ağırlığı. Yalnızca hybrid_fusion='weighted' iken "
                    "kullanılır. Kod/madde no/özel ad aramalarında yükseltin.",
    )
    hybrid_sparse_variant: Literal["simple", "unaccent", "trgm"] = Field(
        default="simple",
        description="Kelime aramasının çalışma biçimi. 'simple': hızlı, hazır index — aksana "
                    "duyarlı ('İsveç' ≠ 'isvec') · 'unaccent': aksanı yok sayar, ikisini eşler "
                    "(sorgu anında hesaplar, daha yavaş) · 'trgm': harf-üçlüsü benzerliği — "
                    "Türkçe ekleri ve yazım hatalarını tolere eder, en gevşek eşleşme.",
    )
    rerank_backend: Literal["passthrough", "tei"] = Field(
        default="passthrough",
        description="Getirilen chunk'ların yeniden sıralanması. 'passthrough': yeniden sıralama "
                    "YOK, hibrit sıra korunur · 'tei': TEI cross-encoder ile yeniden sıralar "
                    "(isabeti artırır, gecikme ekler). 'tei' seçilirse RAGINTEL_TEI_RERANK_URL "
                    "(.env) tanımlı olmalıdır, aksi hâlde arama hata verir.",
    )
    rerank_timeout_sec: float = Field(
        default=5.0, ge=0.0, le=120.0,
        description="Rerank servisi çağrısının zaman aşımı (saniye). Aşılırsa hibrit sıra kullanılır.",
    )
    rerank_retries: int = Field(
        default=1, ge=0, le=10,
        description="Rerank çağrısı için azami yeniden deneme sayısı (zaman aşımı/5xx).",
    )
    context_token_budget: int = Field(
        default=4000, ge=1, le=128000,
        description="LLM'e bağlam olarak verilecek azami token. Bütçe dolunca düşük skorlu "
                    "chunk'lar bağlama alınmaz. Modelin penceresini (agent.max_tokens ile birlikte) "
                    "aşmayacak şekilde ayarlanmalı.",
    )
    context_token_safety_margin: float = Field(
        default=1.1, ge=1.0, le=2.0,
        description="Token sayımı güvenlik çarpanı. Sayaç ile modelin gerçek tokenizer'ı birebir "
                    "aynı değildir; 1.1 = %10 pay bırak (bağlamın sessizce kırpılmasını önler).",
    )
    context_low_quality_threshold: float = Field(
        default=70.0, ge=0.0, le=100.0,
        description="Kalite skoru bu değerin altındaki belgelerden gelen chunk'lar bağlama "
                    "girerken düşük-kalite işaretiyle gider (100 üzerinden).",
    )

    @model_validator(mode="after")
    def _cross_field_rules(self) -> RetrievalConfig:
        if self.max_top_k < self.default_top_k:
            raise ValueError(
                f"max_top_k ({self.max_top_k}) < default_top_k ({self.default_top_k}) olamaz — "
                "üst sınır varsayılanın altına inerse her istek sessizce kırpılır."
            )
        # 'weighted' seçiliyken iki ağırlığın da 0 olması aramayı tamamen sıfırlar.
        if self.hybrid_fusion == "weighted" and self.hybrid_dense_weight == 0 and self.hybrid_sparse_weight == 0:
            raise ValueError(
                "hybrid_fusion='weighted' iken dense ve sparse ağırlıkların İKİSİ BİRDEN 0 olamaz — "
                "tüm skorlar 0 çıkar ve arama hiçbir şey döndürmez."
            )
        return self


class AgentConfig(BaseModel):
    max_iterations: int = Field(
        default=4, ge=1, le=20,
        description="Agent'ın bir soru için yapabileceği azami araç-çağrısı turu. Yükseltmek "
                    "karmaşık sorularda derinleşmeyi sağlar, gecikmeyi ve token maliyetini artırır; "
                    "sınıra dayanınca agent eldeki bağlamla cevaplar.",
    )
    max_tokens: int = Field(
        default=16000, ge=1, le=200000,
        description="LLM yanıtı için azami token. Modelin bağlam penceresini aşmamalı "
                    "(retrieval.context_token_budget ile birlikte düşünün).",
    )
    timeout_sec: int = Field(
        default=60, ge=1, le=900,
        description="Tek bir soru-cevap turunun azami süresi (saniye). Aşılırsa istek hata döner. "
                    "Büyük modeller ve çok turlu akıl yürütme daha uzun sürer.",
    )
    validation_coverage_threshold: float = Field(
        default=0.70, ge=0.0, le=1.0,
        description="Cevabın kaynakla desteklenme oranı bu eşiğin altındaysa cevap doğrulamayı "
                    "GEÇEMEZ (halüsinasyon koruması). Yükseltmek daha katı — 'bilmiyorum' oranı artar.",
    )
    confidence_high_coverage_threshold: float = Field(
        default=0.90, ge=0.0, le=1.0,
        description="Kaynak desteği bu oranı aşan cevaplar 'yüksek güven' olarak işaretlenir. "
                    "validation_coverage_threshold'dan büyük olmalı.",
    )
    # §4 faithful-paraphrase: quote birebir değilse, içerik-token'larının bu oranı
    # bağlamda geçmeli (halüsinasyonu eler, parafrazı kabul eder).
    validation_quote_overlap_threshold: float = Field(
        default=0.70, ge=0.0, le=1.0,
        description="Alıntı birebir değilse: alıntının içerik kelimelerinin bu oranı bağlamda "
                    "geçmelidir. Parafrazı kabul eder, uydurmayı eler. Yükseltmek birebir alıntıya "
                    "zorlar; düşürmek uydurma alıntı riskini artırır.",
    )
    # FAZ 5 validate v2: v1 PASS sonrası toplu LLM entailment (unsupported_claim /
    # overconfident_hypothetical). Varsayılan KAPALI (opt-in guardrail).
    validate_entailment: bool = Field(
        default=False,
        description="Kural tabanlı doğrulama geçtikten SONRA ikinci bir LLM ile 'bu iddia bağlamdan "
                    "çıkıyor mu?' denetimi yapılsın mı? Desteksiz iddiaları yakalar; her soruya ek "
                    "LLM çağrısı ve gecikme ekler (opt-in).",
    )
    validate_entailment_model: str = Field(
        default="",
        description="Entailment denetimini yapacak model. Boşsa .env'deki LLM modeli kullanılır. "
                    "PROD'da veri egemenliği gereği LOKAL bir model verilmelidir — aksi hâlde belge "
                    "içeriği dış sağlayıcıya gider.",
    )
    compose_followup_count: int = Field(
        default=2, ge=0, le=10,
        description="Cevabın sonunda önerilecek takip sorusu sayısı. 0 = öneri gösterme.",
    )
    # FAZ 4: agent LLM'i (config-first). system_prompt boşsa kod varsayılanı (prompts.py).
    model: str = Field(
        default="qwen2.5:3b",
        description="Cevabı üreten LLM (config-first: kod değişmeden değiştirilir). Modelin "
                    "sağlayıcıda yüklü/erişilebilir olması gerekir; bilinmeyen ad ilk soruda "
                    "çalışma-zamanı hatası verir.",
    )
    system_prompt: str = Field(
        default="",
        description="Agent'ın sistem prompt'u. Boş bırakılırsa Promptlar sekmesindeki aktif "
                    "sürüm, o da yoksa koddaki varsayılan kullanılır. Buraya yazmak Promptlar "
                    "sekmesindeki sürümlemeyi BAYPAS eder — sürümlü yönetim tercih edilir.",
        json_schema_extra={"multiline": True},
    )
    # M-9: qwen3.5:35b bir "düşünen" (reasoning) modeldir.
    reasoning_effort: Literal["none", "low", "medium", "high", "default"] = Field(
        default="none",
        description="Düşünen modellerde (ör. qwen3.5:35b) iç akıl yürütmenin derinliği. "
                    "'none': düşünme KAPALI — ölçüldü: çağrı başına ~%40 daha hızlı ve ~%50 daha az "
                    "token; cevabın kalitesini etkilemediği KANITLANMADI, karneyle izlenir. "
                    "'default': modelin kendi davranışı (düşünme açık). "
                    "NOT: agent cevabı `submit_answer` TOOL argümanlarıyla teslim eder, düz "
                    "metinle değil — bu yüzden düşünme kapalıyken de cevap yolu bozulmaz. "
                    "'default' dışındaki değerler LiteLLM `extra_body` ile geçirilir (openai "
                    "sağlayıcısı bu parametreyi doğrudan kabul etmez).",
    )
    # M-9: örnekleme sıcaklığı. Ölçüldü — fallback varyansının KÖK KAYNAĞI buydu.
    temperature: float = Field(
        default=0.0, ge=0.0, le=2.0,
        description="LLM örnekleme sıcaklığı. 0.0 = deterministik/tekrarlanabilir; "
                    "yükseltmek karne zeminini değiştirir ve fallback varyansı geri getirir. "
                    "Ölçüldü: sıcaklık set edilmeyince uç Ollama varsayılanına (0.8) düşüyordu; "
                    "aynı soru %0–%60 arası fallback veriyordu (mühür imkânsız). 0.0'da her soru "
                    "5/5 aynı sonuç. Grounded soru-cevapta 'yaratıcılık' değersiz, "
                    "tekrarlanabilirlik ise mühür şartı. NOT: retry turu determinizmden zarar "
                    "GÖRMEZ — girdi farklıdır (geri bildirim mesajı eklenir), model aynı çıktıyı "
                    "üretmez; çeşitlilik gerekirse o hedefli bir deney olur, global sıcaklık değil.",
    )
    # FAZ 7: API girdi uzunluk sınırı (soru karakter üst sınırı).
    max_question_chars: int = Field(
        default=2000, ge=1, le=100000,
        description="Kullanıcı sorusunun azami karakter uzunluğu; aşan istek reddedilir "
                    "(kaynak tüketimi koruması).",
    )

    @model_validator(mode="after")
    def _confidence_above_validation(self) -> AgentConfig:
        if self.confidence_high_coverage_threshold < self.validation_coverage_threshold:
            raise ValueError(
                f"confidence_high_coverage_threshold ({self.confidence_high_coverage_threshold}) "
                f"≥ validation_coverage_threshold ({self.validation_coverage_threshold}) olmalı — "
                "aksi hâlde doğrulamayı geçemeyen bir cevap 'yüksek güven' işaretlenebilir."
            )
        return self


class EvalGatesConfig(BaseModel):
    """FAZ 8 CI eval gate eşikleri — MÜHÜRLÜ KARNENİN ~%5 ALTI (regresyon yakalar,
    mükemmellik dayatmaz). Değişince gate davranışı değişir (config-first; DB otoriter).

    M-7 (2026-07-13): eşikler `docs/M7_Karne_v1.md`'ye kalibre edildi
    (agent=deepseek-v4-pro · judge=llama-3.3-70b-versatile · golden=v0.1 ·
    iterative_scan=relaxed_order). ESKİ karne (FAZ5, agent=qwen/qwen3-32b) ARŞİVDİR:
    zemini yeniden üretilemez (agent+korpus+retrieval semantiği değişti).
    Karne değişirse bu eşikler de yeniden kalibre EDİLMELİDİR — aksi hâlde gate,
    başka bir zeminin eşiğiyle ölçer ve sessizce yanlış karar verir.
    """

    honesty_min_ratio: float = Field(
        default=0.76, ge=0.0, le=1.0,
        description="Dürüstlük testi (cevabı olmayan soruya 'bilmiyorum' diyebilme) asgari geçme "
                    "oranı. CI eval bu oranın altındaysa BAŞARISIZ olur. M-9 karne: 5/5 = 1.00. "
                    "Metrik AYRIKTIR (5 soru): 0.76 eşiği hâlâ 4/5 şart koşar, 3/5 (0.60) düşer.",
    )
    faithfulness_min: float = Field(
        default=0.87, ge=0.0, le=1.0,
        description="Cevabın kaynağa sadakati için asgari eval skoru. Altındaysa CI gate düşer. "
                    "M-9 karne (ANSWERED-ONLY): 0.915 → eşik 0.87 (×0.95). Fallback'ler HARİÇ "
                    "ölçülür; fallback yapısal olarak 'sadıktır' ve şişirir.",
    )
    context_precision_min: float = Field(
        default=0.85, ge=0.0, le=1.0,
        description="Getirilen bağlamın isabeti için asgari eval skoru (ilgisiz chunk oranı). "
                    "Altındaysa CI gate düşer. M-9 karne (ANSWERED-ONLY): 0.895 → eşik 0.85 (×0.95).",
    )
    # M-9: sapkın teşviki kapatan HARD kontrol. Gate ESKİDEN şişkin 'overall'ı okuyordu →
    # sistem daha çok reddettikçe faithfulness YÜKSELİYOR, gate KOLAYLAŞIYORDU (gate'in
    # varlık amacının tersi). Fallback oranı artık ayrı bir HARD tavandır.
    fallback_rate_max: float = Field(
        default=0.25, ge=0.0, le=1.0,
        description="Cevaplanabilir soruya 'bulunamadı' (fallback) oranının ÜST sınırı. "
                    "M-9 karne: 5/30 = 0.167 → tavan 0.25 (~+0.08 tolerans). Aşılırsa CI gate "
                    "düşer: 'reddederek kaliteli görünme' sapkın teşvikini kapatır.",
    )


# M-3(c) tarih desenleri: sürüm dizesi FP'sine karşı sınır koruması içerir
# (soldaki/sağdaki nokta+rakam → daha uzun bir nokta ayraçlı dizinin parçası).
# Gruplar: dmy → (gün, ayraç, ay, yıl); iso → (yıl, ay, gün).
_DEFAULT_PII_DATE_DMY = r"(?<![\d.])(\d{1,2})([./])(\d{1,2})\2(\d{4})(?!\d)(?!\.\d)"
_DEFAULT_PII_DATE_ISO = r"(?<![\d.])(\d{4})-(\d{2})-(\d{2})(?!\d)(?!\.\d)"


class PiiConfig(BaseModel):
    """FAZ 6 P2: output PII maskeleme (KVKK temel seti). TCKN+tarih deterministik;
    custom_patterns ile genişletilebilir (app_config('pii')).

    M-4: tarih desenleri ve yıl aralığı artık config'ten (M-3 korumaları varsayılan).
    `date_dmy_pattern` 4 grup (gün, ayraç, ay, yıl), `date_iso_pattern` 3 grup
    (yıl, ay, gün) üretmelidir — grup sayısı bozulursa desen sessizce atlanır.
    """

    enabled: bool = Field(
        default=True,
        description="Cevap metnindeki kişisel veriler maskelensin mi? Kapatmak TCKN/tarih gibi "
                    "verilerin kullanıcıya ham hâlde dönmesi demektir (KVKK).",
    )
    mask_tckn: bool = Field(
        default=True,
        description="TC Kimlik No maskelensin mi? (11 hane + doğrulama algoritması — rastgele "
                    "11 haneli sayılar maskelenmez.)",
    )
    mask_dates: bool = Field(
        default=True,
        description="Tarihler maskelensin mi? Doğum tarihi sızıntısını önler; ancak mevzuat "
                    "tarihleri de (yürürlük, tebliğ) maskelenir — cevabın okunurluğunu düşürebilir.",
    )
    custom_patterns: list[str] = Field(
        default_factory=list,
        description="Ek maskeleme regex'leri (kurum sicil no, müşteri no…). Her satır bir Python "
                    "regex'i; eşleşen metin maskelenir. Geçersiz regex kaydetmede reddedilir.",
    )
    date_dmy_pattern: str = Field(
        default=_DEFAULT_PII_DATE_DMY,
        description="Gün/ay/yıl tarih regex'i. TAM 4 yakalama grubu üretmeli: (gün, ayraç, ay, yıl). "
                    "Grup sayısı bozulursa desen sessizce atlanır ve tarih maskelenmez.",
    )
    date_iso_pattern: str = Field(
        default=_DEFAULT_PII_DATE_ISO,
        description="ISO (yyyy-aa-gg) tarih regex'i. TAM 3 yakalama grubu üretmeli: (yıl, ay, gün). "
                    "Grup sayısı bozulursa desen sessizce atlanır.",
    )
    year_min: int = Field(
        default=1900, ge=1000, le=9999,
        description="Geçerli sayılan en küçük yıl. Bu aralığın dışındaki eşleşmeler tarih "
                    "sayılmaz — sürüm numarası/kod gibi dizilerin tarih sanılmasını önler.",
    )
    year_max: int = Field(
        default=2099, ge=1000, le=9999,
        description="Geçerli sayılan en büyük yıl. year_min'den büyük olmalı.",
    )

    @field_validator("custom_patterns")
    @classmethod
    def _custom_patterns_must_compile(cls, value: list[str]) -> list[str]:
        return _validated_regexes(value, field="custom_patterns")

    @model_validator(mode="after")
    def _cross_field_rules(self) -> PiiConfig:
        if self.year_max <= self.year_min:
            raise ValueError(f"year_max ({self.year_max}) > year_min ({self.year_min}) olmalı.")
        # Grup sayısı: maskeleme kodu bu gruplara göre yeniden kurar. Yanlış sayı
        # çalışma-zamanında deseni SESSİZCE atlatır (tarih maskelenmez) — yazarken yakala.
        for field, pattern, expected in (
            ("date_dmy_pattern", self.date_dmy_pattern, 4),
            ("date_iso_pattern", self.date_iso_pattern, 3),
        ):
            try:
                groups = re.compile(pattern).groups
            except re.error as exc:
                raise ValueError(f"{field} geçersiz regex: {exc}") from exc
            if groups != expected:
                raise ValueError(
                    f"{field} TAM {expected} yakalama grubu üretmeli, {groups} üretiyor — "
                    "yanlış grup sayısında desen çalışma-zamanında atlanır ve tarih maskelenmez."
                )
        return self


class StorageConfig(BaseModel):
    """M-4: HNSW index BUILD parametreleri (arama-zamanı `retrieval.vector_ef_search` ayrı).

    DİKKAT: bu değerleri değiştirmek mevcut index'i etkilemez — yeniden index gerektirir
    (DROP INDEX + create_vector_index). Aksi halde config ile gerçeklik ayrışır.
    """

    hnsw_m: int = Field(
        default=16, ge=2, le=100,
        description="HNSW grafiğinde düğüm başına bağlantı sayısı. Büyütmek isabeti (recall) "
                    "artırır; index'i büyütür ve kurulumu yavaşlatır. MEVCUT index'i etkilemez.",
        json_schema_extra={"danger": DANGER_REINDEX},
    )
    hnsw_ef_construction: int = Field(
        default=64, ge=4, le=1000,
        description="Index KURULURKEN taranan aday sayısı. Büyütmek daha kaliteli bir grafik "
                    "kurar (arama isabeti artar), kurulum süresini uzatır. Arama-zamanı karşılığı "
                    "retrieval.vector_ef_search'tür ve o anında etkilidir.",
        json_schema_extra={"danger": DANGER_REINDEX},
    )


class ApiConfig(BaseModel):
    """M-4: API dış çağrı zaman aşımları (eskiden runtime.py'de gömülü `timeout=8`)."""

    health_timeout: float = Field(
        default=8.0, ge=0.0, le=60.0,
        description="/health kontrolünde bağımlı servislere (embedding, rerank) yapılan çağrının "
                    "zaman aşımı (saniye). Aşılırsa servis 'erişilemiyor' raporlanır.",
    )
    feedback_timeout: float = Field(
        default=8.0, ge=0.0, le=60.0,
        description="Geri bildirim (feedback) yazımının zaman aşımı (saniye).",
    )


class EvalConfig(BaseModel):
    """M-4: eval harness çalışma ayarları (eşikler ayrı grupta: `eval_gates`).

    Dev/prod judge ayrımı artık DB'den yönetilir; kod default'u DEV değerleridir.
    """

    ctx_cap: int = Field(
        default=10, ge=1, le=100,
        description="Judge'a verilen azami bağlam parçası sayısı. Doğrudan judge maliyetini "
                    "belirler — büyütmek eval'i pahalılaştırır ve yavaşlatır.",
    )
    agent_model: str = Field(
        default="qwen/qwen3-32b",
        description="Eval koşumunda cevabı üreten model (ölçülen taraf). Karnenin hangi modele "
                    "ait olduğunu bu belirler; değiştirmek skorları kıyaslanamaz kılar.",
    )
    judge_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Cevapları puanlayan hakem model (ölçen taraf). Agent'tan güçlü olmalı; "
                    "değiştirmek geçmiş karnelerle kıyası bozar.",
    )
    # M-9: karnenin MÜHÜRLENDİĞİ sıcaklık zemini (agent.temperature'ın ölçüm-zamanı kaydı).
    # agent.temperature = ÜRETİMDE kullanılan; eval.agent_temperature = karnenin zemini.
    # İkisi ayrışırsa (biri sıcaklığı değiştirip gate koşarsa) skorlar kıyaslanamaz —
    # gate model-zemini kontrolü bunu yakalar (sapma → exit 2). agent_model/eval.agent_model
    # ayrımıyla BİREBİR simetrik.
    agent_temperature: float = Field(
        default=0.0, ge=0.0, le=2.0,
        description="Karnenin mühürlendiği örnekleme sıcaklığı zemini. Gate koşumundaki "
                    "agent.temperature bundan saparsa ölçüm karneyle kıyaslanamaz → exit 2 "
                    "(altyapı). Mühür sırasında agent.temperature ile aynı değere set edilir.",
    )


class PromptsConfig(BaseModel):
    """FAZ 5: DB-versiyonlu sistem prompt'ları. `agent_system` = {versiyon: gövde};
    `agent_system_active` aktif versiyonu seçer. Boş/eksikse kod varsayılanı (prompts.py)
    nihai fallback'tir. En küçük DB-versiyonlu mekanizma (yeni tablo/DDL yok — app_config
    grubu; bkz. IP23 §5b tasarım kararı)."""

    agent_system_active: str = Field(
        default="",
        description="Aşağıdaki sürümlerden hangisinin canlıda kullanılacağı. Boş bırakılırsa "
                    "koddaki varsayılan prompt kullanılır. Var olmayan bir sürüm adı seçilirse "
                    "kaydetme reddedilir.",
        json_schema_extra={"options_from": "agent_system"},
    )
    agent_system: dict[str, str] = Field(
        default_factory=dict,
        description="Sistem prompt'unun sürümleri: {sürüm adı: prompt gövdesi}. Eski sürüm silinmez, "
                    "yenisi eklenir — aktif sürümü değiştirerek anında geri dönebilirsiniz.",
    )

    @model_validator(mode="after")
    def _active_version_must_exist(self) -> PromptsConfig:
        """Aktif sürüm gerçekten tanımlı olmalı. Bu ÇAPRAZ-ALAN kuralıdır: tek alan
        yazılırken bile tüm grup yeniden doğrulanır (bkz. admin PATCH), böylece
        'aktif' var olmayan bir sürümü işaret edemez (agent sessizce koda düşerdi)."""
        if self.agent_system_active and self.agent_system_active not in self.agent_system:
            known = ", ".join(sorted(self.agent_system)) or "(hiç sürüm tanımlı değil)"
            raise ValueError(
                f"agent_system_active='{self.agent_system_active}' tanımlı bir sürüm değil. "
                f"Mevcut sürümler: {known}"
            )
        return self


class AuthConfig(BaseModel):
    """M-12: email+şifre self-kayıt politikası (panelden yönetilir — M-4/M-5 disiplini).

    Bu grup YALNIZCA self-kayıt/giriş akışını yönetir; Bearer/servis token yolu bundan
    bağımsızdır (config'e bakmadan çalışır — regresyonsuz)."""

    allowed_email_domains: list[str] = Field(
        default_factory=list,
        description="Self-kayda İZİN VERİLEN email alan-adları (ör. 'sirket.com'). Büyük/küçük "
                    "harf duyarsız. FAIL-CLOSED: liste BOŞSA hiçbir kayıt kabul edilmez (403) — "
                    "yeni kayıt açmak için önce buraya en az bir alan-adı ekleyin. Mevcut "
                    "admin/servis kullanıcıları token'la erişmeye devam eder (bu listeden etkilenmez).",
    )
    password_min_length: int = Field(
        default=12, ge=8, le=128,
        description="Self-kayıtta kabul edilen asgari şifre uzunluğu (karakter). Düşürmek zayıf "
                    "şifreye izin verir; kayıt-zamanı doğrulanır (mevcut şifreleri etkilemez).",
    )
    session_ttl_seconds: int = Field(
        default=28800, ge=60, le=604800,   # 8 saat; sınır 7 gün
        description="Login (email+şifre) oturumunun Redis'teki yaşam süresi (saniye) — SLIDING: "
                    "hareketsizlik zaman aşımı, mutlak değil (her istekte tazelenir). Değişiklik YENİ "
                    "oturumlardan itibaren geçerli (config startup'ta yüklenir → restart gerekir). "
                    "Redis restart = tüm oturumlar düşer, yeniden login (kalıcılık yok). Admin/servis "
                    "DB token'larını ETKİLEMEZ (onlar Redis'e bakmaz).",
    )


# group adı -> (model sınıfı)
GROUP_MODELS: dict[str, type[BaseModel]] = {
    "chunking": ChunkingConfig,
    "embedding": EmbeddingConfig,
    "ingestion": IngestionConfig,
    "quality": QualityConfig,
    "injection": InjectionConfig,
    "retrieval": RetrievalConfig,
    "agent": AgentConfig,
    "prompts": PromptsConfig,
    "pii": PiiConfig,
    "eval_gates": EvalGatesConfig,
    "storage": StorageConfig,
    "api": ApiConfig,
    "eval": EvalConfig,
    "auth": AuthConfig,
}


def default_pipeline_config() -> dict[str, dict]:
    """Tüm pipeline gruplarının kod varsayılanları (grup -> alan -> değer)."""
    return {name: model().model_dump() for name, model in GROUP_MODELS.items()}

"""Konfigürasyon modelleri: bootstrap ayarları (DB/log) ve pipeline grup
varsayılanları.

- `DbSettings` / `LogSettings` / … : bootstrap (bağlantı/secret) ayarları.
  KARAR: yalnızca `.env` (+ init kwarg + kod varsayılanı); OS ortam değişkenleri
  YOK SAYILIR (bkz. `DotenvOnlySettings`). Bunlar app_config'ten OKUNMAZ (DB'ye
  bağlanmak için gerekliler — tavuk/yumurta).
- Pipeline grupları (`chunking`, `embedding`, `ingestion`): varsayılanları burada
  tanımlı; efektif değer öncelik zincirinden gelir (DB > ENV > varsayılan),
  bkz. `resolver.py` / `loader.py`. Varsayılanlar `FAZ1_Sema.sql` app_config
  seed'i ile birebir aynıdır.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

# Öncelik zincirinden yönetilen pipeline config grupları.
PIPELINE_GROUPS = ("chunking", "embedding", "ingestion", "quality", "injection", "retrieval", "agent", "prompts", "pii")


# --------------------------------------------------------------------------
# Bootstrap ayarları — bağlantı/secret katmanı. KARAR: değerler YALNIZCA .env
# (+ init kwarg + kod varsayılanı) kaynaklarından gelir; OS ortam değişkenleri
# KASITLI olarak yok sayılır. Böylece host makinede kalmış bir `RAGINTEL_DB_*`
# (veya benzeri) ortam değişkeni `.env`'i EZEMEZ — bağlantı bilgisi .env'in
# tekelindedir. (Davranışsal pipeline config DB'den gelir; onun env katmanı
# ayrıdır — bkz. loader._collect_env_overrides, bu değişiklikten etkilenmez.)
# --------------------------------------------------------------------------
class DotenvOnlySettings(BaseSettings):
    """OS ortam değişkenlerini yok sayan bootstrap ayar tabanı (.env-authoritative)."""

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
        # env_settings (os.environ) KASITLI olarak dışarıda bırakıldı.
        # Öncelik: init kwarg > .env > secrets > kod varsayılanı.
        return (init_settings, dotenv_settings, file_secret_settings)


class DbSettings(DotenvOnlySettings):
    """PostgreSQL bağlantı ve pool ayarları (7d)."""

    # Ortam değişkeni adları RAG_v2 Proje Dokümanı 7d ile birebir (tek alt çizgi).
    model_config = SettingsConfigDict(
        env_prefix="RAGINTEL_DB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "192.168.36.15"
    port: int = 5432
    name: str = "ragintel"
    schema_name: str = Field(default="ragintel", alias="RAGINTEL_DB_SCHEMA")
    user: str = "ragintel_app"
    password: str = "ragintel_app"
    pool_min_size: int = 1
    pool_max_size: int = 8
    connect_timeout: int = 10
    connect_retries: int = 3          # İP-0: 3 deneme
    connect_backoff_base: float = 0.5  # İP-0: exponential backoff taban (sn)

    def conninfo(self) -> str:
        """psycopg conninfo string (search_path şemayı hedefler)."""
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


class LogSettings(DotenvOnlySettings):
    """structlog ayarları."""

    model_config = SettingsConfigDict(
        env_prefix="RAGINTEL_LOG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    level: str = "INFO"
    json_logs: bool = Field(default=True, alias="RAGINTEL_LOG_JSON")


class StorageSettings(DotenvOnlySettings):
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


class ParsingSettings(DotenvOnlySettings):
    """Parse backend seçimi (İP-2). 'auto' = docling varsa docling, yoksa fallback."""

    model_config = SettingsConfigDict(
        env_prefix="RAGINTEL_PARSE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    backend: str = "auto"   # auto | docling | fallback


class OllamaSettings(DotenvOnlySettings):
    """Embedding backend (İP-7 / ADR-012): remote Ollama HTTP /api/embed."""

    model_config = SettingsConfigDict(
        env_prefix="RAGINTEL_OLLAMA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    base_url: str = "http://banasor.goldenglobalbank.com.tr:11434"
    model: str = "bge-m3"
    timeout: float = 30.0
    retries: int = 3            # timeout/5xx için retry sayısı
    backoff_base: float = 0.5   # exponential backoff taban (sn)


class TeiSettings(DotenvOnlySettings):
    """TEI rerank backend ayarları (ADR-014). URL bootstrap katmanından gelir."""

    model_config = SettingsConfigDict(
        env_prefix="RAGINTEL_TEI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    rerank_url: str = Field(default="http://192.168.36.15:8085", alias="RAGINTEL_TEI_RERANK_URL")


class LiteLLMSettings(DotenvOnlySettings):
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
    api_base: str = "http://banasor.goldenglobalbank.com.tr:11434"
    api_key: str = ""               # OpenAI-compat uç (ör. Open WebUI /ollama/v1) Bearer token'ı
    request_timeout: float = 120.0
    # Rate-limit/geçici hata için Retry-After'a uyan backoff'lu retry sayısı
    # (Groq free-tier TPM/günlük kap gözetimi; RAGINTEL_LLM_MAX_RETRIES).
    max_retries: int = 5
    # Provider-swap kolaylığı (ADR-003 ek): model adını .env'den de override et
    # (RAGINTEL_LLM_MODEL). Boşsa config-first DB app_config('agent').model kazanır.
    # Öncelik (build_default_runtime): OS env RAGINTEL_AGENT_MODEL > .env RAGINTEL_LLM_MODEL > DB.
    model: str = ""


class LangfuseSettings(DotenvOnlySettings):
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
    strategy: str = "section"
    max_tokens: int = 512
    overlap_tokens: int = 64
    min_tokens: int = 30


class EmbeddingConfig(BaseModel):
    model: str = "BAAI/bge-m3"
    dim: int = 1024
    batch_size: int = 32          # İstek başına chunk sayısı (ADR-012: Ollama)
    normalize: bool = True


class IngestionConfig(BaseModel):
    allowed_types: list[str] = Field(default_factory=lambda: ["pdf", "docx", "xlsx", "txt"])
    max_file_mb: int = 100
    # İP-2: dosya başına parse zaman aşımı. DB app_config('ingestion')'da yoksa
    # bu default zincirden gelir; DB'de tanımlanırsa DB kazanır.
    parse_timeout_sec: int = 300
    # İP-10: PROCESSING'de bu süreden uzun takılı dosyalar açılışta RETRY'a çekilir.
    stuck_processing_minutes: int = 30
    # İP-10: eşzamanlı işleme (MVP sıralı; config'te hazır).
    ingest_parallelism: int = 1


# --- Kalite skorlama (ADR-011 / Ek1). Defaultlar FAZ1_Sema_Ek1_Kalite.sql
#     seed'i ile BİREBİR; Ek1 uygulanınca DB değerleri kazanır. -----------------
class QualityWeights(BaseModel):
    parse: float = 0.35
    clean: float = 0.20
    chunk: float = 0.25
    embed: float = 0.20


class ParseThresholds(BaseModel):
    hard_fail_coverage: float = 0.50
    hard_fail_garbage: float = 0.20
    soft_flag_coverage: float = 0.85


class CleanThresholds(BaseModel):
    hard_fail_retention: float = 0.60
    soft_flag_retention_low: float = 0.80
    soft_flag_retention_high: float = 0.999


class ChunkThresholds(BaseModel):
    soft_flag_truncated_ratio: float = 0.30
    target_token_p95: int = 512


class EmbedThresholds(BaseModel):
    hard_fail_nan: int = 0
    # İP-7: doc-içi ortalama ikili kosinüs bu eşiği aşarsa embed_anomaly (soft).
    anomaly_cosine_high: float = 0.98


class OcrFallback(BaseModel):
    enabled: bool = True
    trigger_coverage_below: float = 0.50
    max_retry: int = 1


class QualityConfig(BaseModel):
    weights: QualityWeights = Field(default_factory=QualityWeights)
    parse: ParseThresholds = Field(default_factory=ParseThresholds)
    clean: CleanThresholds = Field(default_factory=CleanThresholds)
    chunk: ChunkThresholds = Field(default_factory=ChunkThresholds)
    embed: EmbedThresholds = Field(default_factory=EmbedThresholds)
    ocr_fallback: OcrFallback = Field(default_factory=OcrFallback)


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


class InjectionConfig(BaseModel):
    enabled: bool = True
    patterns: list[str] = Field(default_factory=lambda: list(_DEFAULT_INJECTION_PATTERNS))
    hidden_unicode_min_count: int = 1     # gizli unicode ≥ bu -> şüphe
    base64_min_run: int = 40              # bu uzunlukta base64 çalışması -> şüphe
    homoglyph_min_count: int = 2          # karışık-script sözcük ≥ bu -> şüphe


class RetrievalConfig(BaseModel):
    default_top_k: int = 10
    max_top_k: int = 20
    vector_ef_search: int = 80
    lookup_window: int = 2
    hybrid_fusion: str = "rrf"
    hybrid_rrf_k: int = 60
    hybrid_dense_weight: float = 1.0
    hybrid_sparse_weight: float = 1.0
    hybrid_sparse_variant: str = "simple"
    rerank_backend: str = "passthrough"
    rerank_timeout_sec: float = 5.0
    rerank_retries: int = 1
    context_token_budget: int = 4000
    context_token_safety_margin: float = 1.1
    context_low_quality_threshold: float = 70.0


class AgentConfig(BaseModel):
    max_iterations: int = 4
    max_tokens: int = 16000
    timeout_sec: int = 60
    validation_coverage_threshold: float = 0.70
    confidence_high_coverage_threshold: float = 0.90
    # §4 faithful-paraphrase: quote birebir değilse, içerik-token'larının bu oranı
    # bağlamda geçmeli (halüsinasyonu eler, parafrazı kabul eder).
    validation_quote_overlap_threshold: float = 0.70
    # FAZ 5 validate v2: v1 PASS sonrası toplu LLM entailment (unsupported_claim /
    # overconfident_hypothetical). Varsayılan KAPALI (opt-in guardrail). Judge modeli
    # boşsa LiteLLMSettings.model (dev=deepseek-v4-flash) kullanılır; bağlantı
    # RAGINTEL_LLM_* (.env). PROD'da lokal judge ZORUNLU (veri egemenliği — runbook).
    validate_entailment: bool = False
    validate_entailment_model: str = ""
    compose_followup_count: int = 2
    # FAZ 4: agent LLM'i (config-first). Dev'de CPU Ollama'daki küçük model;
    # H200 gelince ör. "qwen3.5:35b" — DB/ENV (RAGINTEL_AGENT_MODEL) ile değişir,
    # kod değişmez. system_prompt boşsa kod varsayılanı kullanılır (prompts.py).
    model: str = "qwen2.5:3b"
    system_prompt: str = ""
    # FAZ 7: API girdi uzunluk sınırı (soru karakter üst sınırı).
    max_question_chars: int = 2000


class PiiConfig(BaseModel):
    """FAZ 6 P2: output PII maskeleme (KVKK temel seti). TCKN+tarih deterministik;
    custom_patterns ile genişletilebilir (app_config('pii'))."""

    enabled: bool = True
    mask_tckn: bool = True
    mask_dates: bool = True
    custom_patterns: list[str] = Field(default_factory=list)


class PromptsConfig(BaseModel):
    """FAZ 5: DB-versiyonlu sistem prompt'ları. `agent_system` = {versiyon: gövde};
    `agent_system_active` aktif versiyonu seçer. Boş/eksikse kod varsayılanı (prompts.py)
    nihai fallback'tir. En küçük DB-versiyonlu mekanizma (yeni tablo/DDL yok — app_config
    grubu; bkz. IP23 §5b tasarım kararı)."""

    agent_system_active: str = ""
    agent_system: dict[str, str] = Field(default_factory=dict)


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
}


def default_pipeline_config() -> dict[str, dict]:
    """Tüm pipeline gruplarının kod varsayılanları (grup -> alan -> değer)."""
    return {name: model().model_dump() for name, model in GROUP_MODELS.items()}

"""M-5 — Admin Config UX: şema-güdümlü, alan-bazlı düzenleme.

Ana iddia: form ŞEMASI pydantic `GROUP_MODELS`'ten TÜRER; ayrı bir "alan → widget"
listesi yoktur. Bunun kanıtı `test_new_pydantic_field_auto_appears_in_ui_schema`
(export-seed testinin UI eşi): modele sahte bir alan eklenince şemada kendiliğinden
görünür — hiçbir UI kodu değişmeden.

Alan-bazlı yazımın iki koruması burada test edilir:
  1. Tek alan yazılırken bile TÜM GRUP yeniden doğrulanır (çapraz-alan).
  2. Modelde tanımlı OLMAYAN yol reddedilir (jsonb_set hayalet anahtar yaratmasın).
"""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel, Field, ValidationError

from ragintel.api.config_errors import field_errors
from ragintel.config.settings import (
    GROUP_MODELS,
    AgentConfig,
    ChunkingConfig,
    PiiConfig,
    PromptsConfig,
    QualityConfig,
    RetrievalConfig,
    StorageConfig,
)
from ragintel.config.ui_schema import UnknownField, build_ui_schema, resolve_field


def _leaves(fields: list[dict]) -> dict[str, dict]:
    """Şemadaki yaprak alanları 'a.b' anahtarıyla düzleştirir."""
    out: dict[str, dict] = {}
    for f in fields:
        if f["widget"] == "section":
            for key, sub in _leaves(f["fields"]).items():
                out[f"{f['name']}.{key}"] = sub
        else:
            out[f["name"]] = f
    return out


# --- 1) ŞEMA-GÜDÜMLÜLÜK (kabul: sahte alan → UI şemasında otomatik) ------------
def test_new_pydantic_field_auto_appears_in_ui_schema():
    class FakeAgent(BaseModel):
        max_iterations: int = Field(default=4, ge=1, le=20, description="mevcut alan")
        brand_new_experimental_field: Literal["a", "b"] = Field(
            default="a", description="TR yardım", json_schema_extra={"danger": "TEST ROZETİ"}
        )

    schema = build_ui_schema({**GROUP_MODELS, "agent": FakeAgent})
    leaves = _leaves(schema["groups"]["agent"]["fields"])

    new = leaves["brand_new_experimental_field"]
    assert new["widget"] == "select"            # Literal → dropdown (otomatik)
    assert new["options"] == ["a", "b"]
    assert new["description"] == "TR yardım"    # yardım metni modelden
    assert new["danger"] == "TEST ROZETİ"       # rozet modelden
    assert new["default"] == "a"


def test_every_group_reachable_from_some_tab():
    """Hiçbir grup sekmelerden düşmez — yeni grup eklenirse 'Diğer'e gider (fail-visible)."""
    schema = build_ui_schema()
    tabbed = {g for tab in schema["tabs"] for g in tab["groups"]}
    assert tabbed == set(GROUP_MODELS), f"sekmesiz grup: {set(GROUP_MODELS) - tabbed}"


def test_unassigned_group_falls_into_other_tab():
    class Newbie(BaseModel):
        x: int = 1

    schema = build_ui_schema({**GROUP_MODELS, "brand_new_group": Newbie})
    other = [t for t in schema["tabs"] if t["id"] == "other"]
    assert other and "brand_new_group" in other[0]["groups"]


# --- 2) WIDGET ÇIKARIMI (tip → widget; elle liste yok) ------------------------
@pytest.mark.parametrize(
    ("group", "field", "widget"),
    [
        ("retrieval", "hybrid_sparse_variant", "select"),      # Literal
        ("retrieval", "hybrid_fusion", "select"),
        ("retrieval", "rerank_backend", "select"),
        ("chunking", "strategy", "select"),
        ("injection", "enabled", "toggle"),                    # bool
        ("storage", "hnsw_m", "number"),                       # int
        ("api", "health_timeout", "number"),                   # float
        ("embedding", "model", "text"),                        # str
        ("agent", "system_prompt", "textarea"),                # str + multiline
        ("injection", "patterns", "string_list"),              # list[str]
        ("pii", "custom_patterns", "string_list"),
        ("prompts", "agent_system", "key_text"),               # dict[str,str]
        ("prompts", "agent_system_active", "select"),          # options_from
    ],
)
def test_widget_inferred_from_type(group: str, field: str, widget: str):
    leaves = _leaves(build_ui_schema()["groups"][group]["fields"])
    assert leaves[field]["widget"] == widget


def test_nested_model_becomes_section():
    fields = build_ui_schema()["groups"]["quality"]["fields"]
    sections = {f["name"] for f in fields if f["widget"] == "section"}
    assert {"weights", "parse", "clean", "chunk", "embed", "ocr_fallback"} <= sections
    # alt alanlar yolunu taşır (alan-bazlı PATCH bu yolu kullanır)
    weights = next(f for f in fields if f["name"] == "weights")
    parse = next(f for f in weights["fields"] if f["name"] == "parse")
    assert parse["path"] == ["weights", "parse"]


def test_bounds_and_options_reach_the_client():
    leaves = _leaves(build_ui_schema()["groups"]["storage"]["fields"])
    assert leaves["hnsw_m"]["min"] == 2 and leaves["hnsw_m"]["max"] == 100
    assert leaves["hnsw_m"]["step"] == 1                     # int → tam sayı adımı
    api = _leaves(build_ui_schema()["groups"]["api"]["fields"])
    assert api["health_timeout"]["step"] == "any"            # float → serbest adım


# --- 3) İÇERİK: TR yardım metni ve danger rozetleri --------------------------
def test_every_field_has_turkish_help_text():
    """Yardım metni MODELDE yaşar (tek kaynak). Yeni alan açıklamasız eklenemez."""
    schema = build_ui_schema()
    missing = [
        f"{g}.{name}"
        for g, gs in schema["groups"].items()
        for name, f in _leaves(gs["fields"]).items()
        if not (f.get("description") or "").strip()
    ]
    assert not missing, f"TR açıklaması olmayan alanlar: {missing}"


def test_danger_badges_on_reprocess_fields_only():
    """Reprocess/reindex gerektiren alanlar rozetli; ötekiler DEĞİL (rozet enflasyonu
    rozeti anlamsızlaştırır — batch_size'ı değiştirmek korpusu geçersizleştirmez)."""
    schema = build_ui_schema()
    flagged = {
        f"{g}.{name}"
        for g, gs in schema["groups"].items()
        for name, f in _leaves(gs["fields"]).items()
        if f.get("danger")
    }
    assert flagged == {
        "chunking.strategy", "chunking.max_tokens", "chunking.overlap_tokens",
        "chunking.min_tokens", "chunking.table_subchunk_max_tokens",
        "embedding.model", "embedding.dim",
        "storage.hnsw_m", "storage.hnsw_ef_construction",
        # M-7: görseller parse anında çıkarılır → değiştirmek yeniden işleme gerektirir
        # (mevcut belgelerin görselleri geriye dönük OLUŞMAZ).
        "ingestion.figure_images", "ingestion.figure_image_scale",
    }
    # Karşı-örnekler: bunlar mevcut veriyi geçersizleştirmez → rozet OLMAMALI.
    leaves = {g: _leaves(gs["fields"]) for g, gs in schema["groups"].items()}
    assert not leaves["embedding"]["batch_size"].get("danger")
    assert not leaves["retrieval"]["vector_ef_search"].get("danger")


# --- 4) YOL ÇÖZÜMLEME (uydurma anahtar DB'ye yazılmaz) -----------------------
def test_resolve_field_accepts_real_paths():
    assert resolve_field(QualityConfig, ["weights", "parse"]).description
    assert resolve_field(StorageConfig, ["hnsw_m"]).description


@pytest.mark.parametrize("path", [
    ["uydurma_alan"],                 # yok
    ["weights", "uydurma"],           # iç-içe yok
    ["parse_success_target", "x"],    # yaprağın altına inilemez
    [],                               # boş yol
])
def test_resolve_field_rejects_unknown_paths(path: list[str]):
    with pytest.raises(UnknownField):
        resolve_field(QualityConfig, path)


# --- 5) ÇAPRAZ-ALAN: tek alan değişse de TÜM GRUP doğrulanır -----------------
def test_out_of_range_number_is_rejected_with_turkish_field_error():
    """Kabul: hnsw_m=400 alan-hatasıyla reddedilir (le=100)."""
    with pytest.raises(ValidationError) as exc:
        StorageConfig(hnsw_m=400, hnsw_ef_construction=64)
    errors = field_errors(exc.value)
    assert errors[0]["field"] == "hnsw_m"
    assert errors[0]["message"] == "Değer en fazla 100 olmalı."


def test_bad_enum_choice_is_rejected():
    with pytest.raises(ValidationError) as exc:
        RetrievalConfig(hybrid_sparse_variant="bm25")     # Literal dışı
    errors = field_errors(exc.value)
    assert errors[0]["field"] == "hybrid_sparse_variant"
    assert "İzin verilen değerler" in errors[0]["message"]


def test_quality_weights_must_sum_to_one():
    with pytest.raises(ValidationError) as exc:
        QualityConfig(weights={"parse": 0.9, "clean": 0.2, "chunk": 0.25, "embed": 0.2})
    msg = field_errors(exc.value)[0]["message"]
    assert "toplamı 1.0 olmalı" in msg          # mesaj modelde yazıldı → zaten TR


def test_cross_field_rules_reject_incoherent_groups():
    with pytest.raises(ValidationError):
        RetrievalConfig(default_top_k=10, max_top_k=5)          # üst sınır < varsayılan
    with pytest.raises(ValidationError):
        AgentConfig(validation_coverage_threshold=0.9,
                    confidence_high_coverage_threshold=0.5)      # güven < doğrulama eşiği
    with pytest.raises(ValidationError):
        PiiConfig(year_min=2050, year_max=1900)                  # ters yıl aralığı
    with pytest.raises(ValidationError):
        PromptsConfig(agent_system_active="v9", agent_system={"v1": "x"})  # yok olan sürüm


def test_invalid_regex_is_rejected_before_db():
    """Açıklama 'geçersiz regex reddedilir' diyor — kod da öyle yapmalı (config yalan söylemez)."""
    with pytest.raises(ValidationError) as exc:
        PiiConfig(custom_patterns=["(unclosed"])
    assert "geçersiz regex" in field_errors(exc.value)[0]["message"]
    with pytest.raises(ValidationError):
        PiiConfig(date_dmy_pattern=r"(\d{2})-(\d{2})")          # 4 grup değil → sessiz atlanırdı


def test_valid_edits_still_pass():
    """Vacuous olmasın: yasak olanı reddeden şema, GEÇERLİ değişikliği kabul etmeli."""
    assert StorageConfig(hnsw_m=32).hnsw_m == 32
    assert RetrievalConfig(hybrid_sparse_variant="trgm").hybrid_sparse_variant == "trgm"
    assert ChunkingConfig(strategy="paragraph").strategy == "paragraph"
    assert PromptsConfig(agent_system_active="v2", agent_system={"v2": "gövde"}).agent_system_active == "v2"

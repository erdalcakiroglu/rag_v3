"""M-4 — Config bütünlüğü: yalan alan sıfırlama + tunable taşıma + altyapı temizliği.

PARÇA 1: embedding.normalize kalktı (yalan alandı); embedding.model tek otorite;
         MODEL_STAMP türetilir; korpus damga uyuşmazlığı AÇIK hata (ADR-012 mekanik).
PARÇA 2: storage/api/eval/pii/ingestion/quality tunable'ları DB'den.
PARÇA 3: settings.py'de gerçek host/parola YOK; eksikse açık hata.
"""

from __future__ import annotations

import pytest

from ragintel.config.loader import load_config
from ragintel.config.settings import (
    GROUP_MODELS,
    PIPELINE_GROUPS,
    DbSettings,
    EmbeddingConfig,
    LiteLLMSettings,
    MissingBootstrapSetting,
    OllamaSettings,
    TeiSettings,
)
from ragintel.guardrails.pii import PiiPolicy, mask_pii
from ragintel.ingestion.embedding import model_stamp, ollama_tag

# ---------------------------------------------------------------------------
# PARÇA 1 — yalan alanlar
# ---------------------------------------------------------------------------
def test_normalize_field_is_gone():
    """`normalize` hiç okunmuyordu → düğme tamamen kaldırıldı (footgun yok)."""
    assert "normalize" not in EmbeddingConfig.model_fields


def test_normalize_is_unconditional_invariant():
    """L2-normalize DAİMA uygulanır — config'ten kapatılamaz (değişmez)."""
    from ragintel.ingestion.embedding import l2_normalize
    v = l2_normalize([3.0, 4.0])
    assert abs(sum(x * x for x in v) - 1.0) < 1e-9


def test_ollama_tag_and_stamp_derive_from_single_authority():
    assert ollama_tag("BAAI/bge-m3") == "bge-m3"
    assert ollama_tag("bge-m3") == "bge-m3"            # zaten etiketse değişmez
    assert model_stamp("BAAI/bge-m3") == "bge-m3@ollama"


def test_stamp_matches_existing_corpus_format():
    """KRİTİK: türetilen damga M-4 ÖNCESİ yazılmış korpusun damgasıyla birebir aynı.

    Naif `f"{model}@ollama"` olsaydı 'BAAI/bge-m3@ollama' üretir ve yeni uyuşmazlık
    koruması mevcut korpusa yazımı tamamen bloklardı (backfill zorunlu olurdu).
    """
    assert model_stamp(EmbeddingConfig().model) == "bge-m3@ollama"


def test_model_change_changes_stamp():
    """Damga artık sabit DEĞİL: model değişince köken bilgisi de değişir."""
    assert model_stamp("BAAI/bge-large") == "bge-large@ollama"
    assert model_stamp("BAAI/bge-large") != model_stamp("BAAI/bge-m3")


class _FakeConn:
    def __init__(self, stamps):
        self._stamps = stamps

    def execute(self, *_a, **_k):
        rows = [(s,) for s in self._stamps]
        return type("R", (), {"fetchall": lambda _self: rows})()


def test_corpus_model_mismatch_raises():
    from ragintel.database.storage_repo import CorpusModelMismatch, assert_corpus_model
    # Boş korpus → her damga kabul.
    assert_corpus_model(_FakeConn([]), "bge-m3@ollama")
    # Aynı damga → sorun yok.
    assert_corpus_model(_FakeConn(["bge-m3@ollama"]), "bge-m3@ollama")
    # Farklı damga → AÇIK hata (ADR-012 mekanik olarak zorlanır).
    with pytest.raises(CorpusModelMismatch) as exc:
        assert_corpus_model(_FakeConn(["bge-m3@ollama"]), "bge-large@ollama")
    assert "bge-m3@ollama" in str(exc.value)


@pytest.mark.db
def test_live_corpus_accepts_current_stamp(live_db):
    """Canlı korpus mevcut config'in damgasını kabul etmeli (aksi halde ingest tuğla)."""
    from ragintel.database.storage_repo import assert_corpus_model
    cfg = load_config(db_reader=None)
    with live_db.connection() as conn:
        assert_corpus_model(conn, model_stamp(cfg.group("embedding").model))


# ---------------------------------------------------------------------------
# PARÇA 2 — tunable taşıma
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("group,field,default", [
    ("storage", "hnsw_m", 16),
    ("storage", "hnsw_ef_construction", 64),
    ("ingestion", "max_retry", 3),
    ("api", "health_timeout", 8.0),
    ("api", "feedback_timeout", 8.0),
    ("quality", "parse_success_target", 0.95),
    ("eval", "ctx_cap", 10),
    ("pii", "year_min", 1900),
    ("pii", "year_max", 2099),
])
def test_moved_tunables_keep_code_defaults(group, field, default):
    """DAVRANIŞ DEĞİŞMEDİ kanıtı: taşınan her tunable'ın kod varsayılanı aynı."""
    cfg = load_config(db_reader=None)
    assert getattr(cfg.group(group), field) == default


def test_new_groups_registered_and_typed():
    for g in ("storage", "api", "eval"):
        assert g in PIPELINE_GROUPS and g in GROUP_MODELS
        assert load_config(db_reader=None).group(g) is not None


def test_seed_has_no_secret_fields_after_new_groups():
    """Yeni gruplar seed allowlist'ine secret sızdırmamalı (export_seed guard'ı)."""
    from ragintel.config.export_seed import assert_no_secret_fields
    assert_no_secret_fields()


def test_env_prefix_longest_match_wins():
    """`eval` ⊂ `eval_gates`: RAGINTEL_EVAL_GATES_* yalnız eval_gates'e gitmeli."""
    cfg = load_config(db_reader=None, environ={
        "RAGINTEL_EVAL_GATES_FAITHFULNESS_MIN": "0.42",
        "RAGINTEL_EVAL_CTX_CAP": "7",
    })
    assert cfg.group("eval_gates").faithfulness_min == 0.42
    assert cfg.group("eval").ctx_cap == 7
    assert cfg.source_of("eval_gates", "faithfulness_min") == "env"


def test_pii_year_range_is_config_driven():
    strict = PiiPolicy(year_min=1990, year_max=2000)
    out, n = mask_pii("Tarih 12.05.1980 kayıtlı", strict)
    assert "12.05.1980" in out and n == 0        # aralık dışı → tarih sayılmaz
    out2, n2 = mask_pii("Tarih 12.05.1995 kayıtlı", strict)
    assert "[TARİH]" in out2 and n2 == 1


def test_pii_broken_pattern_is_skipped_not_crash():
    """Bozuk/eksik-gruplu desen ham istisna atmamalı; maskeleme atlanır."""
    bad = PiiPolicy(date_dmy_pattern="(((", date_iso_pattern=r"(\d{4})")
    out, n = mask_pii("Tarih 12.05.1980", bad)
    assert out == "Tarih 12.05.1980" and n == 0


def test_pii_defaults_still_carry_m3_guards():
    p = PiiPolicy()
    assert mask_pii("Sürüm 14.0.3460.9", p) == ("Sürüm 14.0.3460.9", 0)
    assert mask_pii("Tarih 12.05.1980", p)[1] == 1


def test_health_disabled_tei_does_not_degrade():
    from ragintel.api.runtime import derive_health_status
    ok = {"db": "ok", "ollama": "ok", "tei": "disabled"}
    assert derive_health_status(ok) == "healthy"
    assert derive_health_status({**ok, "tei": "down"}) == "degraded"


def test_create_vector_index_uses_config_params():
    class _C:
        sql = ""
        def execute(self, q):
            _C.sql = q
    from ragintel.database.storage_repo import create_vector_index
    create_vector_index(_C(), m=32, ef_construction=128)
    assert "m = 32" in _C.sql and "ef_construction = 128" in _C.sql


# ---------------------------------------------------------------------------
# PARÇA 3 — altyapı default temizliği
# ---------------------------------------------------------------------------
def test_no_real_infra_in_code_defaults():
    """Kod varsayılanlarında gerçek host/IP/parola OLMAMALI (dünkü push'un dersi)."""
    fields = DbSettings.model_fields
    assert fields["host"].default == ""
    assert fields["user"].default == ""
    assert fields["password"].default == ""
    assert OllamaSettings.model_fields["base_url"].default == ""
    assert LiteLLMSettings.model_fields["api_base"].default == ""
    assert TeiSettings.model_fields["rerank_url"].default == ""


def test_missing_db_host_raises_explicit_error():
    s = DbSettings(host="", user="u", password="p")
    with pytest.raises(MissingBootstrapSetting) as exc:
        s.conninfo()
    assert "RAGINTEL_DB_HOST" in str(exc.value)


def test_missing_ollama_base_url_raises_explicit_error():
    with pytest.raises(MissingBootstrapSetting) as exc:
        OllamaSettings(base_url="").require_base_url()
    assert "RAGINTEL_OLLAMA_BASE_URL" in str(exc.value)


def test_settings_source_has_no_internal_hostnames():
    """settings.py metninde iç host/IP kalmamalı (regresyon kilidi)."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "ragintel" / "config" / "settings.py").read_text(encoding="utf-8")
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", src), "settings.py'de çıplak IP var"
    assert "goldenglobalbank" not in src and "finagotech" not in src

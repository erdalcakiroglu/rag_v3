"""M-5: admin config formunun ŞEMASINI pydantic `GROUP_MODELS`'ten OTOMATİK üretir.

Tasarım kararları (export_seed.py ile aynı ilke: tek doğruluk kaynağı = model):
- **Widget çıkarımı tipten yapılır** — ayrı bir "alan → widget" listesi TUTULMAZ.
  Modele yeni alan eklenince formda kendiliğinden görünür (bkz. test:
  `test_new_pydantic_field_auto_appears_in_ui_schema`). Bu, export-seed testinin
  UI eşidir: şema kod ile ayrışamaz, çünkü şema kodun kendisinden türer.
- **Yardım metni / rozet / sınır modelde yaşar** (`description`, `json_schema_extra`
  `{"danger": ...}`, `ge`/`le`). UI yalnızca çizer; içerik üretmez.
- **Sekme ataması burada** (konu bazlı üst seviye). Atanmamış bir grup KAYBOLMAZ:
  otomatik "Diğer" sekmesine düşer — yeni grup eklendiğinde UI'dan sessizce
  düşmesin diye (fail-visible).
- Şema SALT-OKUNUR bir tanımdır; değerler ayrı uçtan (`/api/admin/config`) gelir.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, get_args, get_origin

from annotated_types import Ge, Gt, Le, Lt
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from .settings import GROUP_MODELS

# Üst sekmeler: konu → gruplar. Sıra UI'daki sekme sırasıdır.
UI_TABS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ingestion", "Veri İşleme",
     ("chunking", "embedding", "ingestion", "injection", "pii", "quality", "storage")),
    ("retrieval", "Arama", ("retrieval",)),
    ("agent", "Agent", ("agent", "prompts")),
    ("eval", "Değerlendirme", ("eval", "eval_gates")),
    ("api", "API", ("api",)),
    ("auth", "Kimlik & Erişim", ("auth",)),
)

# Grup başlıkları (TR). Eksikse grup adı kullanılır — kaybolmaz.
GROUP_LABELS: dict[str, str] = {
    "chunking": "Chunk'lama",
    "embedding": "Embedding",
    "ingestion": "Yükleme & İşleme",
    "injection": "Prompt-Injection Taraması",
    "pii": "Kişisel Veri Maskeleme",
    "quality": "Kalite Skorlama",
    "storage": "Vektör Index (HNSW)",
    "retrieval": "Arama & Bağlam",
    "agent": "Agent",
    "prompts": "Sistem Prompt'ları",
    "eval": "Eval Koşumu",
    "eval_gates": "Eval Eşikleri (CI)",
    "api": "API Zaman Aşımları",
    "auth": "Self-Kayıt & Giriş",
}

_OTHER_TAB = ("other", "Diğer")


def _numeric_bounds(field: FieldInfo) -> tuple[float | None, float | None]:
    """pydantic `Field(ge=…, le=…)` sınırlarını çıkarır (istemciye de yansır)."""
    minimum = maximum = None
    for meta in field.metadata:
        if isinstance(meta, Ge):
            minimum = meta.ge
        elif isinstance(meta, Gt):
            minimum = meta.gt
        elif isinstance(meta, Le):
            maximum = meta.le
        elif isinstance(meta, Lt):
            maximum = meta.lt
    return minimum, maximum


def _extra(field: FieldInfo) -> dict[str, Any]:
    extra = field.json_schema_extra
    return extra if isinstance(extra, dict) else {}


def _default_of(field: FieldInfo) -> Any:
    """Kod varsayılanı — UI'daki 'varsayılandan farklı' rozeti ve 'varsayılana dön'
    düğmesi bunu referans alır (default_factory dahil)."""
    if field.default_factory is not None:
        return field.default_factory()
    return field.default


def _widget_of(annotation: Any, field: FieldInfo) -> tuple[str, list[str] | None]:
    """Tip → widget. Sıra ÖNEMLİ: bool, int'ten önce gelmeli (bool ⊂ int)."""
    if get_origin(annotation) is Literal:
        return "select", [str(v) for v in get_args(annotation)]
    if annotation is bool:
        return "toggle", None
    if annotation is int:
        return "number", None
    if annotation is float:
        return "number", None
    if annotation is str:
        return ("textarea" if _extra(field).get("multiline") else "text"), None
    if annotation == list[str]:
        return "string_list", None
    if annotation == dict[str, str]:
        return "key_text", None
    # Bilinmeyen tip: sessizce düşürmek yerine ham JSON alanı olarak GÖSTER.
    return "json", None


def _field_schema(name: str, field: FieldInfo, path: list[str]) -> dict[str, Any]:
    annotation = field.annotation
    extra = _extra(field)

    # İç-içe model → alt-bölüm (quality.parse/clean/…).
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return {
            "name": name,
            "path": path,
            "widget": "section",
            "description": field.description,
            "fields": _fields_of(annotation, path),
        }

    widget, options = _widget_of(annotation, field)
    minimum, maximum = _numeric_bounds(field)
    schema: dict[str, Any] = {
        "name": name,
        "path": path,
        "widget": widget,
        "description": field.description,
        "default": _default_of(field),
    }
    if options is not None:
        schema["options"] = options
    # Dinamik seçenek: kardeş bir dict alanın anahtarları (prompts.agent_system_active).
    if "options_from" in extra:
        schema["widget"] = "select"
        schema["options_from"] = extra["options_from"]
    if minimum is not None:
        schema["min"] = minimum
    if maximum is not None:
        schema["max"] = maximum
    if widget == "number":
        schema["step"] = 1 if annotation is int else "any"
    if "danger" in extra:
        schema["danger"] = extra["danger"]
    return schema


def _fields_of(model: type[BaseModel], prefix: list[str]) -> list[dict[str, Any]]:
    return [
        _field_schema(name, field, [*prefix, name])
        for name, field in model.model_fields.items()
    ]


class UnknownField(KeyError):
    """İstemciden gelen alan yolu modelde tanımlı değil (uydurma anahtar DB'ye yazılmaz)."""


def resolve_field(model: type[BaseModel], path: list[str]) -> FieldInfo:
    """`path` (ör. ['weights','parse']) modelde GERÇEKTEN var mı? Yoksa `UnknownField`.

    Alan-bazlı yazımın kapısı: istemci uydurma bir yol gönderirse `jsonb_set` onu
    sessizce YARATIR (create_missing) — model bilmediği için doğrulama da yakalamaz
    ve DB'de hayalet anahtar kalır. O yüzden yol, yazmadan önce modele sorulur.
    """
    if not path:
        raise UnknownField("Alan yolu boş")
    current: type[BaseModel] = model
    for idx, part in enumerate(path):
        field = current.model_fields.get(part)
        if field is None:
            raise UnknownField(".".join(path[: idx + 1]))
        annotation = field.annotation
        is_model = isinstance(annotation, type) and issubclass(annotation, BaseModel)
        if idx == len(path) - 1:
            return field
        if not is_model:
            raise UnknownField(".".join(path[: idx + 1]))
        current = annotation
    raise UnknownField(".".join(path))  # pragma: no cover — döngü hep döner/atar


def build_ui_schema(group_models: Mapping[str, type[BaseModel]] = GROUP_MODELS) -> dict[str, Any]:
    """Admin formunun tam şeması: sekmeler + grup başına alan ağacı."""
    groups = {
        group: {
            "group": group,
            "label": GROUP_LABELS.get(group, group),
            "model": model.__name__,
            "doc": (model.__doc__ or "").strip() or None,
            "fields": _fields_of(model, []),
        }
        for group, model in group_models.items()
    }

    tabs: list[dict[str, Any]] = []
    for tab_id, label, tab_groups in UI_TABS:
        present = [g for g in tab_groups if g in groups]
        if present:
            tabs.append({"id": tab_id, "label": label, "groups": present})

    # Hiçbir sekmeye atanmamış grup → "Diğer" (yeni grup UI'dan sessizce düşmesin).
    assigned = {g for tab in tabs for g in tab["groups"]}
    orphans = [g for g in groups if g not in assigned]
    if orphans:
        tabs.append({"id": _OTHER_TAB[0], "label": _OTHER_TAB[1], "groups": orphans})

    return {"tabs": tabs, "groups": groups}

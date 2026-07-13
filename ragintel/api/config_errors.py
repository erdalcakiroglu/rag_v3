"""M-5: pydantic doğrulama hatalarını ALANA İLİŞTİRİLMİŞ Türkçe mesajlara çevirir.

pydantic'in kendi mesajları İngilizcedir ("Input should be less than or equal to
100"). Admin panelinde hata, hatayı yapan alanın ALTINDA ve Türkçe görünmeli —
yoksa kullanıcı hangi kutuyu düzelteceğini bilemez.

Kural: yalnızca YERLEŞİK kısıt hataları (ge/le/tip/literal) burada çevrilir.
Modeldeki `raise ValueError(...)` mesajları ZATEN Türkçedir (tek doğruluk kaynağı
modeldir) — onlar olduğu gibi geçer, burada kopyalanmaz.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError


def _fmt(value: Any) -> str:
    return str(value)


def _message(err: dict[str, Any]) -> str:
    kind = err.get("type", "")
    ctx = err.get("ctx") or {}

    if kind in ("greater_than_equal", "greater_than"):
        limit = ctx.get("ge", ctx.get("gt"))
        rel = "en az" if kind == "greater_than_equal" else "büyük"
        return f"Değer {rel} {_fmt(limit)} olmalı."
    if kind in ("less_than_equal", "less_than"):
        limit = ctx.get("le", ctx.get("lt"))
        rel = "en fazla" if kind == "less_than_equal" else "küçük"
        return f"Değer {rel} {_fmt(limit)} olmalı."
    if kind == "literal_error":
        expected = ctx.get("expected", "")
        return f"Geçersiz seçim. İzin verilen değerler: {expected}."
    if kind in ("int_parsing", "int_type", "int_from_float"):
        return "Tam sayı olmalı."
    if kind in ("float_parsing", "float_type"):
        return "Sayı olmalı."
    if kind in ("bool_parsing", "bool_type"):
        return "Doğru/yanlış (açık/kapalı) olmalı."
    if kind in ("string_type", "string_pattern_mismatch"):
        return "Metin olmalı."
    if kind in ("list_type", "dict_type"):
        return "Beklenen yapıya uymuyor (liste/nesne)."
    if kind == "missing":
        return "Bu alan zorunlu."
    if kind == "extra_forbidden":
        return "Bilinmeyen alan — modelde tanımlı değil."
    # value_error / model tarafı: mesaj zaten Türkçe (modelde yazıldı).
    return err.get("msg", "Geçersiz değer.")


def safe_details(exc: ValidationError) -> list[dict[str, Any]]:
    """Ham pydantic hataları — JSON'a ÇEVRİLEBİLİR hâlde.

    `exc.errors()` varsayılanı, `value_error` girdilerinde `ctx["error"]` altında ham
    `ValueError` NESNESİ taşır; bu doğrudan JSONResponse'a verilirse uç 500 döner
    (model_validator eklenene kadar görünmeyen bir tuzak). `include_context=False`
    o nesneyi dışarıda bırakır; insan-okur mesaj zaten `field_errors`'ta.
    """
    return exc.errors(include_url=False, include_context=False)


def field_errors(exc: ValidationError) -> list[dict[str, Any]]:
    """[{field: 'weights.parse', path: [...], message: 'TR ...'}] — UI alana iliştirir.

    `loc` boşsa (model-seviyesi çapraz-alan hatası) `field` boş string olur; UI onu
    grubun başında gösterir.
    """
    out: list[dict[str, Any]] = []
    for err in exc.errors():
        loc = [str(part) for part in err.get("loc", ())]
        out.append({
            "field": ".".join(loc),
            "path": loc,
            "message": _message(err),
        })
    return out

"""Öncelik zinciri çözümleyicisi (saf/pure — DB'siz test edilebilir).

Katmanlar EN DÜŞÜKTEN EN YÜKSEĞE sıralanır; İP-0 zinciri:
    default (en düşük)  <  env  <  db (en yüksek)

Her yaprak (leaf) değer için hangi katmandan geldiği ayrıca kaydedilir.
Bu modül tip bilmez; birleştirme dict düzeyinde yapılır, tip doğrulaması
`loader` katmanında pydantic modelleriyle yapılır.
"""

from __future__ import annotations

from typing import Any


def _is_mapping(v: Any) -> bool:
    return isinstance(v, dict)


def merge_with_sources(
    layers: list[tuple[str, dict]],
) -> tuple[dict, dict]:
    """Katmanları birleştirir ve yaprak-başına kaynak haritası üretir.

    Args:
        layers: (kaynak_adı, değerler) çiftleri, EN DÜŞÜK öncelikten EN YÜKSEĞE.

    Returns:
        (merged, sources) — `merged` birleştirilmiş değerler; `sources` aynı
        yapıda ama her yaprakta o değerin geldiği kaynak adı.
    """
    merged: dict = {}
    sources: dict = {}

    for source_name, layer in layers:
        if not layer:
            continue
        _overlay(merged, sources, layer, source_name)

    return merged, sources


def _overlay(merged: dict, sources: dict, layer: dict, source_name: str) -> None:
    for key, value in layer.items():
        if _is_mapping(value):
            child = merged.get(key)
            if not _is_mapping(child):
                child = {}
                merged[key] = child
            child_src = sources.get(key)
            if not _is_mapping(child_src):
                child_src = {}
                sources[key] = child_src
            _overlay(child, child_src, value, source_name)
        else:
            merged[key] = value
            sources[key] = source_name

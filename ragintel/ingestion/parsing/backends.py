"""pdf/docx için parse backend seçimi (config-first).

'auto' = docling import edilebiliyorsa docling, aksi halde fallback.
xlsx/txt backend'e gitmez (office_backend ile işlenir).
"""

from __future__ import annotations

import importlib.util


def _docling_available() -> bool:
    return importlib.util.find_spec("docling") is not None


def get_backend(name: str = "auto"):
    """İsimden backend nesnesi döndürür. name: auto | docling | fallback."""
    if name == "fallback":
        from .fallback_backend import FallbackBackend
        return FallbackBackend()
    if name == "docling":
        from .docling_backend import DoclingBackend
        return DoclingBackend()
    if name == "auto":
        if _docling_available():
            from .docling_backend import DoclingBackend
            return DoclingBackend()
        from .fallback_backend import FallbackBackend
        return FallbackBackend()
    raise ValueError(f"Bilinmeyen parse backend: {name!r}")

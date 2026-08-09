"""pdf/docx için parse backend seçimi (config-first).

'auto' = docling import edilebiliyorsa docling, aksi halde fallback.
xlsx/txt backend'e gitmez (office_backend ile işlenir).
"""

from __future__ import annotations

import importlib.util


def _docling_available() -> bool:
    return importlib.util.find_spec("docling") is not None


def get_backend(name: str = "auto", *, figure_images: bool = True,
                figure_image_scale: float = 2.0, pdf_backend: str = "pypdfium2",
                tableformer_mode: str = "accurate", parse_num_threads: int = 4):
    """İsimden backend nesnesi döndürür. name: auto | docling | fallback.

    M-7: görsel çıkarma ayarları (config-first) docling backend'ine geçer.
    Fallback backend görsel üretmez — bu ayarlar onu ilgilendirmez.
    pdf_backend: docling içi PDF alt-parser (config: parse.pdf_backend).
    İP-2: tableformer_mode/parse_num_threads parse hızını ayarlar (docling backend).
    """
    def _docling():
        from .docling_backend import DoclingBackend
        return DoclingBackend(figure_images=figure_images,
                              figure_image_scale=figure_image_scale, pdf_backend=pdf_backend,
                              tableformer_mode=tableformer_mode,
                              parse_num_threads=parse_num_threads)

    if name == "fallback":
        from .fallback_backend import FallbackBackend
        return FallbackBackend()
    if name == "docling":
        return _docling()
    if name == "auto":
        if _docling_available():
            return _docling()
        from .fallback_backend import FallbackBackend
        return FallbackBackend()
    raise ValueError(f"Bilinmeyen parse backend: {name!r}")

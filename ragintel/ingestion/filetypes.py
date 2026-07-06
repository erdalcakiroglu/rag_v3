"""İçerik-tabanlı dosya tipi tespiti (İP-1).

Kural: tip UZANTIYLA DEĞİL içerikle belirlenir (python-magic / libmagic).
docx ve xlsx her ikisi de OOXML (zip) olduğundan, magic bare-zip döndürürse
zip üyelerine bakılarak ayrıştırılır — bu da içerik tabanlıdır.

Desteklenen tipler şema CHECK'i ile aynı: pdf/docx/xlsx/txt.
"""

from __future__ import annotations

import zipfile

import magic

# Şema CHECK (core_files.file_type) ile birebir.
SUPPORTED_TYPES = ("pdf", "docx", "xlsx", "txt")

# libmagic MIME -> şema tipi (net eşleşmeler).
_MIME_MAP = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/msword": "docx",       # eski/yanlış imzalı OOXML için tolerans
    "application/vnd.ms-excel": "xlsx",
}

# Zip (OOXML) içeriğinden ayrım için imza dosyaları.
_OOXML_MARKERS = (
    ("word/", "docx"),
    ("xl/", "xlsx"),
    ("ppt/", None),   # pptx desteklenmiyor -> None (reddedilir)
)

# MIME tespiti için okunan baş bayt sayısı. İmzalar (PDF %PDF, OOXML/zip PK,
# metin) ilk KB'lerdedir; 8KB fazlasıyla yeter.
_MAGIC_BUFFER_BYTES = 8192


def detect_with_mime(path: str) -> tuple[str | None, str]:
    """(şema_tipi, ham_mime) döndürür. Tip desteklenmiyorsa şema_tipi None.

    Ham MIME metrik `detail`'ine (tip tespiti kanıtı) yazılır.
    """
    # BUG düzeltmesi (İP-1): `magic.from_file(path)` Windows'ta libmagic'in
    # Türkçe/Unicode adlı dosyaları açamamasına yol açıyordu ("cannot open" →
    # yanlışlıkla reddet). Dosyayı Python'da (Unicode-güvenli) binary açıp ilk
    # 8KB'yi buffer olarak veriyoruz. Zip alt-tip ayrımı (_refine_zip) zaten
    # Python zipfile ile path'i güvenli açar, o dokunulmadan kalır.
    with open(path, "rb") as fh:
        head = fh.read(_MAGIC_BUFFER_BYTES)
    mime = magic.from_buffer(head, mime=True)

    mapped = _MIME_MAP.get(mime)
    if mapped is not None:
        return mapped, mime

    # OOXML bazen 'application/zip' olarak görünür -> zip içeriğine bak.
    if mime in ("application/zip", "application/octet-stream"):
        return _refine_zip(path), mime

    # Düz metin (text/plain, text/*, application/json ... hepsi txt kabul).
    if mime.startswith("text/") or mime in ("application/json", "application/xml"):
        return "txt", mime

    return None, mime


def detect_type(path: str) -> str | None:
    """Dosyanın şema-uyumlu tipini döndürür; desteklenmiyorsa None.

    None dönerse dosya reddedilir (core_files'a yazılmaz) — file_type CHECK'i
    yalnızca pdf/docx/xlsx/txt kabul eder.
    """
    return detect_with_mime(path)[0]


def _refine_zip(path: str) -> str | None:
    """Zip tabanlı dosyanın OOXML alt tipini üyelerinden çıkarır."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return None

    has_word = any(n.startswith("word/") for n in names)
    has_xl = any(n.startswith("xl/") for n in names)
    if has_word and not has_xl:
        return "docx"
    if has_xl and not has_word:
        return "xlsx"
    return None

"""Checksum + raw dosya deposu (İP-1).

- `sha256_file` / `file_size`: akışlı (streaming) hesaplama; büyük dosyada bellek
  patlamaz.
- `copy_to_storage`: raw dosyayı depoya kopyalar; ORİJİNAL klasör asla
  değiştirilmez (yalnızca okuma). Depo yolu checksum tabanlıdır (dedup dostu).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil

_CHUNK = 1024 * 1024  # 1 MiB
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def sha256_file(path: str) -> str:
    """Dosyanın sha256 hex özetini akışlı hesaplar."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def file_size(path: str) -> int:
    return os.path.getsize(path)


def _safe_filename(name: str) -> str:
    base = os.path.basename(name)
    return _SAFE_NAME.sub("_", base) or "file"


def storage_path_for(root: str, checksum: str, file_name: str) -> str:
    """Depodaki hedef yolu üretir: <root>/raw/<cs[:2]>/<cs>/<safe_name>."""
    return os.path.join(
        root, "raw", checksum[:2], checksum, _safe_filename(file_name)
    )


def copy_to_storage(src_path: str, root: str, checksum: str, file_name: str) -> str:
    """Raw dosyayı depoya kopyalar, hedef mutlak yolu döndürür (idempotent)."""
    dest = storage_path_for(root, checksum, file_name)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    # Kopya yoksa kopyala; varsa (idempotent yeniden tarama) dokunma.
    if not os.path.exists(dest):
        shutil.copy2(src_path, dest)
    return os.path.abspath(dest)
